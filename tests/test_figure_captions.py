"""Caption identity, source reuse and preservation across PDF layout boundaries."""
import copy
from contextlib import nullcontext
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ezread import figure_captions as captions


def line(text, y, column='span', right=550, size=10):
    return {'text': text, 'bbox': [50, y, right, y + size], 'size': size, '_column': column}


class FigureCaptionTests(unittest.TestCase):
    def scan(self, doc, lines):
        page = SimpleNamespace(width=600, height=800)
        with patch('pdfplumber.open', return_value=nullcontext(SimpleNamespace(pages=[page]))), \
                patch('pdf_tools._lines', return_value=lines):
            return captions.scan(doc, 'fixture.pdf')

    def test_spanning_caption_short_tail_reuses_translation(self):
        text = 'Figure 2. Full width caption. Short final sentence.'
        block = {'id': 'caption', 'page': 1, 'bbox': [50/600, .5, 550/600, 422/800],
                 'text': text, 'translation': '手工图注', 'translation_manually_edited': True}
        doc = {'blocks': [block]}
        entries = self.scan(doc, [line('Figure 2. Full width caption.', 400),
                                  line('Short final sentence.', 412, 'left', 220)])
        entry = entries[1]
        self.assertEqual(entry['source_ids'], ['caption'])
        self.assertEqual(entry['translation'], '手工图注')
        self.assertEqual(entry['status'], 'translated')
        doc['figure_captions'] = entries
        shown = copy.deepcopy(doc['blocks'])
        captions.display(doc, shown)
        self.assertEqual(shown, [block], 'Keep the editable source and its original ID')

    def test_wrapped_reference_does_not_create_duplicate_number(self):
        entries = self.scan({}, [line('As shown in', 100, 'left', 280),
                                line('Figure 1. We evaluate the method.', 112, 'left', 280),
                                line('Figure 1. Actual figure caption.', 400)])
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['status'], 'pending')
        self.assertEqual(entries[0]['text'], 'Figure 1. Actual figure caption.')

    def test_true_duplicate_caption_is_still_ambiguous(self):
        entries = self.scan({}, [line('Figure 1. First caption.', 100),
                                line('Figure 1. Second caption.', 400)])
        self.assertEqual(entries[0]['status'], 'ambiguous')

    def test_separate_superscript_is_restored_in_reading_order(self):
        row = line('R scores.', 412)
        def char(text, x, top=412, size=10):
            return {'text': text, 'x0': x, 'x1': x+5, 'top': top, 'size': size}
        row['chars'] = [char('R', 50), char(' scores.', 60)]
        exponent = {'text': '2', 'bbox': [55, 411, 60, 417], 'size': 6, '_column': 'left',
                    'chars': [char('2', 55, 411, 6)]}
        entries = self.scan({}, [line('Figure 5. Evaluation.', 400), row, exponent])
        self.assertIn('R2 scores.', entries[4]['text'])

    def test_old_damaged_ledger_does_not_duplicate_complete_source(self):
        source = {'id': 'original', 'page': 1, 'bbox': [.1, .5, .9, .6],
                  'text': 'Figure 5. Evaluation R2 scores along three directions.',
                  'translation': '原有完整译文', 'note': '用户批注', 'text_override': '用户校订原文'}
        entry = {'number': 5, 'page': 1, 'bbox': [.1, .5, .9, .6], 'source_ids': [],
                 'text': 'Figure 5. Evaluation R scores along three directions.', 'translation': '重复新译文'}
        doc = {'blocks': [source], 'figure_captions': [entry]}
        original = copy.deepcopy(doc)
        shown = copy.deepcopy(doc['blocks'])
        captions.display(doc, shown)
        self.assertEqual(shown, [source])
        self.assertEqual(doc, original, 'Rendering must not mutate any stored history')

    def test_unrelated_caption_on_same_page_is_not_hidden(self):
        source = {'id': 'other', 'page': 1, 'bbox': [.1, .5, .9, .6],
                  'text': 'Figure 5. Different experiment.', 'translation': '保留'}
        entry = {'number': 5, 'page': 1, 'bbox': [.1, .5, .9, .6], 'source_ids': [],
                 'text': 'Figure 5. Evaluation R scores along three directions.'}
        shown = [dict(source)]
        captions.display({'blocks': [source], 'figure_captions': [entry]}, shown)
        self.assertEqual(shown[0], source)
        self.assertEqual(len(shown), 2)
