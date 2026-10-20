"""Original PDF coverage and cancellation, with generated documents only."""
import tempfile
import threading
import unittest
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image
from ezread.chat_pdf import original_pdf, prepare_pdf
from test_layout_rules import synthetic_pdf


class ChatPDFTests(unittest.TestCase):
    def test_every_page_is_attached_including_scans_and_late_text(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'work') as temp:
            root=Path(temp)
            pid='0123456789abcdef'
            (root/pid).mkdir()
            source=root/pid/'original.pdf'
            tail=root/'tail.pdf'
            synthetic_pdf(tail,600,[(30,700,'Section 4.1: Young modulus is measured experimentally.')])
            document=pdfium.PdfDocument.new()
            try:
                for _ in range(35):
                    page=document.new_page(100,120)
                    page.close()
                with pdfium.PdfDocument(str(tail)) as appendix:
                    document.import_pages(appendix)
                document.save(str(source))
            finally:
                document.close()
            before=source.read_bytes()
            self.assertEqual(original_pdf(root,pid),source.resolve())
            with self.assertRaises(ValueError):
                original_pdf(root,'fedcba9876543210')
            pages,images=prepare_pdf(root,pid,root/'rendered')
            self.assertEqual(len(pages),36)
            self.assertEqual(len(images),36)
            self.assertEqual(pages[-1]['block_id'],'pdf-page-36')
            self.assertIn('Young modulus',pages[-1]['original'])
            self.assertEqual(pages[0]['original'],'')
            self.assertEqual(source.read_bytes(),before)
            for path in images:
                with Image.open(path) as image:
                    self.assertLessEqual(max(image.size),2600)
                    image.verify()
            cancelled=threading.Event();cancelled.set()
            with self.assertRaisesRegex(ValueError,'取消'):
                prepare_pdf(root,pid,root/'cancelled',cancel_event=cancelled)
            with self.assertRaises(ValueError):
                prepare_pdf(root,'../outside',root/'bad')
