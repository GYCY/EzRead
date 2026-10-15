"""Publication-independent fixtures; no private PDF, title, DOI or paper ID."""
import copy
from pathlib import Path
import tempfile
import unittest

import layout_rules
import pdf_tools
import pdf_regions


def synthetic_pdf(path, width, lines, strokes=()):
    commands = ['BT /F1 10 Tf 1 0 0 1 %s %s Tm (%s) Tj ET' %
                (x, y, text.replace('(', r'\(').replace(')', r'\)')) for x, y, text in lines]
    commands += [f'{x0} {y0} m {x1} {y1} l S' for x0, y0, x1, y1 in strokes]
    content = '\n'.join(commands).encode('ascii')
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
               f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} 800] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>'.encode(),
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
               b'<< /Length ' + str(len(content)).encode() + b' >>\nstream\n' + content + b'\nendstream']
    data, offsets = b'%PDF-1.4\n', []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(data)); data += f'{i} 0 obj\n'.encode() + obj + b'\nendobj\n'
    xref = len(data)
    data += b'xref\n0 6\n0000000000 65535 f \n' + b''.join(f'{n:010d} 00000 n \n'.encode() for n in offsets)
    data += f'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF'.encode()
    path.write_bytes(data)


class LayoutRuleTests(unittest.TestCase):
    def test_caption_notation_variants_and_negative_prose(self):
        for caption in ['Table 7. Data', 'TABLE II: Data', 'Table S2. Results', 'Tab. 3: Test', '表 2：结果']:
            self.assertTrue(layout_rules.TABLE_START.match(caption), caption)
        for caption in ['Fig. 4. Model', 'Figure S1: Supplement', 'FIGURE II. System', '图 3：装置']:
            self.assertTrue(layout_rules.FIGURE_START.match(caption), caption)
        for prose in ['Tables show data.', 'Figure out the answer.', 'As shown in Table 3.', 'Tabulation of results']:
            self.assertFalse(layout_rules.TABLE_START.match(prose) or layout_rules.FIGURE_START.match(prose))

    def test_bibliography_numbering_author_year_and_end_boundary(self):
        def line(text, y):
            return {'text': text, 'bbox': [60, y, 280, y+10], '_column': 'left'}
        for lead in ['[7] Doe A. Title.', '12. Doe A. Title.', '12) Doe A. Title.', 'Müller A, Doe B, et al. (2024a) Title.']:
            self.assertTrue(layout_rules.reference_start(lead))
            block = {'text': lead, 'kind': 'paragraph', '_lines': [line(lead, 100), line('Journal 2: 1-9.', 112)]}
            tail = {'text': 'Appendix A', 'kind': 'heading', '_lines': [line('Appendix A', 130)]}
            paragraph = {'text': 'Here is a method.', 'kind': 'paragraph', '_lines': [line('Here is a method.', 150)]}
            result = pdf_regions.reference_blocks([block, tail, paragraph], True)
            self.assertEqual([b['kind'] for b in result], ['reference', 'heading', 'paragraph'])

    def test_audit_keeps_source_and_reports_legacy_and_conflicts(self):
        doc = {'blocks': [{'id': 'eq', 'page': 2, 'is_formula': True, 'bbox': [.55,.1,.9,.2]},
                          {'id': 't', 'page': 3, 'text': 'Table IV. Test'}],
               'figures': [{'page': 2, 'bbox': [.1,.1,.9,.3]}]}
        before = copy.deepcopy(doc)
        codes = {i['code'] for i in layout_rules.audit(doc)['issues']}
        self.assertTrue({'legacy-extraction', 'figure-equation-overlap', 'table-caption-without-region', 'equation-source-missing'} <= codes)
        self.assertEqual(doc, before)
        self.assertEqual(layout_rules.audit({'extraction': {'version': 5}, 'blocks': []})['issues'][0]['code'], 'no-selectable-text')

    def test_different_page_sizes_and_table_labels_share_extractor(self):
        # Two independent synthetic papers, both three-rule tables. Neither
        # depends on the user's paper coordinates, title, numbering or identity.
        for width, label in [(600, 'Table IV. Measurements'), (720, 'Table S2. Experimental data')]:
            with self.subTest(width=width), tempfile.TemporaryDirectory(dir=Path.cwd()/'work') as temp:
                root = Path(temp)
                right = width-60
                lines = [(60,740,'Synthetic independent layout'), (60,700,label),
                         (60,670,'Method'), (width*.6,670,'Value'), (60,640,'Alpha'),
                         (width*.6,640,'2.0 N'), (60,620,'Beta'), (width*.6,620,'3.0 N'),
                         (60,560,'Ordinary prose follows the complete table.')]
                strokes = [(60,y,right,y) for y in [690,655,605]] + [(60,660,width*.4,660),(width*.55,660,right,660)]
                synthetic_pdf(root/'sample.pdf',width,lines,strokes)
                doc = pdf_tools.extract_document(root/'sample.pdf',root/'assets')
                tables = [b for b in doc['blocks'] if b.get('is_table')]
                self.assertEqual(len(tables),1)
                self.assertEqual(tables[0]['table_cells'], [['Method','Value'],['Alpha','2.0 N'],['Beta','3.0 N']])
                self.assertTrue(any('Ordinary prose' in b['text'] for b in doc['blocks']))
                self.assertEqual(doc['layout_quality']['rules_version'],layout_rules.VERSION)


if __name__ == '__main__':
    unittest.main()
