"""Offline regression for per-paper context ownership and handoff."""
import contextlib
import json
import queue
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import codex_models
import codex_bridge
import paper_ai
RealCodexSession=paper_ai.CodexSession

PID='0123456789abcdef'


def resolve(model='',reasoning_effort=''):
    if model and model not in ('model-a','model-b'):
        raise ValueError('unavailable model')
    return {'model':model or 'model-a','reasoning_effort':reasoning_effort or 'medium'}


class FakeSession:
    next_id=0
    calls=[]
    def __init__(self,*_,web_search=False):
        self.web_search=web_search
        self.opened_web_urls=set()
        self.temp=type('Temp',(),{'name':str(ROOT/'work')})()
    def __enter__(self): return self
    def __exit__(self,*_): pass
    def rpc(self,method,params):
        self.calls.append((method,params))
        if method=='thread/start':
            FakeSession.next_id+=1
            return {'thread':{'id':f'00000000-0000-0000-0000-{FakeSession.next_id:012d}'}}
        return {}
    def turn(self,thread_id,prompt,config,schema,**_):
        self.calls.append(('turn',{'thread_id':thread_id,'prompt':prompt,'config':config,'images':_.get('image_paths',[]),'images_exist':all(Path(p).is_file() for p in _.get('image_paths',[]))}))
        if 'answer' in schema['properties']:
            return {'answer':'实验证据见所引段落。','citations':['pdf-page-1']}
        return {key:f'译文 {key}' for key in schema['required']}


