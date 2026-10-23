"""Static assets remain executable with conflicting Windows MIME associations."""
from http.server import ThreadingHTTPServer
import mimetypes
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
import urllib.request
from unittest.mock import patch

from ezread.web import handler_for


class StaticTypesTests(unittest.TestCase):
    def test_registry_associations_cannot_override_web_asset_types(self):
        assets = {'index.html': 'text/html', 'app.js': 'application/javascript',
                  'style.css': 'text/css', 'cover.svg': 'image/svg+xml',
                  'original.pdf': 'application/pdf', 'unknown.ezread-test': 'application/octet-stream'}
        mimetypes.init()
        with tempfile.TemporaryDirectory() as folder, \
             patch.dict(mimetypes.types_map, {'.html': 'text/plain', '.js': 'text/plain', '.css': 'text/plain'}):
            self.assertEqual(mimetypes.guess_type('app.js')[0], 'text/plain')
            root = Path(folder)
            for filename in assets:
                (root / filename).write_bytes(b'fixture')
            app = SimpleNamespace(VERSION='test', PORT=0, STATIC=root, SHUTDOWN=threading.Event())
            listener = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(app))
            app.PORT = listener.server_port
            thread = threading.Thread(target=listener.serve_forever, daemon=True)
            thread.start()
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            try:
                for filename, expected in assets.items():
                    with self.subTest(filename=filename), \
                         opener.open(f'http://127.0.0.1:{app.PORT}/static/{filename}', timeout=3) as response:
                        self.assertEqual(response.headers['Content-Type'], expected)
                        self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
                        self.assertEqual(response.read(), b'fixture')
            finally:
                listener.shutdown()
                listener.server_close()
                thread.join(timeout=3)
