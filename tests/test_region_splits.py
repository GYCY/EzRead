"""Real synthetic PDFs exercise crop boundaries without touching the user's library."""
import copy
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
import paper_ai
from ezread import formulas, region_splits, terminology, text_actions

PID = '0123456789abcdef'


def make_pdf(path, lines=None):
    lines = lines if lines is not None else [(60, 640, 'Before retained.'), (60, 520, 'GRAPH LABEL'),
             (60, 360, 'SECOND LABEL'), (60, 240, 'After retained.')]
    content = '\n'.join(f'BT /F1 10 Tf 1 0 0 1 {x} {y} Tm ({text}) Tj ET' for x,y,text in lines).encode()
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
        b'<< /Length '+str(len(content)).encode()+b' >>\nstream\n'+content+b'\nendstream']
    raw=b'%PDF-1.4\n'; offsets=[]
    for i,obj in enumerate(objects,1):
        offsets.append(len(raw)); raw+=f'{i} 0 obj\n'.encode()+obj+b'\nendobj\n'
    xref=len(raw)
    raw+=b'xref\n0 6\n0000000000 65535 f \n'+b''.join(f'{n:010d} 00000 n \n'.encode() for n in offsets)
    raw+=f'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF'.encode()
    path.write_bytes(raw)


class SplitTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(dir=Path.cwd()/'work'); self.addCleanup(temp.cleanup)
        root=Path(temp.name)
        for name,value in [('DATA',root),('LIBRARY',root/'library'),('QUEUED',set()),('STRUCTURE_QUEUED',set())]:
            p=patch.object(server,name,value); p.start(); self.addCleanup(p.stop)
        server.init_db(); folder=server.LIBRARY/PID; folder.mkdir()
        make_pdf(folder/'original.pdf')
        text='Before retained. GRAPH LABEL SECOND LABEL After retained.'
        self.original={'id':'source','page':1,'kind':'paragraph','bbox':[.09,.18,.8,.72], 'text':text,
                       'translation':'完整旧译文，含公式前后正文。','note':'原有批注','text_override':text+' Edited.'}
        start=text.index('After')
        self.annotation={'id':'annotation','kind':'note','note':'保留定位','ranges':[{'block_id':'source','language':'original',
            'start':start,'end':start+5,'quote':'After','prefix':'','suffix':'','text_hash':text_actions.digest(self.original['text_override'])}]}
        server.put_doc({'id':PID,'hash':'fixture','pages':[{'number':1}], 'blocks':[self.original],
                        'figures':[], 'translation':{}, 'text_annotations':[self.annotation]})

    def correct(self, boxes=(), removals=()):
        d=server.get_doc(PID)
        return formulas.correct(server,PID,{'revision':formulas.revision(d), 'additions':[
            {'page':1,'kind':'figure','bbox':b} for b in boxes], 'removals':list(removals)})

    def test_split_preserves_original_translation_annotations_and_all_outside_text(self):
        saved=self.correct([[.09,.32,.8,.39]])
        self.assertEqual(saved['blocks'][0],self.original)
        self.assertEqual(saved['text_annotations'],[self.annotation])
        shown=formulas.display_doc(saved)
        self.assertTrue(shown['blocks'][0]['region_retained'])
        outside=[b for b in shown['blocks'] if b.get('region_split_parent') and not b.get('formula_continuation')]
        self.assertEqual([b['text'] for b in outside],['Before retained.','SECOND LABEL','After retained.'])
        self.assertTrue(all(not b.get('translation') for b in outside))
        self.assertEqual(text_actions.resolved_annotations(shown)[0]['status'],'resolved')
        self.assertEqual(server.translation_counts(saved)['total'],3)
        # Persisted records survive a database round trip.
        with closing(sqlite3.connect(server.DATA/'library.sqlite3')) as con:
            restored=json.loads(con.execute('select doc from papers where id=?',(PID,)).fetchone()[0])
        self.assertEqual(formulas.display_doc(restored),shown)

    def test_two_crops_repartition_and_remove_restores_source_without_erasing_history(self):
        first=self.correct([[.09,.32,.8,.39]])
        fragment=first['region_text_splits'][0]['fragment_ids'][0]
        server.update_doc(PID,lambda d: next(b for b in d['blocks'] if b['id']==fragment).update(translation='手工保留译文'))
        second=self.correct([[.09,.52,.8,.59]])
        self.assertEqual(len(second['region_text_splits'][0]['fragment_ids']),2)
        self.assertEqual(next(b for b in second['blocks'] if b['id']==fragment)['translation'],'手工保留译文')
        restored=self.correct(removals=[r['id'] for r in second['formula_regions']])
        self.assertEqual(restored['blocks'][0],self.original)
        self.assertEqual(restored['region_text_splits'],[])
        self.assertEqual(restored['text_annotations'],[self.annotation])
        shown=formulas.display_doc(restored)
        self.assertFalse(shown['blocks'][0].get('region_retained'))
        self.assertTrue(next(b for b in shown['blocks'] if b['id']==fragment)['region_retained'])

    def test_bbox_overlap_without_glyph_overlap_does_not_split_or_hide_body(self):
        saved=self.correct([[.5,.3,.7,.6]])
        self.assertEqual(saved['region_text_splits'],[])
        self.assertEqual(formulas.display_doc(saved)['blocks'][0],self.original)

    def test_outside_translation_is_independent_and_failure_keeps_old_content(self):
        saved=self.correct([[.09,.32,.8,.39]])
        def model(instruction,payload,schema,config,event,**kwargs):
            return {'text':'新译文：'+payload.get('text','图中标签')}
        with patch.object(terminology,'configuration',return_value={'model':'mock','reasoning_effort':'low'}), patch.object(paper_ai,'reader_json',side_effect=model):
            translated,errors=formulas.translate_missing(server,PID)
        self.assertFalse(errors)
        self.assertEqual(translated['blocks'][0],self.original)
        for bid in saved['region_text_splits'][0]['fragment_ids']:
            self.assertTrue(next(b for b in translated['blocks'] if b['id']==bid)['translation'].startswith('新译文'))
        self.assertEqual(translated['text_annotations'],[self.annotation])

    def test_unmatched_pdf_text_aborts_atomically(self):
        server.update_doc(PID,lambda d: d['blocks'][0].update(text='Unmatched source'))
        before=copy.deepcopy(server.get_doc(PID))
        with self.assertRaisesRegex(ValueError,'无法完整对应'):
            self.correct([[.09,.32,.8,.39]])
        self.assertEqual(server.get_doc(PID),before)

    def test_glyph_positions_partition_one_line_left_middle_right(self):
        chars=[{'text':c,'x0':i*10,'x1':i*10+8,'top':10,'bottom':20,'size':10} for i,c in enumerate('ABCDE')]
        block={'id':'line','page':1,'text':'ABCDE','bbox':[0,0,1,1]}
        parts,inside=region_splits.partition(block,[{'chars':chars}], [{'id':'crop','bbox':[.18,0,.38,1]}],100,100)
        self.assertEqual(inside['crop'],'CD')
        self.assertEqual(''.join(p['text'].replace(' ','') for p in parts),'ABE')


if __name__=='__main__': unittest.main()
