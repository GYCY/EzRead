"""Terminology and formula checks use synthetic text, isolated storage and mock inference."""
import copy
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
import paper_ai
import pdf_tools
from ezread import terminology, formulas, tables, paragraphs
from types import SimpleNamespace
import pdf_regions

PID = '1234567890abcdef'
CONFIG = {'model': 'mock', 'reasoning_effort': 'low'}


class ReaderQualityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd() / 'work')
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        for name, value in [('DATA', root), ('LIBRARY', root / 'library'), ('QUEUED', set()), ('STRUCTURE_QUEUED', set())]:
            p = patch.object(server, name, value); p.start(); self.addCleanup(p.stop)
        server.init_db()
        self.doc = {'id': PID, 'hash': 'synthetic', 'title': 'Fixture', 'deleted': False,
                    'pages': [{'number': 1}], 'figures': [], 'translation': {},
                    'blocks': [{'id': 'b1', 'page': 1, 'text': 'Contact force matters.', 'translation': '接触力度重要。', 'translation_manually_edited': True, 'bbox': [.1, .1, .5, .2]},
                               {'id': 'b2', 'page': 1, 'text': 'f(x) (2)', 'translation': '旧公式译文', 'bbox': [.2, .4, .9, .5], 'note': '保留批注'}]}
        server.put_doc(self.doc)
        self.entry = {'source': 'Contact force', 'target': '接触力', 'abbreviation': '', 'definition': '接触产生的力', 'block_id': 'b1', 'uncertain': False}

    def test_full_translation_includes_table_without_reader_action(self):
        from ezread import translations
        self.doc['blocks'].append({'id': 'table', 'text': 'Table 1. Force.', 'is_table': True,
                                   'table_cells': [['Force'], ['2.0 N']]})
        self.doc['translation'] = {'config': CONFIG, 'run_id': 'test', 'mode': 'retranslate', 'staging': {}}
        server.put_doc(self.doc)
        def model(db, pid, doc, batch, config, thread_id, **kwargs):
            return 'thread', {b['id']: '译文' for b in batch}
        with patch.object(terminology, 'ensure'), patch.object(paper_ai, 'translate_batch', side_effect=model), \
                patch.object(paper_ai, 'reader_json', return_value={'caption': '表 1。力。', 'cells': [['力'], ['2.0 N']]}) as table_model:
            translations.execute_translation(server, PID, threading.Event())
        saved = server.get_doc(PID)
        self.assertEqual(saved['translation']['status'], 'completed')
        self.assertEqual(saved['table_translations']['table']['cells'], [['力'], ['2.0 N']])
        table_model.assert_called_once()
        self.assertEqual(table_model.call_args.args[3], CONFIG)

    def save(self):
        return terminology.save(server, PID, {'revision': 0, 'entries': [self.entry]})

    def test_save_rejects_stale_and_relocates_wrong_source(self):
        saved = self.save()
        self.assertEqual(saved['entries'][0]['page'], 1)
        with self.assertRaisesRegex(ValueError, '已更新'):
            self.save()
        relocated = terminology.validate_entries([{**self.entry, 'block_id': 'missing'}], self.doc)[0]
        self.assertEqual(relocated['block_id'], 'b1')
        self.assertEqual(relocated['source_status'], 'verified')

    def test_generation_scans_late_blocks_and_retains_manual_entries(self):
        self.doc['blocks'].append({'id': 'late', 'page': 9, 'text': 'padding ' * 3000 + ' tactile sensor'})
        self.doc['terminology'] = {'revision': 1, 'entries': [self.entry], 'status': 'ready'}
        visited = []
        def model(instruction, payload, schema, config, cancel_event):
            visited.extend(p['text'] for p in payload['blocks'])
            return {'entries': []}
        with patch.object(paper_ai, 'reader_json', side_effect=model):
            entries = terminology.generate(self.doc, CONFIG)
        self.assertIn('tactile sensor', ''.join(visited))
        self.assertEqual(entries, [self.entry])

    def test_generation_flags_unconfirmed_terms_and_still_honors_cancellation(self):
        with patch.object(paper_ai, 'reader_json', return_value={'entries': [{**self.entry, 'source': 'invented', 'block_id': 'missing'}, self.entry]}):
            entries = terminology.generate(self.doc, CONFIG)
        self.assertEqual(len(entries), 2)
        self.assertTrue(entries[0]['uncertain'])
        self.assertEqual(entries[0]['source_status'], 'unverified')
        self.assertEqual(entries[0]['block_id'], '')
        self.assertIsNone(entries[0]['page'])
        self.assertEqual(entries[1]['source_status'], 'verified')
        self.assertEqual(terminology.translation_entries({'terminology': {'entries': entries}}), [entries[1]])
        event = threading.Event(); event.set()
        with patch.object(paper_ai, 'reader_json') as model, self.assertRaisesRegex(ValueError, '暂停'):
            terminology.generate(self.doc, CONFIG, event)
        model.assert_not_called()

    def test_generation_reserves_capacity_for_late_batches_at_the_ledger_limit(self):
        existing = [{**self.entry, 'source': f'Manual term {i}'} for i in range(498)]
        self.doc['terminology'] = {'revision': 1, 'entries': existing, 'status': 'ready'}
        self.doc['blocks'].append({'id': 'late', 'page': 9, 'text': 'padding ' * 3000 + 'late definition'})
        limits, visited = [], []
        def model(instruction, payload, schema, config, cancel_event):
            limit = schema['properties']['entries']['maxItems']
            self.assertEqual(limit, payload['max_new_entries'])
            limits.append(limit)
            visited.extend(b['text'] for b in payload['blocks'])
            return {'entries': [{**self.entry, 'source': f'New concept {len(limits)}-{i}'} for i in range(limit)]}
        with patch.object(paper_ai, 'reader_json', side_effect=model):
            entries = terminology.generate(self.doc, CONFIG)
        self.assertEqual(len(entries), 500)
        self.assertEqual(entries[:498], existing)
        self.assertIn('late definition', ''.join(visited))
        self.assertGreater(limits[-1], 0)
        self.assertEqual(self.doc['terminology']['entries'], existing)

    def test_generation_rejects_excess_output_without_truncating_or_overwriting(self):
        before = copy.deepcopy(self.doc)
        oversized = [{**self.entry, 'source': f'Concept {i}'} for i in range(81)]
        with patch.object(paper_ai, 'reader_json', return_value={'entries': oversized}), \
             self.assertRaisesRegex(ValueError, '本批数量限制'):
            terminology.generate(self.doc, CONFIG)
        self.assertEqual(self.doc, before)

    def test_matching_tolerates_typography_and_plurals_but_not_other_terms(self):
        for term, source in [('soft polyhedral network', 'Soft\u2011polyhedral networks'),
                             ('tactile sensor', 'tactile\n  sensors'),
                             ('compliance', 'compli-\nance'), ('force', 'for\u00adce'),
                             ('policy', 'policies'), ('analysis', 'analysis'), ('C++', 'C++')]:
            self.assertTrue(terminology.term_matches(term, source), (term, source))
        for term, source in [('force', 'reinforcement'), ('C++', 'C'), ('sensor', 'sensory'),
                             ('tactile sensor', 'tactile feedback sensor'), ('mass', 'mas'), ('x+y', 'xy')]:
            self.assertFalse(terminology.term_matches(term, source), (term, source))

    def test_unverified_status_cannot_be_cleared_without_source_evidence(self):
        entry = {**self.entry, 'source': 'invented', 'block_id': '', 'uncertain': False}
        saved = terminology.save(server, PID, {'revision': 0, 'entries': [entry]})
        again = terminology.save(server, PID, {'revision': saved['revision'], 'entries': [{**saved['entries'][0], 'uncertain': False}]})
        self.assertTrue(again['entries'][0]['uncertain'])
        self.assertEqual(terminology.translation_entries(server.get_doc(PID)), [])

    def test_split_source_block_and_wrong_batch_reference_do_not_abort(self):
        self.doc['blocks'].append({'id': 'long', 'text': 'Contact force. ' + 'padding ' * 3000, 'page': 2})
        with patch.object(paper_ai, 'reader_json', return_value={'entries': [{**self.entry, 'block_id': 'long'}]}) as model:
            entries = terminology.generate(self.doc, CONFIG)
        self.assertGreater(model.call_count, 1)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['block_id'], 'long')
        self.assertFalse(entries[0]['uncertain'])

    def test_unconfirmed_term_does_not_stop_full_translation(self):
        self.doc['blocks'][0].pop('translation'); self.doc['blocks'][0].pop('translation_manually_edited')
        self.doc['translation'] = {'config': CONFIG, 'run_id': 'run'}
        server.put_doc(self.doc)
        def translate(con, pid, doc, batch, config, thread_id, **kwargs):
            self.assertEqual(len(doc['terminology']['entries']), 2)
            self.assertEqual([e['source'] for e in terminology.translation_entries(doc)], ['Contact force'])
            return 'thread', {b['id']: '接触力重要。' for b in batch}
        with patch.object(paper_ai, 'reader_json', return_value={'entries': [self.entry, {**self.entry, 'source': 'invented'}]}), patch.object(paper_ai, 'translate_batch', side_effect=translate):
            server.execute_translation(PID, threading.Event())
        saved = server.get_doc(PID)
        self.assertEqual(saved['translation']['status'], 'completed')
        self.assertTrue(saved['terminology']['entries'][1]['uncertain'])
        self.assertEqual(saved['blocks'][0]['translation'], '接触力重要。')

    def test_review_does_not_use_unconfirmed_terms(self):
        terminology.save(server, PID, {'revision': 0, 'entries': [{**self.entry, 'uncertain': True}]})
        with patch.object(terminology, 'configuration', return_value=CONFIG), patch.object(paper_ai, 'reader_json') as model:
            preview = terminology.review(server, PID)
        model.assert_not_called()
        self.assertEqual(preview['items'], [])

    def test_concurrent_edit_during_build_is_preserved(self):
        def generate(*args):
            self.save()
            return []
        with patch.object(terminology, 'generate', side_effect=generate), self.assertRaisesRegex(ValueError, '未覆盖'):
            terminology.ensure(server, PID, CONFIG)
        self.assertEqual(terminology.ledger(server.get_doc(PID))['entries'][0]['target'], '接触力')

    def test_review_requires_selection_and_preserves_other_blocks(self):
        self.save()
        with patch.object(terminology, 'configuration', return_value=CONFIG), patch.object(paper_ai, 'reader_json', return_value={'b1': '接触力重要。'}):
            preview = terminology.review(server, PID)
        self.assertEqual(server.get_doc(PID)['blocks'][0]['translation'], '接触力度重要。')
        self.assertTrue(preview['items'][0]['manual'])
        result = terminology.apply_review(server, PID, {'preview_id': preview['id'], 'ids': ['b1']})
        self.assertEqual(result['blocks'][0]['translation'], '接触力重要。')
        self.assertEqual(result['blocks'][1], self.doc['blocks'][1])
        self.assertEqual(result['translation_versions'][-1]['blocks']['b1']['translation'], '接触力度重要。')

    def test_review_rejects_concurrent_translation_edit(self):
        self.save()
        with patch.object(terminology, 'configuration', return_value=CONFIG), patch.object(paper_ai, 'reader_json', return_value={'b1': '接触力重要。'}):
            preview = terminology.review(server, PID)
        server.update_doc(PID, lambda d: d['blocks'][0].update(translation='我的新修订'))
        with self.assertRaisesRegex(ValueError, '未覆盖'):
            terminology.apply_review(server, PID, {'preview_id': preview['id'], 'ids': ['b1']})


    def test_correction_add_remove_preserves_source_and_rejects_stale_batch(self):
        request = {'revision': formulas.revision(self.doc), 'additions': [
            {'page': 1, 'kind': 'algorithm', 'bbox': [.19, .39, .91, .51]}], 'removals': []}
        with patch.object(pdf_tools, 'cover_crop', return_value='algorithm.jpg'):
            corrected = formulas.correct(server, PID, request)
            with self.assertRaisesRegex(ValueError, '变化'):
                formulas.correct(server, PID, request)
        self.assertEqual(corrected['blocks'], self.doc['blocks'])
        self.assertEqual(formulas.display_doc(corrected)['blocks'][1]['source_region_kind'], 'algorithm')
        restored = formulas.correct(server, PID, {'revision': formulas.revision(corrected), 'removals': ['b2']})
        self.assertEqual(restored['blocks'], self.doc['blocks'])
        self.assertFalse(formulas.display_doc(restored)['blocks'][1].get('is_formula'))
        self.assertEqual(formulas.display_doc(restored)['blocks'][1]['translation'], '旧公式译文')

    def test_pure_image_region_translates_and_retries_without_repeating_success(self):
        with patch.object(pdf_tools, 'cover_crop', return_value='figure.jpg'):
            corrected = formulas.correct(server, PID, {'revision': formulas.revision(self.doc),
                'additions': [{'page': 1, 'kind': 'figure', 'bbox': [.1, .6, .8, .9]}]})
        block = formulas.display_doc(corrected)['blocks'][-1]
        self.assertEqual(block['source_region_kind'], 'figure')
        with patch.object(terminology, 'configuration', return_value=CONFIG), patch.object(paper_ai, 'reader_json', side_effect=ValueError('模拟断线')):
            saved, errors = formulas.translate_missing(server, PID)
        self.assertTrue(errors)
        self.assertEqual(saved['formula_regions'], corrected['formula_regions'])
        with patch.object(terminology, 'configuration', return_value=CONFIG), patch.object(paper_ai, 'reader_json', return_value={'text': '图中标签译文'}) as model:
            saved, errors = formulas.translate_missing(server, PID)
            formulas.translate_missing(server, PID)
        self.assertFalse(errors)
        self.assertEqual(model.call_count, 1)
        self.assertTrue(model.call_args.kwargs['image_paths'][0].endswith('figure.jpg'))
        self.assertEqual(formulas.display_doc(saved)['blocks'][-1]['region_translation'], '图中标签译文')
        saved['formula_regions'][0]['image'] = 'changed.jpg'
        self.assertNotIn('region_translation', formulas.display_doc(saved)['blocks'][-1])

    def caption_fixture(self):
        self.doc['blocks'] = [
            {'id': 'lead', 'page': 1, 'text': 'Figure 2. Model with a', 'bbox': [.1, .31, .48, .325]},
            {'id': 'other', 'page': 1, 'text': 'Other column body.', 'bbox': [.52, .326, .9, .4]},
            {'id': 'tail', 'page': 1, 'text': 'spring and damper.', 'bbox': [.1, .327, .48, .38], 'note': '保留批注'},
            {'id': 'body', 'page': 1, 'text': 'Next body paragraph.', 'bbox': [.1, .41, .48, .45]}]
        self.doc['formula_regions'] = [{'id': 'figure', 'page': 1, 'bbox': [.1, .1, .49, .30],
            'image': 'figure.jpg', 'source': '', 'region_kind': 'figure', 'block_ids': []}]
        figure = formulas.display_doc(self.doc)['blocks'][-1]
        self.doc['region_translations'] = {'figure': {'source_hash': formulas.region_fingerprint(figure), 'text': '已有图内译文'}}
        server.put_doc(self.doc)

    def test_missing_imported_caption_is_recovered_from_pdf_and_translated(self):
        from tests.test_region_splits import make_pdf
        self.caption_fixture()
        self.doc['blocks'] = [self.doc['blocks'][1], self.doc['blocks'][3]]
        self.doc['blocks'][1]['translation'] = '保留正文译文'
        self.doc['blocks'][1]['note'] = '保留批注'
        server.put_doc(self.doc)
        folder = server.LIBRARY / PID
        folder.mkdir(parents=True, exist_ok=True)
        make_pdf(folder / 'original.pdf', [(60, 550, 'Figure 12. Synthetic reconstruction.'),
            (60, 538, 'Caption continuation with force and geometry.'),
            (315, 550, 'Figure 13. Other column.'), (60, 500, 'Next body paragraph.')])
        with patch.object(terminology, 'configuration', return_value=CONFIG), patch.object(paper_ai, 'reader_json', return_value={'text': '图12的完整图注译文'}) as model:
            saved, errors = formulas.translate_missing(server, PID)
            again, retry_errors = formulas.translate_missing(server, PID)
        self.assertEqual(len(errors), 11)
        self.assertEqual(len(retry_errors), 11)
        self.assertEqual(model.call_count, 2)
        self.assertEqual([e['number'] for e in saved['figure_captions']], list(range(1, 14)))
        caption = next(b for b in formulas.display_doc(saved)['blocks'] if b.get('figure_caption_number') == 12)
        self.assertEqual(caption['text'], 'Figure 12. Synthetic reconstruction.\nCaption continuation with force and geometry.')
        self.assertEqual(caption['translation'], '图12的完整图注译文')
        self.assertEqual(saved['blocks'][:2], self.doc['blocks'])
        self.assertEqual(saved['blocks'], again['blocks'])
        self.assertIn(caption['id'], {b['id'] for b in server.public_doc(saved, True)['blocks']})
        self.assertEqual(server.translation_counts(saved)['total'], len(self.doc['blocks']))

    def test_numbered_caption_scan_without_manual_regions_and_retry(self):
        from tests.test_region_splits import make_pdf
        from ezread import figure_captions
        folder = server.LIBRARY / PID
        folder.mkdir(parents=True, exist_ok=True)
        make_pdf(folder / 'original.pdf', [(60, 550, 'Figure 3. Third.'), (315, 550, 'Figure 1. First.'),
            (60, 400, 'Figure 4. Duplicate A.'), (315, 400, 'Figure 4. Duplicate B.')])
        with patch.object(terminology, 'configuration', return_value=CONFIG), patch.object(paper_ai, 'reader_json', side_effect=[{'text': '图1译文'}, ValueError('断线')]) as model:
            errors, active = figure_captions.complete(server, PID)
        self.assertTrue(active)
        self.assertEqual([c.args[1]['figure_number'] for c in model.call_args_list], [1, 3])
        self.assertEqual(len(errors), 3)
        entries = server.get_doc(PID)['figure_captions']
        self.assertEqual([e['status'] for e in entries], ['translated', 'missing', 'pending', 'ambiguous'])
        with patch.object(terminology, 'configuration', return_value=CONFIG), patch.object(paper_ai, 'reader_json', return_value={'text': '图3译文'}) as model:
            figure_captions.complete(server, PID)
        model.assert_called_once()
        self.assertEqual(model.call_args.args[1]['figure_number'], 3)
        self.assertEqual(server.get_doc(PID)['blocks'], self.doc['blocks'])

    def test_numbered_caption_accepts_fig_abbreviations_and_case(self):
        from tests.test_region_splits import make_pdf
        from ezread import figure_captions
        folder = server.LIBRARY / PID
        folder.mkdir(parents=True, exist_ok=True)
        for prefix in ['Figure', 'figure', 'Fig', 'fig', 'Fig.', 'fig.', 'FIG.']:
            with self.subTest(prefix=prefix):
                make_pdf(folder / 'original.pdf', [(60, 550, f'{prefix} 1. Caption text.'),
                    (60, 538, 'Caption continuation.')])
                entries = figure_captions.scan(self.doc, folder / 'original.pdf')
                self.assertEqual(len(entries), 1)
                self.assertEqual(entries[0]['number'], 1)
                self.assertEqual(entries[0]['text'], f'{prefix} 1. Caption text.\nCaption continuation.')


    def test_correction_batch_rejects_overlap_without_saving_any_region(self):
        with patch.object(pdf_tools, 'cover_crop', return_value='image.jpg'), self.assertRaisesRegex(ValueError, '重叠'):
            formulas.correct(server, PID, {'revision': formulas.revision(self.doc), 'additions': [
                {'page': 1, 'kind': 'figure', 'bbox': [.1, .6, .8, .9]},
                {'page': 1, 'kind': 'table', 'bbox': [.2, .6, .8, .9]}]})
        self.assertNotIn('formula_regions', server.get_doc(PID))

    def test_manual_validation_does_not_crop_or_write_and_includes_partial_blocks(self):
        with patch.object(pdf_tools, 'cover_crop') as crop:
            matched = formulas.validate_manual_region(self.doc, {'page': 1, 'kind': 'formula', 'bbox': [.19, .39, .91, .51]})
            self.assertEqual([b['id'] for b in matched], ['b2'])
            partial = formulas.validate_manual_region(self.doc, {'page': 1, 'kind': 'figure', 'bbox': [.1, .1, .3, .2]})
            self.assertEqual([b['id'] for b in partial], ['b1'])
            crop.assert_not_called()
        self.assertNotIn('formula_review', server.get_doc(PID))


    def test_translation_prepares_and_passes_ledger_before_batch(self):
        self.doc['blocks'][0].pop('translation'); self.doc['blocks'][0].pop('translation_manually_edited')
        self.doc['translation'] = {'config': CONFIG, 'run_id': 'run'}
        server.put_doc(self.doc)
        sequence = []
        def prepare(*args): sequence.append('terms'); return [self.entry]
        def translate(con, pid, doc, batch, config, thread_id, **kwargs):
            sequence.append('translate')
            self.assertEqual(doc['terminology']['entries'][0]['target'], '接触力')
            return 'thread', {b['id']: '接触力' for b in batch}
        with patch.object(terminology, 'generate', side_effect=prepare), patch.object(paper_ai, 'translate_batch', side_effect=translate):
            server.execute_translation(PID, threading.Event())
        self.assertEqual(sequence, ['terms', 'translate'])

    def test_table_overlay_retains_original_and_excludes_body_from_translation(self):
        self.doc['formula_regions'] = [{'block_ids': ['b1', 'b2'], 'page': 1, 'bbox': [.1, .1, .9, .5],
                                       'image': 'table.jpg', 'region_kind': 'table', 'table_caption': 'Table 1.',
                                       'table_cells': [['Sensor', 'Force'], ['A', '2.0 N']]}]
        server.put_doc(self.doc)
        displayed = formulas.display_doc(self.doc)
        self.assertTrue(displayed['blocks'][0]['is_table'])
        self.assertTrue(displayed['blocks'][1]['formula_continuation'])
        self.assertEqual(server.translation_counts(self.doc)['total'], 0)
        response = {'caption': '表 1。', 'cells': [['传感器', '力'], ['A', '2.0 N']]}
        with patch.object(paper_ai, 'reader_json', return_value=response), patch.object(tables, 'configuration', return_value=CONFIG):
            tables.translate(server, PID, {'block_id': 'b1'})
        saved = server.get_doc(PID)
        self.assertEqual(saved['blocks'], self.doc['blocks'])
        self.assertEqual(formulas.display_doc(saved)['blocks'][0]['table_translation']['cells'], response['cells'])
        saved['formula_regions'][0]['image'] = 'changed.jpg'
        self.assertNotIn('table_translation', formulas.display_doc(saved)['blocks'][0])

    def test_table_rejects_shape_number_and_empty_cell_drift(self):
        self.doc['blocks'][0].update(is_table=True, table_cells=[['Sensor', '2.0 N', '']], image='table.jpg')
        server.put_doc(self.doc)
        for cells in [[['传感器', '3.0 N', '']], [['传感器', '2.0 kN', '']], [['传感器']], [['传感器', '2.0 N', '补写']]]:
            with patch.object(paper_ai, 'reader_json', return_value={'caption': '表', 'cells': cells}), patch.object(tables, 'configuration', return_value=CONFIG):
                with self.assertRaisesRegex(ValueError, '校验'):
                    tables.translate(server, PID, {'block_id': 'b1'})
        self.assertNotIn('table_translations', server.get_doc(PID))


    def test_figure_overlay_suppresses_duplicate_figure_and_keeps_caption(self):
        self.doc['figures'] = [{'page': 1, 'bbox': [.2, .4, .9, .5], 'path': 'old.jpg'}]
        self.doc['formula_regions'] = [{'block_ids': ['b2'], 'page': 1, 'bbox': [.2, .4, .9, .5], 'image': 'new.jpg', 'region_kind': 'figure'}]
        displayed = formulas.display_doc(self.doc)
        self.assertEqual(displayed['figures'], [])
        self.assertEqual(displayed['blocks'][0], self.doc['blocks'][0])
        self.assertEqual(server.translation_counts(self.doc)['total'], 1)

    def test_paragraph_repair_preserves_original_blocks_and_annotation_sources(self):
        self.doc['blocks'] = [
            {'id': 'a', 'page': 1, 'kind': 'paragraph', 'text': '· Build soft', 'bbox': [.1, .2, .5, .25], 'translation': '旧译文'},
            {'id': 'b', 'page': 1, 'kind': 'paragraph', 'text': 'networks.', 'bbox': [.1, .25, .5, .3], 'note': '保留'}]
        extracted = {'blocks': [{'id': 'new', 'page': 1, 'kind': 'paragraph', 'text': '· Build soft networks.', 'bbox': [.1, .2, .5, .3]}]}
        proposals = paragraphs.plan(self.doc, extracted)
        self.assertEqual(len(proposals), 1)
        original = copy.deepcopy(self.doc['blocks'])
        block = proposals[0]['blocks'][0]
        paragraphs.apply(self.doc, proposals, {block['id']: '· 构建软体网络。'}, 'now')
        self.assertEqual(self.doc['blocks'][:2], original)
        visible = [b for b in formulas.display_doc(self.doc)['blocks'] if not b.get('formula_continuation')]
        self.assertEqual([b['id'] for b in visible], [block['id']])
        with self.assertRaisesRegex(ValueError, '已经应用'):
            paragraphs.apply(self.doc, proposals, {block['id']: '重复'}, 'now')
        self.assertEqual(paragraphs.plan(self.doc, extracted), [])

    def test_paragraph_plan_rejects_missing_source_characters_and_cross_column_merge(self):
        self.doc['blocks'] = [{'id': 'a', 'page': 1, 'kind': 'paragraph', 'text': 'First half', 'bbox': [.1, .2, .4, .3]},
                              {'id': 'b', 'page': 1, 'kind': 'paragraph', 'text': 'second half', 'bbox': [.6, .2, .9, .3]}]
        extracted = {'blocks': [{'id': 'new', 'page': 1, 'kind': 'paragraph', 'text': 'First half second half', 'bbox': [.1, .2, .9, .3]}]}
        self.assertEqual(paragraphs.plan(self.doc, extracted), [])
        extracted['blocks'][0].update(text='First half missing', bbox=[.1, .2, .4, .3])
        self.assertEqual(paragraphs.plan(self.doc, extracted), [])

    def test_repaired_paragraph_annotation_follows_only_unique_source_quote(self):
        from ezread import text_actions
        doc = {'blocks': [{'id': 'old', 'text': 'soft networks'}, {'id': 'new', 'text': 'Build soft networks.'}],
               'paragraph_repairs': [{'source_ids': ['old'], 'replacement_ids': ['new']}],
               'text_annotations': [{'id': 'note', 'ranges': [{'block_id': 'old', 'language': 'original', 'start': 0, 'end': 4,
                   'quote': 'soft', 'prefix': '', 'suffix': '', 'text_hash': text_actions.digest('soft networks')}]}]}
        self.assertEqual(text_actions.resolved_annotations(doc)[0]['ranges'][0]['block_id'], 'new')
        doc['blocks'].append({'id': 'ambiguous', 'text': 'soft'})
        doc['paragraph_repairs'][0]['replacement_ids'].append('ambiguous')
        self.assertEqual(text_actions.resolved_annotations(doc)[0]['status'], 'needs_review')


