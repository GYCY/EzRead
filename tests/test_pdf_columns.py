"""Two-column prose stays separate despite sparse graphic labels in the gutter."""
from collections import Counter
from types import SimpleNamespace
import unittest

import pdf_tools


class ColumnTests(unittest.TestCase):
    def test_sparse_labels_do_not_join_aligned_columns_or_split_spanning_title(self):
        for width in (600, 720):
            with self.subTest(width=width):
                scale = width / 600
                chars = []
                def row(text, x, top, step=4):
                    for i, letter in enumerate(text):
                        chars.append({'text': letter, 'x0': (x+i*step)*scale,
                            'x1': (x+(i+1)*step)*scale, 'top': top, 'bottom': top+10,
                            'size': 10, 'fontname': 'Helvetica', 'matrix': (1,0,0,1,0,800-top)})
                for y in range(200, 500, 20):
                    row('L'*59, 60, y)
                    row('R'*59, 309, y)
                row('label', 297, 80, 2.2)
                row('label', 297, 110, 2.2)
                row('x', 308, 80, 1)
                title = 'SpanningTitleAcrossBothColumns'
                row(title, 60, 55, 480/len(title))
                page = SimpleNamespace(width=width, height=800, chars=chars)
                self.assertIsNotNone(pdf_tools._column_gutter(page))
                lines = pdf_tools._lines(page)
                prose = [line for line in lines if 200 <= line['bbox'][1] < 500]
                self.assertEqual(len(prose), 30)
                self.assertTrue(all(not ('L' in line['text'] and 'R' in line['text']) for line in prose))
                self.assertEqual({line['_column'] for line in prose}, {'left', 'right'})
                self.assertTrue(any(line['text'] == title and line['_column'] == 'span' for line in lines))
                self.assertEqual(Counter(c['text'] for line in lines for c in line['chars']), Counter(c['text'] for c in chars))

    def test_dense_single_column_remains_one_line(self):
        chars = [{'text': 'A', 'x0': x, 'x1': x+4, 'top': y, 'bottom': y+10,
                  'size': 10, 'fontname': 'Helvetica'} for y in range(100, 500, 20) for x in range(60, 540, 4)]
        page = SimpleNamespace(width=600, height=800, chars=chars)
        self.assertIsNone(pdf_tools._column_gutter(page))
        self.assertEqual(len(pdf_tools._lines(page)), 20)
