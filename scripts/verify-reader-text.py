"""Real browser selection events + real HTTP/storage, with synthetic papers/models."""
import json
import os
from pathlib import Path
import shutil
import secrets
import subprocess
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import server
import codex_models
import codex_usage
import codex_bridge
import paper_ai
import paper_structure
from ezread.selection_output import diagnostic

PID = '0123456789abcdef'
calls = []
preparations = []
slow_model = False
progress_mode = ''


def main():
    native = '--native' in sys.argv
    quality = '--quality' in sys.argv
    run_id = secrets.token_hex(8)
    work = ROOT / 'work'; work.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='reader-text-browser-', dir=work, ignore_cleanup_errors=True) as folder:
        data = Path(folder).resolve()
        assert data.is_relative_to(work.resolve())
        server.DATA, server.LIBRARY = data, data / 'library'
        server.init_db()
        server.codex_status = lambda **kwargs: {'authenticated': True, 'available': True}
        codex_models.get = lambda **kwargs: {'models': [], 'status': 'available'}
        codex_models.resolve_config = lambda *args, **kwargs: {'model': 'mock-model', 'reasoning_effort': 'low'}
        codex_usage.snapshot = codex_usage.get = lambda **kwargs: {'status': 'unavailable', 'buckets': []}
        def translate(text, **kwargs):
            record = {'text': text, 'target_language': kwargs.get('target_language'), 'cancelled': False}
            calls.append(record)
            if progress_mode:
                progress = kwargs.get('on_progress')
                event = kwargs.get('cancel_event')
                for value in (
                        {'text': '模型已接收请求，等待输出…'},
                        {'kind': 'diagnostic', 'text': diagnostic('Network is not available. Reconnecting... 1/5')},
                        {'kind': 'output', 'text': '模拟正在输出的部分译文'},
                        {'kind': 'diagnostic', 'text': diagnostic('Stream disconnected before completion; retrying 2/5')}):
                    if progress: progress(value)
                    if event is not None and event.wait(.7):
                        record['cancelled'] = True
                        raise codex_bridge.TranslationError('此翻译请求已取消。', code='cancelled')
                if progress_mode == 'failure':
                    raise codex_bridge.TranslationError('无法连接 Codex 服务，请检查网络。', code='network')
                if progress: progress({'text': '翻译完成。'})
            if slow_model:
                event = kwargs.get('cancel_event')
                for _ in range(100):
                    if event is not None and event.wait(.05):
                        record['cancelled'] = True
                        raise codex_bridge.TranslationError('此翻译请求已取消。', code='cancelled')
            return '模拟划线译文：' + text
        codex_bridge.translate_selection = translate
        codex_bridge.prepare_selection = lambda **kwargs: preparations.append({'model': kwargs.get('model')})
        paper_ai.translate_batch = lambda *args, **kwargs: ('mock-thread', {b['id']: '重译：' + b['text'] for b in args[3]})
        doc = {'id': PID, 'hash': 'synthetic-original-pdf', 'title': 'Reader interaction fixture', 'authors': ['Test Author'],
               'journal': 'Synthetic Journal', 'year': 2026, 'deleted': False, 'translation': {'status': 'completed'},
               'pages': [{'number': n, 'width': 595, 'height': 842, 'image': 'page-001.png'} for n in (1, 2)], 'figures': [],
               'blocks': [
                   {'id': 'one', 'kind': 'paragraph', 'page': 1, 'text': 'First 😀 sentence. Another sentence.', 'translation': '第一😀句。第二句话保留。', 'bbox': [.1,.2,.85,.25]},
                   {'id': 'two', 'kind': 'paragraph', 'page': 1, 'text': 'Second paragraph has selectable text.', 'translation': '另一个自然段可以自由选择文字。', 'bbox': [.1,.4,.85,.45]},
                   {'id': 'three', 'kind': 'paragraph', 'page': 1, 'text': 'Legacy note and highlight stay available.', 'translation': '旧批注和高亮继续保留。', 'note': '原有批注', 'highlight': True, 'bbox': [.1,.6,.85,.65]},
                   {'id': 'four', 'kind': 'paragraph', 'page': 1, 'text': 'A long selected passage starts here. ' + 'Annotation geometry follows natural wrapping as the reader changes width and font size. ' * 5,
                    'translation': '批注应当跟随自然换行，不改变文字节点。' * 8, 'bbox': [.1,.7,.85,.8]},
                   {'id': 'five', 'kind': 'paragraph', 'page': 1, 'text': 'The same cross-paragraph annotation continues here.',
                    'translation': '这条跨段批注在下一段继续。', 'bbox': [.1,.8,.85,.9]}]}
        doc['reading_structure'] = {'version': paper_structure.VERSION, 'source_fingerprint': paper_structure.fingerprint(doc), 'status': 'ready',
            'sections': [{'id': 'intro', 'role': 'introduction', 'label': 'Introduction', 'entries': [{'id': b['id'], 'role': 'paragraph', 'source_ids': [b['id']]} for b in doc['blocks']]}]}
        server.put_doc(doc)
        from PIL import Image
        library = server.LIBRARY / PID; library.mkdir(parents=True)
        Image.new('RGB', (595, 842), '#f7f7ef').save(library / 'page-001.png')
        Image.new('RGB', (595, 842), '#f7f7ef').save(library / 'page-001.jpg')
        if quality:
            import pdf_tools
            doc['blocks'].append({'id': 'table-test', 'kind': 'caption', 'page': 2, 'text': 'Table 1. Sensor force.',
                                  'is_table': True, 'image': 'page-001.jpg', 'bbox': [.1, .6, .9, .8],
                                  'table_cells': [['Sensor', 'Force'], ['A', '2.0 N']]})
            from ezread.tables import fingerprint
            doc['table_translations'] = {'table-test': {'caption': '表 1。传感器力。',
                'cells': [['传感器', '力'], ['A', '2.0 N']], 'source_hash': fingerprint(doc['blocks'][-1])}}
            server.put_doc(doc)
            def reader_json(instruction, payload, schema, config, cancel_event=None, image_paths=None):
                if 'entries' in schema['properties']:
                    return {'entries': [{'source': 'First', 'target': '第一', 'abbreviation': '', 'definition': '合成测试术语', 'block_id': 'one', 'uncertain': True}]}
                if 'text' in schema['properties']:
                    if not image_paths: return {'text': '模拟框外译文：' + payload.get('text', '')}
                    assert Path(image_paths[0]).is_file()
                    return {'text': '模拟图表译文：图中标签'}
                if 'cells' in schema['properties']:
                    return {'caption': '表 1。传感器力。', 'cells': [['传感器', '力'], ['A', '2.0 N']]}
                return {bid: '第一😀句。术语核对后的句子。' for bid in schema['properties']}
            paper_ai.reader_json = reader_json
            def manual_crop(folder, page, bbox):
                Image.new('RGB', (500, 80), 'white').save(folder / 'manual.jpg')
                return 'manual.jpg'
            pdf_tools.cover_crop = manual_crop
        class Handler(server.Handler):
            def log_message(self, *args): pass
            def do_GET(self):
                if native and self.path == '/':
                    raw = (ROOT / 'static/index.html').read_text(encoding='utf-8').replace('</head>', '<script src="/__test/native-driver.js" defer></script></head>').encode('utf-8')
                    self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw); return
                if native and self.path == '/__test/native-driver.js':
                    self.send_file(ROOT / 'tests/browser_reader_native.js'); return
                if self.path == '/__test/state':
                    self.send_json({'calls': calls, 'preparations': preparations, 'doc': server.get_doc(PID)}); return
                super().do_GET()
            def do_POST(self):
                global slow_model, progress_mode
                if quality and self.path == '/__test/caption-fixture':
                    sys.path.insert(0, str(ROOT / 'tests'))
                    from test_region_splits import make_pdf
                    make_pdf(library / 'original.pdf', [(60, 550, 'Figure 12. Missing caption.'),
                        (60, 538, 'Caption continuation.'), (60, 500, 'Next paragraph.')])
                    changed = server.get_doc(PID)
                    changed.update(blocks=[], paragraph_repairs=[], region_text_splits=[], text_annotations=[],
                        formula_regions=[{'id': 'caption-figure', 'page': 1, 'bbox': [.1,.1,.49,.30],
                            'image': 'manual.jpg', 'source': '', 'region_kind': 'figure', 'block_ids': []}],
                        region_translations={}, region_dismissals=[])
                    server.put_doc(changed)
                    self.send_json({'ok': True}); return
                if quality and self.path == '/__test/split-fixture':
                    sys.path.insert(0, str(ROOT / 'tests'))
                    from test_region_splits import make_pdf
                    from ezread.text_actions import digest
                    make_pdf(library / 'original.pdf')
                    source = 'Before retained. GRAPH LABEL SECOND LABEL After retained.'
                    changed = server.get_doc(PID)
                    changed.update(blocks=[{'id': 'split-source', 'page': 1, 'kind': 'paragraph', 'bbox': [.09,.18,.8,.72],
                        'text': source, 'translation': '完整旧译文保留', 'note': '保留旧批注'}],
                        pages=[{'number': 1, 'width': 600, 'height': 800, 'image': 'page-001.jpg'}], figures=[],
                        formula_regions=[], region_text_splits=[], region_dismissals=[], region_translations={},
                        text_annotations=[{'id': 'split-note', 'kind': 'note', 'note': '原文范围批注', 'ranges': [
                            {'block_id': 'split-source', 'language': 'original', 'start': 0, 'end': 6,
                             'quote': 'Before', 'prefix': '', 'suffix': '', 'text_hash': digest(source)}]}])
                    server.put_doc(changed)
                    self.send_json({'ok': True}); return
                if self.path == '/__test/model-delay':
                    slow_model = bool(self.read_json().get('enabled'))
                    self.send_json({'ok': True}); return
                if self.path == '/__test/model-progress':
                    progress_mode = self.read_json().get('mode', '')
                    self.send_json({'ok': True}); return
                if self.path == '/__test/native-results':
                    (work / 'reader-text-webview2-verification.json').write_text(json.dumps({**self.read_json(), 'run_id': run_id},ensure_ascii=False,indent=2),encoding='utf-8')
                    self.send_json({'ok':True}); return
                if self.path == '/__test/append':
                    server.update_doc(PID, lambda d: d['blocks'][0].update(translation=d['blocks'][0]['translation'] + ' 后台更新。'))
                    self.send_json({'ok': True}); return
                super().do_POST()
        listener = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.PORT = listener.server_port
        thread = threading.Thread(target=listener.serve_forever, daemon=True); thread.start()
        environment = os.environ.copy()
        environment['EZREAD_TEST_URL'] = f'http://127.0.0.1:{listener.server_port}'
        environment['EZREAD_TEST_ROOT'] = str(ROOT)
        runtime = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node'
        node = environment.get('EZREAD_NODE') or shutil.which('node') or str(runtime / 'bin/node.exe')
        environment.setdefault('EZREAD_PLAYWRIGHT', str(runtime / 'node_modules/playwright'))
        try:
            if native:
                report = work / 'reader-text-native-host.json'
                command = [str(ROOT/'desktop/bin/EzRead.Desktop.exe'),'--app-root',str(ROOT),'--data-dir',str(data),'--url',environment['EZREAD_TEST_URL'],'--smoke-test',str(report)]
                result = subprocess.run(command,cwd=ROOT,timeout=45,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                value = json.loads((work / 'reader-text-webview2-verification.json').read_text(encoding='utf-8'))
                if value.get('run_id') != run_id: raise RuntimeError('Native reader verification did not finish in this run.')
                if 'error' in value: raise RuntimeError(value['error'])
                print(json.dumps(value,ensure_ascii=False))
            else:
                result = subprocess.run([node, str(ROOT / ('tests/browser_reader_quality.cjs' if quality else 'tests/browser_reader_text.cjs'))], env=environment, cwd=ROOT)
            if result.returncode: raise SystemExit(result.returncode)
        finally:
            listener.shutdown(); listener.server_close(); thread.join()
    print('Browser selection verification completed; real library/model calls: 0.')


if __name__ == '__main__': main()