class AlgorithmRegionTests(unittest.TestCase):
    def test_ruled_algorithm_is_separate_but_prose_mention_is_not(self):
        page = SimpleNamespace(width=600, height=800, horizontal_edges=[
            {'x0': 50, 'x1': 280, 'top': 99}, {'x0': 50, 'x1': 280, 'top': 120},
            {'x0': 50, 'x1': 280, 'top': 200}])
        lines = [{'text': 'Algorithm 1: Navigation', 'bbox': [51, 102, 250, 112]},
                 {'text': '1: while contact do', 'bbox': [51, 130, 230, 140]},
                 {'text': '2: return trajectory', 'bbox': [51, 180, 230, 190]},
                 {'text': 'Algorithm 1 is evaluated below.', 'bbox': [51, 220, 270, 230]}]
        regions = pdf_regions.algorithm_regions(page, lines)
        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0]['source_region_kind'], 'algorithm')
        self.assertIn('return trajectory', regions[0]['text'])
        self.assertNotIn('evaluated', regions[0]['text'])


class NumberedFormulaTests(unittest.TestCase):
    @staticmethod
    def line(text, box):
        return {'text': text, 'bbox': box, 'size': 10, 'math': 0, 'bold': 0, 'chars': list(text)}

    def test_numbered_equation_without_equals_is_detected(self):
        self.assertTrue(pdf_tools._numbered_equation('f(x) (12)'))
        self.assertTrue(pdf_tools._numbered_equation('x + y (A.2)'))
        self.assertTrue(pdf_tools._numbered_equation('Fe 1⁄4 keXe, (3)'))
        self.assertTrue(pdf_tools._numbered_equation('F ¼ Fe þ Fi (1)'))
        for text in ['As shown in (12)', 'The result is reported in (2)', '(1) First item', 'Fig. 3 (2)', 'Experiment (1)']:
            self.assertFalse(pdf_tools._numbered_equation(text), text)

    def test_numbered_region_keeps_scripts_and_never_other_column(self):
        lines = [self.line('F 1⁄4 Fe þ Fi', [380, 460, 450, 473]), self.line('(1)', [522, 460, 534, 473]),
                 self.line('X3', [423, 450, 437, 460]), self.line('i1⁄41', [425, 475, 435, 482]),
                 self.line('Other column prose.', [60, 460, 280, 473])]
        for line in lines: line['_column'] = 'right' if line['bbox'][0] > 300 else 'left'
        regions = pdf_regions.numbered_regions(lines, 600)
        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0]['bbox'], [380, 450, 534, 482])
        self.assertEqual(len(regions[0]['_lines']), 4)

    def test_bullet_continuations_form_one_item_and_next_bullet_starts_new(self):
        lines = [self.line('· Proposed a generic design for soft', [60, 100, 280, 116]),
                 self.line('polyhedral networks with in-finger vision', [73, 116, 280, 126]),
                 self.line('for proprioception.', [73, 128, 180, 138]),
                 self.line('· Implemented another item.', [60, 140, 280, 156])]
        lines[0]['size'] = lines[-1]['size'] = 13
        blocks = pdf_tools._paragraphs(lines, 600, 10)
        self.assertEqual(len(blocks), 2)
        self.assertIn('soft polyhedral networks', blocks[0]['text'])

    def test_unfinished_paragraph_keeps_capitalized_continuation_across_columns(self):
        import paper_structure
        doc = {'blocks': [{'id': 'left', 'kind': 'paragraph', 'page': 2,
                           'bbox': [.1, .7, .45, .8], 'text': 'The material makes the Soft Polyhedral'},
                          {'id': 'right', 'kind': 'paragraph', 'page': 2,
                           'bbox': [.55, .1, .9, .2], 'text': 'Network responsive to adaptation in a time-dependent manner.'}]}
        structure = paper_structure.organize(doc, {'left': 'paragraph', 'right': 'paragraph'})
        entries = [e for s in structure['sections'] for e in s['entries']]
        self.assertEqual(entries[0]['source_ids'], ['left', 'right'])

    def test_table_bounds_use_caption_rules_and_footnote_not_following_prose(self):
        caption = self.line('Table 2. Comparison.', [50, 100, 350, 110])
        body = self.line('Sensor 2.0 N', [50, 145, 350, 155])
        foot = self.line('a Source note.', [50, 182, 350, 190]); foot['size'] = 8
        prose = self.line('Following paragraph.', [50, 205, 350, 215])
        page = SimpleNamespace(width=600, height=800, horizontal_edges=[{'x0': 50, 'x1': 550, 'top': y} for y in [115, 135, 180]])
        with patch.object(pdf_regions, 'source_grid', return_value=[]):
            regions = pdf_regions.table_regions(page, [caption, body, foot, prose])
        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0]['bbox'], [49, 99, 551, 191])
        self.assertNotIn(prose, regions[0]['_lines'])
        self.assertEqual(regions[0]['table_status'], 'needs-review')

    def test_ruled_table_preserves_multiline_cells_and_empty_cells(self):
        chars = []
        for text, x, y in [('Sensor', 55, 106), ('Force', 155, 106), ('Soft', 55, 130), ('finger', 55, 142)]:
            chars.extend({'text': c, 'x0': x + i * 5, 'x1': x + i * 5 + 4,
                          'top': y, 'bottom': y + 8, 'size': 8} for i, c in enumerate(text))
        edges = [{'x0': 50, 'x1': 250, 'top': y} for y in [100, 123, 160]]
        page = SimpleNamespace(crop=lambda box: SimpleNamespace(chars=chars),
                               vertical_edges=[{'x0': x, 'top': 100, 'bottom': 160} for x in [50, 150, 250]])
        self.assertEqual(pdf_regions.source_grid(page, 50, 250, 100, 160, edges), [['Sensor', 'Force'], ['Soft\nfinger', '']])
        page.vertical_edges = []
        self.assertEqual(pdf_regions.source_grid(page, 50, 250, 100, 160, edges), [])

    def test_separate_number_joins_formula_but_not_prose_or_other_column(self):
        number = self.line('(4)', [260, 100, 280, 111])
        formula = self.line('f(x)', [100, 100, 180, 112])
        lines = pdf_tools._attach_equation_numbers([formula, number], 600)
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]['text'], 'f(x) (4)')
        self.assertEqual(lines[0]['bbox'][2], 280)
        prose = self.line('As shown in', [100, 100, 180, 112])
        self.assertEqual(len(pdf_tools._attach_equation_numbers([prose, number], 600)), 2)
        far = self.line('(4)', [560, 100, 580, 111])
        self.assertEqual(len(pdf_tools._attach_equation_numbers([self.line('f(x)', [100, 100, 180, 112]), far], 600)), 2)

    def test_multiline_math_merges_and_does_not_swallow_next_numbered_equation(self):
        def block(text, y, formula=True):
            line = self.line(text, [100, y, 270, y + 10])
            return {'text': text, 'bbox': line['bbox'], '_lines': [line], 'is_formula': formula, 'kind': 'paragraph'}
        first, second, third = block('f(x) = x +', 100), block('y (1)', 112), block('g(x) (2)', 128)
        merged = pdf_tools._merge_formula_regions(copy.deepcopy([first, second, third]))
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0]['bbox'], [100, 100, 270, 122])

    def test_inline_parameters_are_not_display_equations(self):
        lines = [self.line('elements m1, m2 = 0.10, 0.11 mm/N and the', [60, 100, 290, 112]),
                 self.line('characteristic creep times t1, t2 = 3.1, 22.8 s,', [60, 113, 290, 125])]
        for line in lines:
            line.update(math=.8)
        blocks = pdf_tools._paragraphs(lines, 600, 10)
        self.assertFalse(any(b['is_formula'] for b in blocks))

    def test_left_figure_caption_cannot_absorb_right_column_graphics(self):
        page = SimpleNamespace(width=600, height=800, images=[], curves=[], lines=[],
                               rects=[{'x0': 60, 'x1': 280, 'top': 80, 'bottom': 200},
                                      {'x0': 350, 'x1': 470, 'top': 90, 'bottom': 180}])
        caption = self.line('Figure 2. Model.', [60, 220, 280, 230])
        caption['_column'] = 'left'
        boxes = pdf_tools._graphic_candidates(page, [caption])
        self.assertEqual(len(boxes), 1)
        self.assertLess(boxes[0][2], 300)

    def test_author_year_references_split_and_keep_hanging_lines(self):
        lines = [self.line('Smith A, Doe B, et al. (2020) First title.', [60, 100, 290, 110]),
                 self.line('Journal 1: 20-30.', [75, 112, 290, 122]),
                 self.line('Jones C (2021) Another title.', [60, 124, 290, 134])]
        result = pdf_regions.reference_blocks([{'text': 'merged', '_lines': lines}], True)
        self.assertEqual(len(result), 2)
        self.assertIn('Journal 1', result[0]['text'])

    def test_numbered_fraction_uses_whole_body_and_next_line(self):
        lines = [self.line('K(t) = F(t)/X(t)', [330, 70, 500, 95]),
                 self.line('X(0)', [420, 90, 460, 100]),
                 self.line('(14)', [526, 91, 543, 101]),
                 self.line('= k exp(-t)', [364, 104, 428, 117])]
        result = pdf_regions.numbered_regions(lines, 600)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['bbox'], [330, 70, 543, 117])

    def test_real_pdf_extraction_includes_right_number_and_multiline_body(self):
        # Minimal lawful synthetic PDF; exercises pdfplumber geometry and PDFium crops.
        lines = [(60, 740, 'Synthetic formula extraction'),
                 (60, 700, 'We define the mapping below.'),
                 (240, 650, 'f(x)'), (520, 650, '(1)'),
                 (60, 615, 'The next expression is multiline.'),
                 (240, 570, 'g(x) = x +'), (240, 557, 'y'), (520, 557, '(2)'),
                 (60, 520, 'As shown in (12), we retain the prose.')]
        content = '\n'.join('BT /F1 10 Tf 1 0 0 1 %s %s Tm (%s) Tj ET' %
                            (x, y, text.replace('(', r'\(').replace(')', r'\)')) for x, y, text in lines).encode('ascii')
        objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
                   b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
                   b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
                   b'<< /Length ' + str(len(content)).encode() + b' >>\nstream\n' + content + b'\nendstream']
        data = b'%PDF-1.4\n'; offsets = [0]
        for i, obj in enumerate(objects, 1):
            offsets.append(len(data)); data += f'{i} 0 obj\n'.encode() + obj + b'\nendobj\n'
        xref = len(data)
        data += b'xref\n0 6\n0000000000 65535 f \n' + b''.join(f'{n:010d} 00000 n \n'.encode() for n in offsets[1:])
        data += f'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF'.encode()
        with tempfile.TemporaryDirectory(dir=Path.cwd() / 'work') as tmp:
            root = Path(tmp); source = root / 'synthetic.pdf'; source.write_bytes(data)
            extracted = pdf_tools.extract_document(source, root / 'assets')
            formulas_found = [b for b in extracted['blocks'] if b.get('is_formula')]
            self.assertEqual(len(formulas_found), 2)
            self.assertTrue(all(b['bbox'][2] > 520 / 600 for b in formulas_found), 'Right-side numbers must be in each crop')
            self.assertGreater(formulas_found[1]['bbox'][3] - formulas_found[1]['bbox'][1], .025)
            self.assertTrue(all((root / 'assets' / b['image']).is_file() for b in formulas_found))
            self.assertTrue(any('As shown in (12)' in b['text'] and not b.get('is_formula') for b in extracted['blocks']))


if __name__ == '__main__':
    unittest.main()