class PaperAITests(unittest.TestCase):
    def setUp(self):
        work=ROOT/'work';work.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(prefix='paper-ai-',dir=work)
        self.root=Path(self.temp.name)
        self.library=self.root/'library';(self.library/PID).mkdir(parents=True)
        from test_layout_rules import synthetic_pdf
        synthetic_pdf(self.library/PID/'original.pdf',600,[(40,720,'Full original PDF material parameter E = 42 MPa')])
        self.path=self.root/'library.sqlite3'
        with self.db() as con:
            con.execute('CREATE TABLE papers(id TEXT PRIMARY KEY)')
            con.execute('INSERT INTO papers VALUES(?)',(PID,))
            paper_ai.init(con)
        self.doc={'id':PID,'title':'Test paper','notes':'Measure force carefully.',
                  'blocks':[{'id':'p1-b1','page':1,'text':'We measured contact forces at 10 Hz.'}],
                  'translation':{'status':'idle'}}
        self.model_patch=patch.object(codex_models,'resolve_config',side_effect=resolve)
        self.session_patch=patch.object(paper_ai,'CodexSession',FakeSession)
        self.model_patch.start();self.session_patch.start()
        FakeSession.calls=[];FakeSession.next_id=0

    def ask(self, question, **kwargs):
        return paper_ai.ask(self.db,PID,self.doc,question,library=self.library,**kwargs)

    def test_pdf_is_default_even_when_extracted_blocks_are_missing(self):
        self.doc['blocks']=[]
        result=self.ask('材料参数根据什么设置？')
        call=[v for method,v in FakeSession.calls if method=='turn'][-1]
        self.assertIn('E = 42 MPa',call['prompt'])
        self.assertEqual(len(call['images']),1)
        self.assertTrue(call['images_exist'])
        self.assertTrue(Path(call['images'][0]).exists(), 'Prepared pages persist for reuse')
        self.assertEqual(result['citations'][0]['type'],'pdf')

    def test_followup_reuses_context_without_pdf_or_history_duplication(self):
        self.ask('First question')
        with patch('ezread.chat_pdf.prepare_pdf', side_effect=AssertionError('must not render again')):
            self.ask('Follow-up question')
        turns=[v for method,v in FakeSession.calls if method=='turn']
        self.assertEqual(turns[-1]['images'],[])
        self.assertNotIn('pdf_pages',turns[-1]['prompt'])
        self.assertNotIn('recent_local_history',turns[-1]['prompt'])
        self.assertEqual(turns[0]['thread_id'],turns[1]['thread_id'])

    def test_old_conversation_continues_its_own_thread_and_model(self):
        first=self.ask('First question')
        with self.db() as con:
            paper_ai.new_chat(con,PID)
            con.execute("UPDATE paper_ai_generations SET model='model-b' WHERE generation=2")
        second=self.ask('New conversation',generation=2)
        continued=self.ask('Continue old conversation',generation=1)
        self.assertEqual(continued['thread_id'],first['thread_id'])
        self.assertNotEqual(continued['thread_id'],second['thread_id'])
        turn=[v for method,v in FakeSession.calls if method=='turn'][-1]
        self.assertEqual(turn['config']['model'],'model-a')
        self.assertEqual(turn['images'],[])
        with self.db() as con:
            self.assertEqual(paper_ai.current(con,PID)['generation'],2)
            self.assertEqual(len(paper_ai.history(con,PID,1)['messages']),4)
            self.assertEqual(len(paper_ai.history(con,PID,2)['messages']),2)

    def test_open_empty_thread_is_idempotent_and_does_not_send_a_turn(self):
        tid=paper_ai.prepare_chat_thread(self.db,PID,1)
        self.assertEqual(paper_ai.prepare_chat_thread(self.db,PID,1),tid)
        self.assertEqual([method for method,_ in FakeSession.calls],['thread/start'])
        with self.db() as con:
            self.assertEqual(paper_ai.history(con,PID)['messages'],[])
        self.assertEqual(self.ask('First actual question')['thread_id'],tid)
        self.assertEqual(len([v for m,v in FakeSession.calls if m=='turn'][-1]['images']),1)

    def test_fork_inherits_pdf_receipt_and_missing_thread_resends(self):
        first=self.ask('First question')
        original=FakeSession.rpc
        def busy(session,method,params):
            if method=='thread/resume':
                raise paper_ai.SessionError('busy','thread_busy')
            if method=='thread/fork':
                return {'thread':{'id':'forked-thread'}}
            return original(session,method,params)
        with patch.object(FakeSession,'rpc',busy):
            second=self.ask('Follow-up')
        self.assertNotEqual(first['thread_id'],second['thread_id'])
        self.assertEqual([v for m,v in FakeSession.calls if m=='turn'][-1]['images'],[])
        def missing(session,method,params):
            if method=='thread/resume':
                raise paper_ai.SessionError('missing','missing_thread')
            return original(session,method,params)
        with patch.object(FakeSession,'rpc',missing):
            self.ask('Recover lost session')
        self.assertEqual(len([v for m,v in FakeSession.calls if m=='turn'][-1]['images']),1)

    def test_failed_first_turn_does_not_mark_pdf_as_ready(self):
        with patch.object(FakeSession,'turn',side_effect=paper_ai.SessionError('offline')):
            with self.assertRaises(paper_ai.SessionError):
                self.ask('First question')
        self.ask('Retry')
        turn=[v for method,v in FakeSession.calls if method=='turn'][-1]
        self.assertEqual(len(turn['images']),1)

    def test_changed_pdf_is_attached_again(self):
        self.ask('First question')
        from test_layout_rules import synthetic_pdf
        synthetic_pdf(self.library/PID/'original.pdf',600,[(40,720,'Updated material E = 55 MPa')])
        self.ask('Follow-up question')
        turn=[v for method,v in FakeSession.calls if method=='turn'][-1]
        self.assertIn('55 MPa',turn['prompt'])
        self.assertEqual(len(turn['images']),1)

    def test_retries_are_bounded_and_explain_failure(self):
        session=RealCodexSession.__new__(RealCodexSession)
        session.web_search=True
        session.deadline=time.monotonic()+1
        session.events=queue.Queue()
        session.rpc=lambda *_: {'turn':{'id':'t'}}
        for _ in range(4):
            session.events.put({'method':'error','params':{'threadId':'thread','turnId':'t',
                'willRetry':True,'error':{'message':'connection timed out'}}})
        events=[]
        with self.assertRaisesRegex(paper_ai.SessionError,'多次重试'):
            session.turn('thread','q',resolve(),paper_ai.ANSWER_SCHEMA,on_progress=events.append)
        retries=[e for e in events if e.get('stage')=='reconnecting']
        self.assertEqual(len(retries),3)
        self.assertIn('第 3 次',retries[-1]['label'])

    def test_missing_pdf_never_falls_back_to_selected_blocks(self):
        (self.library/PID/'original.pdf').unlink()
        with self.assertRaisesRegex(ValueError,'原始 PDF'):
            self.ask('What material?')
        self.assertFalse(any(method=='turn' for method,_ in FakeSession.calls))

    def tearDown(self):
        self.session_patch.stop();self.model_patch.stop();self.temp.cleanup()

    @contextlib.contextmanager
    def db(self):
        con=sqlite3.connect(self.path)
        try:
            with con: yield con
        finally: con.close()

    def test_chat_enables_search_on_start_and_resume_but_translation_does_not(self):
        self.ask('Explain')
        self.ask('Continue')
        for method,params in FakeSession.calls:
            if method in ('thread/start','thread/resume'):
                self.assertEqual(params['config']['web_search'],'live')
                self.assertEqual(params['baseInstructions'],paper_ai.CHAT_BASE)
        FakeSession.calls=[]
        paper_ai.translate_batch(self.db,PID,self.doc,self.doc['blocks'],resolve(),None)
        self.assertEqual(FakeSession.calls[0][1]['config']['web_search'],'disabled')

    def test_web_sources_must_be_opened_in_current_turn_and_survive_history(self):
        def answer(session,*args,**kwargs):
            session.opened_web_urls={'https://example.org/paper'}
            return {'answer':'外部研究','citations':[],
                    'sources':[{'title':'Research','url':'https://example.org/paper'}]}
        with patch.object(FakeSession,'turn',answer):
            result=self.ask('Search')
        self.assertEqual(result['citations'][0]['type'],'web')
        with self.db() as con:
            self.assertEqual(paper_ai.history(con,PID)['messages'][-1]['citations'],result['citations'])
        with patch.object(FakeSession,'turn',return_value={'answer':'Unverified','citations':[],
                'sources':[{'title':'Unopened','url':'https://example.org/missing'}]}):
            result=self.ask('Search again')
            self.assertIn('Unverified',result['answer'])
            self.assertIn('待核验',result['answer'])
            self.assertFalse(result['citations'][0]['open_recorded'])
        with self.db() as con:
            self.assertEqual(len(paper_ai.history(con,PID)['messages']),4)

    def test_invalid_web_source_preserves_completed_answer_without_unsafe_link(self):
        with patch.object(FakeSession,'turn',return_value={'answer':'Completed answer','citations':[],
                'sources':[{'title':'bad','url':'javascript:alert(1)'},None]}):
            result=self.ask('Question')
        self.assertIn('Completed answer',result['answer'])
        self.assertIn('待核验',result['answer'])
        self.assertEqual(result['citations'],[])
        with self.db() as con:
            self.assertEqual(paper_ai.history(con,PID)['messages'][-1]['text'],result['answer'])

    def test_no_search_is_valid_and_no_forced_failure_message(self):
        result=self.ask('Explain a concept')
        self.assertNotIn('未找到',result['answer'])

    def test_search_events_track_only_current_turn_opened_pages(self):
        session=RealCodexSession.__new__(RealCodexSession)
        session.deadline=time.monotonic()+1
        session.events=queue.Queue()
        session.rpc=lambda *_: {'turn':{'id':'turn-a'}}
        for turn_id,action in [('wrong',{'type':'openPage','url':'https://example.org/wrong'}),
                               ('turn-a',{'type':'search','query':'topic'}),
                               ('turn-a',{'type':'openPage','url':'https://example.org/paper'})]:
            session.events.put({'method':'item/completed','params':{'threadId':'thread-a','turnId':turn_id,
                'item':{'type':'webSearch','action':action}}})
        session.events.put({'method':'turn/completed','params':{'threadId':'thread-a',
            'turn':{'id':'turn-a','status':'completed','items':[{'type':'agentMessage',
                'text':'{"answer":"Answer","citations":[],"sources":[]}'},
                {'type':'webSearch','action':{'type':'openPage','url':'https://example.org/final'}}]}}})
        events=[]
        session.turn('thread-a','q',resolve(),paper_ai.CHAT_SCHEMA,on_progress=events.append)
        self.assertEqual(session.opened_web_urls,{'https://example.org/paper','https://example.org/final'})
        self.assertTrue(any(e.get('stage')=='searching' for e in events))

    def test_busy_thread_forks_context_without_new_empty_thread(self):
        calls=[]
        class BusySession(FakeSession):
            def rpc(self,method,params):
                calls.append((method,params))
                if method=='thread/resume':
                    raise paper_ai.SessionError('busy','thread_busy')
                if method=='thread/fork':
                    return {'thread':{'id':'forked-thread'}}
                raise AssertionError('unexpected method')
        session=BusySession(web_search=True)
        tid=paper_ai._start_or_resume(session,'original-thread',resolve(),paper_ai.CHAT_BASE,'{}')
        self.assertEqual(tid,'forked-thread')
        self.assertEqual([c[0] for c in calls],['thread/resume','thread/fork'])
        self.assertEqual(calls[-1][1]['threadId'],'original-thread')
        self.assertEqual(calls[-1][1]['config']['web_search'],'live')

    def test_active_writer_rpc_error_has_specific_code(self):
        session=RealCodexSession.__new__(RealCodexSession)
        session.number=0
        session.deadline=time.monotonic()+1
        session.send=lambda _: None
        session.responses=queue.Queue()
        session.responses.put({'id':1,'error':{'message':'thread abc already has an active writer'}})
        with self.assertRaises(paper_ai.SessionError) as caught:
            session.rpc('thread/resume',{})
        self.assertEqual(caught.exception.code,'thread_busy')
        self.assertIn('占用',str(caught.exception))

    def test_unmaterialized_thread_is_recreated_without_a_model_turn(self):
        session=RealCodexSession.__new__(RealCodexSession)
        session.number=0
        session.deadline=time.monotonic()+1
        session.web_search=True
        session.temp=type('Temp',(),{'name':str(self.root)})()
        calls=[]
        session.send=calls.append
        session.responses=queue.Queue()
        session.responses.put({'id':1,'error':{'message':'no rollout found for thread id empty-thread'}})
        session.responses.put({'id':2,'result':{'thread':{'id':'replacement-thread'}}})
        thread=paper_ai._start_or_resume(session,'empty-thread',resolve(),paper_ai.CHAT_BASE,'{}')
        self.assertEqual(thread,'replacement-thread')
        self.assertEqual([c['method'] for c in calls],['thread/resume','thread/start'])
        self.assertEqual(calls[1]['params']['config']['web_search'],'live')

    def test_missing_model_is_not_misidentified_as_a_missing_thread(self):
        session=RealCodexSession.__new__(RealCodexSession)
        session.number=0
        session.deadline=time.monotonic()+1
        session.send=lambda _:None
        session.responses=queue.Queue()
        session.responses.put({'id':1,'error':{'message':'model not found'}})
        with self.assertRaises(paper_ai.SessionError) as caught:
            session.rpc('thread/resume',{})
        self.assertNotEqual(caught.exception.code,'missing_thread')

    def test_selection_attachment_is_prompt_context_and_saved_separately(self):
        text='Selected full text with a newline\n第二行'
        self.ask('Explain this',selected_text=text)
        prompt=[v['prompt'] for method,v in FakeSession.calls if method=='turn'][-1]
        self.assertIn(json.dumps(text,ensure_ascii=False),prompt)
        with self.db() as con:
            message=paper_ai.history(con,PID)['messages'][0]
            self.assertEqual(message['text'],'Explain this')
            self.assertEqual(message['citations'],[{'type':'selection','text':text}])
        with self.assertRaises(ValueError):
            self.ask('Explain',selected_text='x'*20001)

    def test_ask_reuses_thread_and_preserves_history_across_model_switch(self):
        first=self.ask('What was measured?')
        second=self.ask('At what rate?')
        self.assertEqual(first['thread_id'],second['thread_id'])
        self.assertEqual(len([c for c in FakeSession.calls if c[0]=='thread/start']),1)
        with self.db() as con:
            old=paper_ai.history(con,PID)
            self.assertEqual(len(old['messages']),4)
            result=paper_ai.switch_model(con,PID,{'model':'model-b'},self.doc,self.library)
            self.assertEqual(result['generation'],2)
        handoff=json.loads((self.library/PID/'ai-handoff-2.json').read_text(encoding='utf-8'))
        self.assertEqual(handoff['user_notes'],'Measure force carefully.')
        self.assertEqual(handoff['verified_in_paper'][0]['citations'][0]['block_id'],'pdf-page-1')
        third=self.ask('Does it report contact force?')
        self.assertNotEqual(third['thread_id'],first['thread_id'])
        with self.db() as con:
            self.assertEqual(len(paper_ai.history(con,PID,1)['messages']),4)
            self.assertEqual(len(paper_ai.history(con,PID)['messages']),2)

    def test_translation_thread_is_separate_and_selection_is_stateless(self):
        config={'model':'model-a','reasoning_effort':'medium'}
        saved=[]
        tid,result=paper_ai.translate_batch(self.db,PID,self.doc,self.doc['blocks'],config,None,
                                              on_thread=saved.append)
        self.assertEqual(saved,[tid])
        tid2,_=paper_ai.translate_batch(self.db,PID,self.doc,self.doc['blocks'],config,tid)
        self.assertEqual(tid,tid2)
        self.assertEqual(result,{'p1-b1':'译文 p1-b1'})
        with self.db() as con:
            self.assertFalse(paper_ai.history(con,PID)['messages'])
        with patch('selection_codex.translate',return_value='接触力'):
            self.assertEqual(codex_bridge.translate_selection('contact force',**config),'接触力')

    def test_invalid_citation_is_not_saved(self):
        original=FakeSession.turn
        def bad(self,thread_id,prompt,config,schema,**kwargs):
            return {'answer':'不实引用','citations':['another-paper']}
        FakeSession.turn=bad
        try:
            with self.assertRaises(paper_ai.SessionError):
                self.ask('What was measured?')
        finally:
            FakeSession.turn=original
        with self.db() as con:
            self.assertEqual(paper_ai.history(con,PID)['messages'],[])

    def test_app_server_completed_turn_payload_is_parsed(self):
        session=RealCodexSession.__new__(RealCodexSession)
        session.deadline=time.monotonic()+1
        session.events=queue.Queue()
        session.rpc=lambda *_: {}
        session.events.put({'method':'turn/completed','params':{'threadId':'test-thread',
            'turn':{'status':'completed','items':[{'type':'agentMessage',
                'text':'{"answer":"结论","citations":["p1-b1"]}'}]}}})
        answer=RealCodexSession.turn(session,'test-thread','question',
            {'model':'model-a','reasoning_effort':'medium'},paper_ai.ANSWER_SCHEMA)
        self.assertEqual(answer['citations'],['p1-b1'])

    def test_stream_uses_only_matching_turn_and_readable_reasoning_summary(self):
        session=RealCodexSession.__new__(RealCodexSession)
        session.deadline=time.monotonic()+1
        session.events=queue.Queue()
        session.rpc=lambda *_: {'turn':{'id':'turn-a'}}
        raw=json.dumps({'answer':'**结论**\n接触力为 10 N。','citations':['p1-b1']},ensure_ascii=False)
        def put(method, **params):
            session.events.put({'method':method,'params':{'threadId':'thread-a','turnId':'turn-a',**params}})
        put('item/reasoning/summaryTextDelta',itemId='reason',summaryIndex=0,delta='检查实验段落。')
        put('item/reasoning/textDelta',itemId='reason',delta='raw reasoning must not be exposed')
        put('item/reasoning/summaryTextDelta',turnId='another-turn',delta='wrong turn')
        put('item/agentMessage/delta',itemId='answer',delta=raw[:16])
        put('item/agentMessage/delta',itemId='answer',delta=raw[16:])
        put('turn/completed',turn={'id':'turn-a','status':'completed','items':[{'type':'agentMessage','text':raw}]})
        events=[]
        answer=session.turn('thread-a','question',{'model':'model-a','reasoning_effort':'medium'},
                            paper_ai.ANSWER_SCHEMA,on_progress=events.append)
        self.assertEqual(answer['answer'],'**结论**\n接触力为 10 N。')
        self.assertEqual([e['text'] for e in events if e['type']=='reasoning'],['检查实验段落。'])
        self.assertEqual([e['text'] for e in events if e['type']=='answer'][-1],answer['answer'])
        self.assertNotIn('raw reasoning',str(events))

    def test_partial_answer_decodes_escapes_and_split_surrogate_without_json(self):
        self.assertEqual(paper_ai.answer_preview('{"answer":"第一行\\n第二行\\u4e2d\\u6587\\uD83D'), '第一行\n第二行中文')
        self.assertEqual(paper_ai.answer_preview('{"citations":[],"answer":"quote \\" and \\uD83D\\uDE00"}'), 'quote " and 😀')
        self.assertEqual(paper_ai.answer_preview('{"citations":["p1-b1"'), '')

    def test_readable_reasoning_is_saved_with_answer_and_reopens_in_history(self):
        original=FakeSession.turn
        def streamed(self,thread_id,prompt,config,schema,**kwargs):
            kwargs['on_progress']({'type':'reasoning','text':'对照实验与所引段落。'})
            return original(self,thread_id,prompt,config,schema)
        events=[]
        with patch.object(FakeSession,'turn',streamed):
            self.ask('What was measured?',on_progress=events.append)
        with self.db() as con:
            saved=paper_ai.history(con,PID)['messages']
        self.assertEqual([m['kind'] for m in saved],['question','reasoning','answer'])
        self.assertEqual(saved[1]['text'],'对照实验与所引段落。')
        self.assertTrue(any(e['type']=='thread' for e in events))

    def test_item_completed_can_supply_answer_when_turn_items_are_empty(self):
        session=RealCodexSession.__new__(RealCodexSession)
        session.deadline=time.monotonic()+1
        session.events=queue.Queue()
        session.rpc=lambda *_: {'turn':{'id':'turn-a'}}
        for method,values in [('item/completed',{'item':{'type':'agentMessage','id':'a','text':'{"answer":"结论","citations":[]}'}}),
                             ('turn/completed',{'turn':{'id':'turn-a','status':'completed','items':[]}})]:
            session.events.put({'method':method,'params':{'threadId':'thread-a','turnId':'turn-a',**values}})
        self.assertEqual(session.turn('thread-a','q',{'model':'model-a','reasoning_effort':'medium'},paper_ai.ANSWER_SCHEMA)['answer'],'结论')

    def test_app_server_error_notification_is_reported(self):
        session=RealCodexSession.__new__(RealCodexSession)
        session.deadline=time.monotonic()+1
        session.events=queue.Queue()
        session.rpc=lambda *_: {'turn':{'id':'test-turn'}}
        session.events.put({'method':'error','params':{'threadId':'test-thread',
            'turnId':'test-turn','willRetry':False,
            'error':{'message':'rate limit reached'}}})
        with self.assertRaises(paper_ai.SessionError) as raised:
            RealCodexSession.turn(session,'test-thread','question',
                {'model':'model-a','reasoning_effort':'medium'},paper_ai.ANSWER_SCHEMA)
        self.assertEqual(raised.exception.code,'quota')

    def test_stopped_answer_is_persisted_as_incomplete(self):
        with patch.object(FakeSession,'turn',return_value={'answer':'Partial','citations':[],'sources':[],'stopped':True}):
            result=self.ask('Stop this')
        self.assertTrue(result['stopped'])
        with self.db() as con:
            messages=paper_ai.history(con,PID)['messages']
        self.assertEqual(messages[-1]['kind'],'stopped')
        self.assertIn('已停止',messages[-1]['text'])
        self.assertEqual(messages[-2]['text'],'Stop this')

    def test_compaction_failure_is_not_reported_as_success(self):
        session=RealCodexSession.__new__(RealCodexSession)
        session.deadline=time.monotonic()+1
        session.events=queue.Queue()
        session.rpc=lambda *_: {}
        session.events.put({'method':'turn/completed','params':{'threadId':'thread',
            'turn':{'id':'t','status':'failed','error':{'message':'connection timed out'}}}})
        with self.assertRaises(paper_ai.SessionError):
            session.turn('thread','',resolve(),paper_ai.CHAT_SCHEMA,compact=True)

    def test_interrupt_sends_rpc_and_keeps_partial_answer(self):
        import threading
        session=RealCodexSession.__new__(RealCodexSession)
        session.number=0
        session.deadline=time.monotonic()+1
        session.events=queue.Queue()
        session.rpc=lambda *_: {'turn':{'id':'t'}}
        cancellation=threading.Event()
        sent=[]
        session.send=sent.append
        events=[]
        def progress(event):
            events.append(event)
            if event['type']=='answer': cancellation.set()
        for method,params in [
            ('item/agentMessage/delta',{'itemId':'a','delta':'{"answer":"Partial answer'}),
            ('turn/completed',{'turn':{'id':'t','status':'interrupted'}})]:
            session.events.put({'method':method,'params':{'threadId':'thread','turnId':'t',**params}})
        result=session.turn('thread','q',resolve(),paper_ai.CHAT_SCHEMA,cancel_event=cancellation,on_progress=progress)
        self.assertTrue(result['stopped'])
        self.assertEqual(result['answer'],'Partial answer')
        self.assertEqual(sent[0]['method'],'turn/interrupt')
        self.assertEqual(sent[0]['params'],{'threadId':'thread','turnId':'t'})

    def test_compaction_waits_for_terminal_and_reports_usage_and_auto_progress(self):
        for compact in (False,True):
            session=RealCodexSession.__new__(RealCodexSession)
            session.deadline=time.monotonic()+1
            session.events=queue.Queue()
            session.rpc=lambda *_: {} if compact else {'turn':{'id':'t'}}
            events=[]
            for method,params in [
                ('turn/started',{'turn':{'id':'t'}}),
                ('item/started',{'item':{'type':'contextCompaction'}}),
                ('thread/tokenUsage/updated',{'tokenUsage':{'last':{'totalTokens':100},'modelContextWindow':1000}}),
                ('item/completed',{'item':{'type':'contextCompaction'}}),
                ('turn/completed',{'turn':{'id':'t','status':'completed','items':[{'type':'agentMessage','text':'{"answer":"ok","citations":[]}'}]}})]:
                session.events.put({'method':method,'params':{'threadId':'thread','turnId':'t',**params}})
            result=session.turn('thread','q',resolve(),paper_ai.CHAT_SCHEMA,compact=compact,on_progress=events.append)
            self.assertTrue(any(e.get('stage')=='compacting' for e in events))
            self.assertTrue(any(e['type']=='usage' for e in events))
            self.assertTrue(result.get('compacted') if compact else result['answer']=='ok')

    def test_app_server_failed_turn_is_reported(self):
        session=RealCodexSession.__new__(RealCodexSession)
        session.deadline=time.monotonic()+1
        session.events=queue.Queue()
        session.rpc=lambda *_: {'turn':{'id':'test-turn'}}
        session.events.put({'method':'turn/completed','params':{'threadId':'test-thread',
            'turn':{'id':'test-turn','status':'failed',
                    'error':{'message':'connection timed out'},'items':[]}}})
        with self.assertRaises(paper_ai.SessionError) as raised:
            RealCodexSession.turn(session,'test-thread','question',
                {'model':'model-a','reasoning_effort':'medium'},paper_ai.ANSWER_SCHEMA)
        self.assertEqual(raised.exception.code,'network')


if __name__=='__main__': unittest.main()
