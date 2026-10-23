"""Personal pilot UI checks use synthetic images and disposable databases only."""
from io import BytesIO
import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from datetime import datetime, timedelta, timezone


ROOT = Path(__file__).resolve().parents[1]
ORIGINAL_TABS = ['📊 ผลวิเคราะห์ราคา', '🎟️ ตัวเลือกสเต็ป', '📚 ราคาไหลและตรวจผล', 'ℹ️ วิธีใช้']
PILOT_TABS = ORIGINAL_TABS[:-1] + ['📸 คลังสถิติ', ORIGINAL_TABS[-1]]


def synthetic_image(name, color='green'):
    from PIL import Image
    image = BytesIO()
    Image.new('RGB', (32, 32), color).save(image, format='PNG')
    image.seek(0)
    image.name = name
    image.type = 'image/png'
    image.size = len(image.getvalue())
    return image


@unittest.skipUnless(importlib.util.find_spec('streamlit'), 'Streamlit is not installed locally')
class PersonalStreamlitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bill_path = Path(self.tmp.name) / 'original-bills.db'
        self.env = patch.dict(os.environ, VALUE_BET_DB=str(self.bill_path),
                              VALUE_BET_PERSONAL_STATS_PILOT='', OPENAI_API_KEY='',
                              THE_ODDS_API_KEY='', OPENAI_BASE_URL='')
        self.env.start()
        self.addCleanup(self.env.stop)
        # Rendering and review actions must never contact either provider.
        self.network = patch('requests.sessions.Session.request',
                             side_effect=AssertionError('personal UI test must remain offline'))
        self.request = self.network.start()
        self.addCleanup(self.network.stop)

    def app(self):
        from streamlit.testing.v1 import AppTest
        app = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=30).run()
        self.assertEqual(len(app.exception), 0)
        return app

    def assert_offline(self):
        self.request.assert_not_called()

    def test_default_and_explicit_disabled_pilot_keep_exact_original_four_tabs(self):
        for setting in ('', '0', 'false', 'no'):
            with self.subTest(setting=setting), patch.dict(os.environ, VALUE_BET_PERSONAL_STATS_PILOT=setting):
                app = self.app()
                self.assertEqual([tab.label for tab in app.tabs], ORIGINAL_TABS)
                self.assertFalse(any(widget.key == 'personal_mode' for widget in app.radio))
                with sqlite3.connect(self.bill_path) as con:
                    self.assertFalse(any(row[0].startswith('personal_match_stats')
                                         for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")))
        self.assert_offline()

    def test_enabled_pilot_adds_only_the_isolated_personal_tab_without_provider_calls(self):
        with patch.dict(os.environ, VALUE_BET_PERSONAL_STATS_PILOT='true'):
            app = self.app()
            self.assertEqual([tab.label for tab in app.tabs], PILOT_TABS)
            panel = app.tabs[3]
            self.assertTrue(any(widget.key == 'personal_mode' for widget in panel.radio))
            self.assertFalse(any(widget.key == 'personal_mode' for tab in (app.tabs[0], app.tabs[1], app.tabs[2], app.tabs[4]) for widget in tab.radio))
            uploads = panel.get('file_uploader')
            self.assertEqual(len(uploads), 1)
            self.assertTrue(uploads[0].proto.multiple_files)
            self.assertFalse(next(widget for widget in panel.checkbox if widget.key == 'personal_confirm').value)
        self.assert_offline()

    def test_two_uploaded_images_preview_without_automatic_extraction_or_save(self):
        photos = [synthetic_image('offline-home.png'), synthetic_image('offline-away.png', 'blue')]
        with patch.dict(os.environ, VALUE_BET_PERSONAL_STATS_PILOT='true'), \
                patch('streamlit.file_uploader', return_value=photos), \
                patch('stats_image_reader.read_images', side_effect=AssertionError('no extraction without explicit request')) as reader:
            app = self.app()
            panel = app.tabs[3]
            previews = panel.get('image')
            self.assertGreaterEqual(len(previews), 2)
            captions = ' '.join(str(preview.proto) for preview in previews)
            self.assertIn('offline-home.png', captions)
            self.assertIn('offline-away.png', captions)
            vision = next(button for button in panel.button if button.key == 'personal_vision')
            self.assertTrue(vision.disabled)
            self.assertFalse(next(widget for widget in panel.checkbox if widget.key == 'personal_confirm').value)
            reader.assert_not_called()
            with sqlite3.connect(self.bill_path) as con:
                tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                for table in ('personal_match_stats', 'personal_match_stats_revisions'):
                    if table in tables:
                        self.assertEqual(con.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], 0)
        self.assert_offline()

    def test_pre_match_history_view_stays_offline_and_preserves_original_bills(self):
        from test_regressions import load_functions
        functions = load_functions(ROOT / 'app.py')
        functions['DB_FILE'] = self.bill_path
        functions['init_db']()
        with functions['db']() as con:
            con.execute('INSERT INTO slips(selections_json,status,profit_units,final_summary) VALUES (?,?,?,?)',
                        ('{damaged old payload', 'แพ้บิล', -1, 'original final result'))
            before = {table: [tuple(row) for row in con.execute('SELECT * FROM ' + table)]
                      for table in ('picks', 'slips', 'odds_snapshots')}
        with patch.dict(os.environ, VALUE_BET_PERSONAL_STATS_PILOT='1'):
            app = self.app()
            mode = next(widget for widget in app.radio if widget.key == 'personal_mode')
            mode.set_value(mode.options[1]).run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual([tab.label for tab in app.tabs], PILOT_TABS)
            self.assertTrue(any(widget.key == 'personal_team_filter' for widget in app.tabs[3].text_input))
        with functions['db']() as con:
            after = {table: [tuple(row) for row in con.execute('SELECT * FROM ' + table)] for table in before}
            self.assertEqual(before, after)
        self.assert_offline()

    def fill_identity(self, app):
        values = {'personal_match_key': 'offline-reviewed-game',
                  'personal_kickoff': (datetime.now(timezone.utc)-timedelta(days=2)).isoformat(),
                  'personal_home': 'Arsenal', 'personal_away': 'Chelsea',
                  'personal_source': 'owner-provided offline fixture',
                  'personal_league': 'soccer_epl', 'personal_season': '2627'}
        for widget in app.tabs[3].text_input:
            if widget.key in values:
                widget.set_value(values[widget.key])
        for widget in app.tabs[3].selectbox:
            if widget.key == 'personal_league':
                widget.set_value('soccer_epl')
        return app

    def test_unchecked_review_cannot_submit_even_valid_manual_fields(self):
        with patch.dict(os.environ, VALUE_BET_PERSONAL_STATS_PILOT='true'), \
                patch('personal_stats.save_verified') as save:
            app = self.fill_identity(self.app())
            next(widget for widget in app.text_area if widget.key == 'personal_raw_stats').set_value(
                json.dumps({'xG': {'home': 2.7, 'away': .74}}))
            next(button for button in app.tabs[3].button if button.label == 'บันทึกสถิติที่ตรวจแล้ว').click().run()
            self.assertEqual(len(app.exception), 0)
            save.assert_not_called()
            self.assertTrue(any('ยืนยัน' in item.value or 'ตรวจ' in item.value for item in app.tabs[3].error))
        self.assert_offline()

    def test_explicit_extraction_is_draft_until_copied_edited_and_human_confirmed(self):
        photos = [synthetic_image('draft-1.png'), synthetic_image('draft-2.png', 'blue')]
        draft = dict(home_team=None, away_team=None, home_goals=None, away_goals=None,
                     stats={'xG': {'home': 2.7, 'away': .74}, 'shots': {'home': 21, 'away': 10},
                            'shots_on_target': {'home': 10, 'away': 3}},
                     warnings=['offline draft: compare every value with image'], verified=False,
                     review_required=True, data_quality={'status': 'needs_review', 'has_conflicts': False, 'missing_fields': []})
        from stats_image_reader import prepare_images
        prepared = prepare_images(photos)
        draft['extraction'] = {'provider': 'offline mock', 'batch_fingerprint': prepared['batch_fingerprint'],
                               'images': prepared['images'], 'received_at': prepared['received_at']}
        with patch.dict(os.environ, VALUE_BET_PERSONAL_STATS_PILOT='true', OPENAI_API_KEY='offline-placeholder-not-a-real-key'), \
                patch('streamlit.file_uploader', return_value=photos), \
                patch('stats_image_reader.read_images', return_value=draft) as reader, \
                patch('personal_stats.save_verified') as save:
            app = self.app()
            reader.assert_not_called()
            next(button for button in app.tabs[3].button if button.key == 'personal_vision').click().run()
            self.assertEqual(len(app.exception), 0)
            reader.assert_called_once()
            self.assertIs(reader.call_args.kwargs.get('user_requested'), True)
            self.assertTrue(any('ร่าง' in item.value or 'ตรวจ' in item.value for item in app.tabs[3].warning))
            save.assert_not_called()
            next(button for button in app.tabs[3].button if button.key == 'personal_use_extraction').click().run()
            self.assertEqual(len(app.exception), 0)
            copied = next(widget for widget in app.text_area if widget.key == 'personal_raw_stats').value
            self.assertEqual(json.loads(copied), draft['stats'])
            save.assert_not_called()
            self.fill_identity(app)
            # Human changes an extracted value before confirming: the reviewed
            # value, rather than the AI draft, must reach the storage boundary.
            edited = dict(draft['stats'], xG={'home': 2.5, 'away': .74})
            next(widget for widget in app.text_area if widget.key == 'personal_raw_stats').set_value(json.dumps(edited))
            next(widget for widget in app.checkbox if widget.key == 'personal_confirm').check()
            next(button for button in app.tabs[3].button if button.label == 'บันทึกสถิติที่ตรวจแล้ว').click().run()
            self.assertEqual(len(app.exception), 0)
            save.assert_called_once()
            self.assertEqual(save.call_args.kwargs['stats'], edited)
            self.assertIs(save.call_args.kwargs.get('reviewed'), True)
            self.assertIs(save.call_args.kwargs.get('completed'), True)
            self.assertEqual(save.call_args.kwargs['match_key'], 'offline-reviewed-game')
            reader.assert_called_once()
        self.assert_offline()

    def test_new_image_batch_invalidates_previous_extraction_without_another_request(self):
        first = [synthetic_image('batch-1.png')]
        replacement = [synthetic_image('batch-2.png', 'blue')]
        draft = dict(home_team=None, away_team=None, home_goals=None, away_goals=None,
                     stats={'xG': {'home': 2.7, 'away': .74}}, warnings=[],
                     verified=False, review_required=True, data_quality={'status': 'needs_review'})
        from stats_image_reader import prepare_images
        prepared = prepare_images(first)
        draft['extraction'] = {'provider': 'offline mock', 'batch_fingerprint': prepared['batch_fingerprint'],
                               'images': prepared['images'], 'received_at': prepared['received_at']}
        with patch.dict(os.environ, VALUE_BET_PERSONAL_STATS_PILOT='true', OPENAI_API_KEY='offline-placeholder-not-a-real-key'), \
                patch('streamlit.file_uploader', return_value=first) as uploads, \
                patch('stats_image_reader.read_images', return_value=draft) as reader:
            app = self.app()
            next(button for button in app.tabs[3].button if button.key == 'personal_vision').click().run()
            self.assertTrue(any(button.key == 'personal_use_extraction' for button in app.tabs[3].button))
            uploads.return_value = replacement
            app.run()
            self.assertEqual(len(app.exception), 0)
            self.assertFalse(any(button.key == 'personal_use_extraction' for button in app.tabs[3].button))
            reader.assert_called_once()
        self.assert_offline()

    def test_seventh_image_rejects_the_batch_instead_of_silently_dropping_evidence(self):
        photos = [synthetic_image(f'image-{index}.png') for index in range(7)]
        with patch.dict(os.environ, VALUE_BET_PERSONAL_STATS_PILOT='true', OPENAI_API_KEY='offline-placeholder-not-a-real-key'), \
                patch('streamlit.file_uploader', return_value=photos), \
                patch('stats_image_reader.read_images') as reader:
            app = self.app()
            self.assertTrue(any('6' in item.value for item in app.tabs[3].error))
            self.assertEqual(len(app.tabs[3].get('image')), 0)
            self.assertFalse(any(button.key == 'personal_vision' for button in app.tabs[3].button))
            reader.assert_not_called()
        self.assert_offline()

    def test_reader_error_keeps_unsaved_draft_and_hides_provider_key_text(self):
        photos = [synthetic_image('reader-error.png')]
        secret = 'apiKey=must-never-render-offline-fixture'
        with patch.dict(os.environ, VALUE_BET_PERSONAL_STATS_PILOT='true', OPENAI_API_KEY='offline-placeholder-not-a-real-key'), \
                patch('streamlit.file_uploader', return_value=photos), \
                patch('stats_image_reader.read_images', side_effect=ValueError(secret)) as reader, \
                patch('personal_stats.save_verified') as save:
            app = self.app()
            next(button for button in app.tabs[3].button if button.key == 'personal_vision').click().run()
            self.assertEqual(len(app.exception), 0)
            self.assertTrue(any('อ่านภาพไม่สำเร็จ' in item.value for item in app.tabs[3].error))
            self.assertFalse(any(secret in item.value for item in app.tabs[3].error))
            self.assertFalse(any(button.key == 'personal_use_extraction' for button in app.tabs[3].button))
            reader.assert_called_once()
            save.assert_not_called()
        self.assert_offline()


if __name__ == '__main__':
    unittest.main()
