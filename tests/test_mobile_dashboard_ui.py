"""Mobile presentation acceptance on authored facts, never live football evidence.

Screenshots are artifacts of the asserted DOM contract. They are not a claim of
physical Safari testing, model accuracy, or a self-updating golden baseline.
"""
from copy import deepcopy
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import struct
import socket
import subprocess
import tempfile
import time
import urllib.request
import sys
import unittest
from unittest.mock import Mock, patch

import test_data_center_ui as original_ui

ROOT = Path(__file__).resolve().parents[1]
WIDTHS = (320, 375, 390, 430)


def authored_cards(*, analysis=False, hostile=False):
    card = deepcopy(original_ui.synthetic_card())
    for quote in card['quotes']:
        quote['received_at'] = card['received_at']
    missing = dict(deepcopy(card), event_id='authored-missing-event',
                   home_team='No Statistics Home', away_team='No Statistics Away',
                   statistics=None, quotes=[], data_status='no_prices')
    rows = []
    if analysis:
        for quote in card['quotes']:
            rows.append({**{key: card[key] for key in ('event_id', 'home_team', 'away_team',
                         'league', 'kickoff_utc')}, **quote,
                'analysis_a_json': json.dumps({'score': 61, 'result': 'หลักฐานฟอร์มสนับสนุน',
                    'status': 'คะแนนหลักฐานจากข้อมูลทดสอบ ไม่ใช่ความน่าจะเป็น'}),
                'analysis_b_json': json.dumps({'available': True, 'selection': {'ev_pct': 4.2},
                    'hda': {'home': 45, 'draw': 25, 'away': 30},
                    'status': 'ค่าที่ผู้เขียน fixture กำหนด ไม่ใช่ผลโมเดลจริง', 'calibrated': False}),
                'recommendation': 'ผล C ที่บันทึกใน fixture',
                'analysis_reason': 'เหตุผลที่บันทึกไว้ — ไม่ใช่ผลวิเคราะห์การแข่งขันจริง'})
        from data_center_presentation import project_cards
        card, missing = project_cards([card, missing], rows)
    if hostile:
        payload = '<img src=x onerror="window.PWNED=1"><script>window.PWNED=1</script>'
        card['home_team'] = payload + 'LONGTEAM' * 24
        card['league'] = payload
        for quote in card['quotes']:
            quote['bookmaker'] = payload + 'LONGBOOK' * 15
        card['statistics']['limitations'].append(payload)
    return [card, missing]


class MobileDashboardPresentationTests(unittest.TestCase):
    def test_summary_counts_actual_fixtures_not_quote_legs_or_loaded_flags(self):
        from data_center_presentation import summary_counts
        cards = authored_cards()
        no_samples = deepcopy(cards[1])
        no_samples.update(event_id='loaded-but-empty', statistics={
            'status': 'complete', 'teams': {}, 'h2h': {'sample': 0, 'matches': []}})
        self.assertEqual(summary_counts([*cards, deepcopy(cards[0]), no_samples]),
                         {'fixtures': 3, 'priced': 1, 'statistics': 1})
        self.assertEqual(summary_counts([]), {'fixtures': 0, 'priced': 0, 'statistics': 0})

    def test_summary_and_match_cards_render_without_actions_or_mutations(self):
        from data_center_ui import render_center
        cards = authored_cards()
        before = deepcopy(cards)
        st = original_ui.FakeStreamlit()
        load, analyze = Mock(), Mock()
        with patch.dict(sys.modules, {'streamlit': st}), \
                patch('requests.get', side_effect=AssertionError('UI cannot fetch')) as request:
            render_center(cards, None, None, on_load_statistics=load, on_analyze=analyze)
        markup = [(i, args[0]) for i, (name, args, _) in enumerate(st.events)
                  if name == 'markdown']
        summary = next(i for i, text in markup if 'data-dc-count="fixtures"' in text)
        first_card = next(i for i, text in markup if 'data-center-fixture=' in text)
        self.assertLess(summary, first_card)
        self.assertEqual(cards, before)
        request.assert_not_called()
        load.assert_not_called()
        analyze.assert_not_called()
        st.rerun.assert_not_called()

    def test_empty_dashboard_preserves_zero_summary_and_provider_status(self):
        from data_center_ui import render_center
        st = original_ui.FakeStreamlit()
        with patch.dict(sys.modules, {'streamlit': st}):
            render_center([], None, None)
        markup = '\n'.join(args[0] for name, args, _ in st.events if name == 'markdown')
        for key in ('fixtures', 'priced', 'statistics'):
            self.assertIn('data-dc-count="' + key + '"', markup)
        self.assertNotIn('ไม่มีโปรแกรมแข่งขัน', str(st.events))

    def test_dashboard_does_not_turn_evidence_into_calibrated_probability(self):
        from data_center_ui import data_center_card_html
        with_analysis, without = authored_cards(analysis=True)
        html = data_center_card_html(with_analysis)
        self.assertIn('คะแนนหลักฐาน A', html)
        self.assertIn('61', html)
        self.assertIn('ระบบ B', html)
        self.assertIn('ระบบ C', html)
        self.assertIn('ไม่ใช่ความน่าจะเป็นชนะที่ผ่านการปรับเทียบ', html)
        self.assertIn('ผล C ที่บันทึกใน fixture', html)
        absent = data_center_card_html(without)
        self.assertNotIn('61', absent)
        self.assertNotIn('4.20', absent)
        self.assertNotIn('ผล C ที่บันทึกใน fixture', absent)
        self.assertIn('ไม่มีข้อมูล', absent)


@unittest.skipUnless(importlib.util.find_spec('playwright'), 'Install Playwright for screenshots')
class MobileDashboardScreenshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import test_dashboard_browser as browser
        browser.DashboardBrowserTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.runtime.stop()

    def make_page(self, width, dark=False, *, analysis=False, hostile=False):
        from data_center_ui import DATA_CENTER_CSS, dashboard_summary_html, data_center_card_html
        cards = authored_cards(analysis=analysis, hostile=hostile)
        page = self.browser.new_page(viewport={'width': width, 'height': 844},
                                     color_scheme='dark' if dark else 'light')
        requests = []
        page.on('request', lambda request: requests.append(request.url))
        page.route('**/*', lambda route: route.abort())
        colors = ('--background-color:#212121;--secondary-background-color:#2f2f2f;--text-color:#ececec'
                  if dark else '--background-color:#fff;--secondary-background-color:#f7f7f8;--text-color:#202123')
        content = ('<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">'
            + DATA_CENTER_CSS + '<style>body{margin:0;padding:12px;font-family:Arial,sans-serif;'
            + colors + ';background:var(--background-color)}</style></head><body>'
            + dashboard_summary_html(cards)
            + ''.join(data_center_card_html(card, instance=index) for index, card in enumerate(cards))
            + '</body></html>')
        page.set_content(content)
        page.evaluate('document.fonts.ready')
        return page, requests

    def assert_fits(self, page):
        self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
        for card in page.locator('article.dc-card').all():
            box = card.bounding_box()
            self.assertGreater(box['width'], 0)
            self.assertGreaterEqual(box['x'], 0)
            self.assertLessEqual(box['x'] + box['width'], page.viewport_size['width'])

    def save_image(self, name, image):
        directory = os.environ.get('DASHBOARD_SCREENSHOTS')
        if directory:
            folder = Path(directory)
            folder.mkdir(parents=True, exist_ok=True)
            (folder / name).write_bytes(image)

    def assert_host_theme_and_contrast(self, page, *, dark):
        measurements = page.evaluate('''() => {
            const rgba = value => value.match(/[\\d.]+/g).map(Number);
            const linear = value => {
                value /= 255;
                return value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4;
            };
            const luminance = color => .2126 * linear(color[0]) + .7152 * linear(color[1]) + .0722 * linear(color[2]);
            const background = element => {
                const parents = [];
                for (let item = element; item; item = item.parentElement) parents.unshift(item);
                let color = [255, 255, 255];
                for (const item of parents) {
                    const value = rgba(getComputedStyle(item).backgroundColor);
                    const alpha = value.length > 3 ? value[3] : 1;
                    color = color.map((channel, index) => value[index] * alpha + channel * (1 - alpha));
                }
                return color;
            };
            const host = getComputedStyle(document.querySelector('[data-dc-theme-probe]')).color;
            return Array.from(document.querySelectorAll('article.dc-card,.dc-summary')).map(element => {
                const text = getComputedStyle(element).color;
                const foreground = luminance(rgba(text));
                const surface = luminance(background(element));
                return {host, text, foreground, surface,
                    contrast: (Math.max(foreground, surface) + .05) / (Math.min(foreground, surface) + .05)};
            });
        }''')
        self.assertGreaterEqual(len(measurements), 2)
        for value in measurements:
            self.assertEqual(value['text'], value['host'], value)
            self.assertGreaterEqual(value['contrast'], 4.5, value)
            if dark:
                self.assertGreater(value['foreground'], .5, value)
                self.assertLess(value['surface'], .2, value)
            else:
                self.assertLess(value['foreground'], .2, value)
                self.assertGreater(value['surface'], .5, value)

    def test_host_theme_without_css_variables_overrides_opposite_operating_system_theme(self):
        """Streamlit does not promise the custom CSS variables used by static fixtures."""
        from data_center_ui import DATA_CENTER_CSS, dashboard_summary_html, data_center_card_html
        cards = authored_cards()
        for dark in (False, True):
            with self.subTest(host_dark=dark):
                page = self.browser.new_page(viewport={'width': 320, 'height': 844},
                    color_scheme='light' if dark else 'dark')
                try:
                    text, surface = ('#ececec', '#212121') if dark else ('#202123', '#ffffff')
                    page.set_content('<html><head>' + DATA_CENTER_CSS + '</head><body style="margin:0;'
                        'padding:12px;font-family:Arial,sans-serif;color:' + text + ';background:' + surface + '">'
                        '<p data-dc-theme-probe>Authored host theme</p>' + dashboard_summary_html(cards)
                        + ''.join(data_center_card_html(card, instance=index) for index, card in enumerate(cards))
                        + '</body></html>')
                    self.assertEqual(page.evaluate("getComputedStyle(document.body).getPropertyValue('--text-color')"), '')
                    self.assert_host_theme_and_contrast(page, dark=dark)
                    self.assert_fits(page)
                    self.save_image('mobile-dashboard-no-css-variables-' + ('dark' if dark else 'light') + '.png',
                                    page.screenshot(animations='disabled', full_page=True))
                finally:
                    page.close()

    def serve_isolated_theme_fixture(self, theme):
        """Serve only the production presentation module under the real Streamlit theme."""
        directory = tempfile.TemporaryDirectory(prefix='value-bet-ui-theme-')
        self.addCleanup(directory.cleanup)
        folder = Path(directory.name)
        (folder / 'cards.json').write_text(json.dumps(authored_cards(), ensure_ascii=False), encoding='utf-8')
        script = folder / 'dashboard.py'
        script.write_text('''import json, sys
from pathlib import Path
import streamlit as st
sys.path.insert(0, ROOT_PATH)
from data_center_ui import DATA_CENTER_CSS, dashboard_summary_html, data_center_card_html
st.set_page_config(page_title='Authored Dashboard Theme Test', layout='wide')
cards = json.loads(Path(__file__).with_name('cards.json').read_text())
st.markdown('<p data-dc-theme-probe>Streamlit host theme</p>', unsafe_allow_html=True)
st.markdown(DATA_CENTER_CSS + dashboard_summary_html(cards), unsafe_allow_html=True)
for index, card in enumerate(cards):
    st.markdown(data_center_card_html(card, instance=index), unsafe_allow_html=True)
'''.replace('ROOT_PATH', repr(str(ROOT))), encoding='utf-8')
        marker = folder / 'provider-call-attempt.txt'
        log = folder / 'server.log'
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        wrapper = '''import pathlib, requests, sys
marker = pathlib.Path(sys.argv[1])
def deny(*args, **kwargs):
    marker.write_text('provider call attempted')
    raise AssertionError('provider calls forbidden in theme UI test')
requests.sessions.Session.request = deny
from streamlit.web import cli
sys.argv = ['streamlit', 'run', sys.argv[2], '--server.address=127.0.0.1',
    '--server.port=' + sys.argv[3], '--theme.base=' + sys.argv[4],
    '--server.headless=true', '--browser.gatherUsageStats=false']
sys.exit(cli.main())
'''
        env = dict(os.environ, THE_ODDS_API_KEY='', API_FOOTBALL_KEY='',
                   STREAMLIT_BROWSER_GATHER_USAGE_STATS='false')
        with log.open('w') as output:
            process = subprocess.Popen([sys.executable, '-c', wrapper, str(marker), str(script), str(port), theme],
                cwd=ROOT, env=env, stdout=output, stderr=subprocess.STDOUT)
        def stop():
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=10)
        self.addCleanup(stop)
        address = f'http://127.0.0.1:{port}'
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if process.poll() is not None:
                self.fail('Guarded theme server exited: ' + log.read_text()[-2500:])
            try:
                with urllib.request.urlopen(address + '/_stcore/health', timeout=1) as response:
                    if response.status == 200:
                        return address, marker, log
            except (OSError, TimeoutError):
                time.sleep(.1)
        self.fail('Guarded theme server did not start')

    def test_real_streamlit_forced_theme_remains_readable_with_opposite_os_theme(self):
        for theme in ('light', 'dark'):
            with self.subTest(streamlit_theme=theme):
                address, marker, log = self.serve_isolated_theme_fixture(theme)
                page = self.browser.new_page(viewport={'width': 375, 'height': 844},
                    color_scheme='light' if theme == 'dark' else 'dark')
                external = []
                def allow_local(route):
                    if route.request.url.startswith(address):
                        route.continue_()
                    else:
                        external.append(route.request.url)
                        route.abort()
                page.route('**/*', allow_local)
                try:
                    page.goto(address, wait_until='domcontentloaded')
                    page.locator('article.dc-card').first.wait_for(timeout=30000)
                    self.assert_host_theme_and_contrast(page, dark=theme == 'dark')
                    self.assert_fits(page)
                    self.assertEqual(external, [])
                    self.assertFalse(marker.exists(), 'Theme UI attempted a provider request')
                    self.save_image(f'mobile-dashboard-streamlit-forced-{theme}-opposite-os.png',
                                    page.screenshot(animations='disabled', full_page=True))
                except Exception:
                    self.save_image(f'mobile-dashboard-streamlit-theme-{theme}-failure.png',
                                    page.screenshot(animations='disabled', full_page=True))
                    directory = os.environ.get('DASHBOARD_SCREENSHOTS')
                    if directory:
                        (Path(directory) / f'mobile-dashboard-streamlit-theme-{theme}-server.log').write_text(
                            log.read_text(), encoding='utf-8')
                    raise
                finally:
                    page.close()

    def test_layout_and_screenshots_at_all_iphone_widths_and_themes(self):
        manifest = []
        for width in WIDTHS:
            for dark in (False, True):
                with self.subTest(width=width, dark=dark):
                    page, requests = self.make_page(width, dark, analysis=True)
                    try:
                        self.assert_fits(page)
                        self.assertEqual(page.locator('article.dc-card').count(), 2)
                        for key, expected in (('fixtures', '2'), ('priced', '1'), ('statistics', '1')):
                            self.assertEqual(page.locator(f'[data-dc-count="{key}"] strong').inner_text(), expected)
                        tiles = page.locator('.dc-team-overview .dc-team-tile')
                        self.assertEqual(tiles.count(), 2)
                        self.assertEqual(tiles.nth(0).bounding_box()['y'], tiles.nth(1).bounding_box()['y'])
                        self.assertTrue(page.locator('details').evaluate_all('(items)=>items.every(item=>!item.open)'))
                        card = page.locator('article.dc-card').first
                        self.assertTrue(card.locator('.dc-panel-ah').is_visible())
                        self.assertFalse(card.locator('.dc-panel-ou').is_visible())
                        before = page.screenshot(animations='disabled', full_page=True)
                        self.assertEqual(before, page.screenshot(animations='disabled', full_page=True))
                        self.assertEqual(struct.unpack('>II', before[16:24])[0], width)
                        card.locator('[data-dc-tab="ou"]').click()
                        self.assertTrue(card.locator('.dc-panel-ou').is_visible())
                        self.assertFalse(card.locator('.dc-panel-ah').is_visible())
                        self.assert_fits(page)
                        after = page.screenshot(animations='disabled', full_page=True)
                        self.assertNotEqual(sha256(before).hexdigest(), sha256(after).hexdigest())
                        theme = 'dark' if dark else 'light'
                        for state, image in (('ah', before), ('ou', after)):
                            filename = f'mobile-dashboard-{width}-{theme}-{state}.png'
                            self.save_image(filename, image)
                            manifest.append({'name': filename, 'width': width, 'theme': theme,
                                'market': state, 'sha256': sha256(image).hexdigest(),
                                'evidence': 'authored_fixture_browser_render_not_live'})
                        self.assertEqual(requests, [])
                    finally:
                        page.close()
        directory = os.environ.get('DASHBOARD_SCREENSHOTS')
        if directory:
            (Path(directory) / 'mobile-dashboard-screenshots.json').write_text(
                json.dumps({'schema_version': 1, 'physical_safari_verified': False,
                    'live_end_to_end_verified': False, 'screenshots': manifest}, indent=2), encoding='utf-8')

    def test_market_controls_are_independent_accessible_and_do_not_request(self):
        page, requests = self.make_page(320)
        try:
            cards = page.locator('article.dc-card')
            ids = page.locator('input[type="radio"]').evaluate_all('(items)=>items.map(item=>item.id)')
            self.assertEqual(len(ids), 4)
            self.assertEqual(len(set(ids)), 4)
            for label in page.locator('[data-dc-tab]').all():
                self.assertGreaterEqual(label.bounding_box()['height'], 44)
            cards.first.locator('[data-dc-tab="ou"]').click()
            self.assertTrue(cards.first.locator('.dc-tab-ou').is_checked())
            self.assertTrue(cards.nth(1).locator('.dc-panel-ah').is_visible())
            self.assertFalse(cards.nth(1).locator('.dc-panel-ou').is_visible())
            self.assertIn('ไม่มีราคาตลาด', cards.nth(1).locator('.dc-panel-ah').inner_text())
            cards.first.locator('.dc-tab-ou').focus()
            page.keyboard.press('ArrowLeft')
            self.assertTrue(cards.first.locator('.dc-tab-ah').is_checked())
            self.assertTrue(cards.first.locator('.dc-panel-ah').is_visible())
            self.assertEqual(requests, [])
        finally:
            page.close()

    def test_expanded_details_and_untrusted_api_text_remain_inside_viewport(self):
        for width in (320, 430):
            for dark in (False, True):
                with self.subTest(width=width, dark=dark):
                    page, requests = self.make_page(width, dark, analysis=True, hostile=True)
                    try:
                        self.assertEqual(page.locator('img,script').count(), 0)
                        self.assertIsNone(page.evaluate('window.PWNED'))
                        self.assert_fits(page)
                        page.locator('details').evaluate_all('(items)=>items.forEach(item=>item.open=true)')
                        self.assert_fits(page)
                        text = page.locator('body').inner_text()
                        for value in ('H2H', 'xG', 'Football-Data', '2026-10-25T12:00:00Z',
                                      '1.80 / 0.80', 'ผลย้อนหลัง'):
                            self.assertIn(value, text)
                        self.assertEqual(requests, [])
                    finally:
                        page.close()

    def test_native_market_switch_survives_real_streamlit_app_without_provider_calls(self):
        """React/Streamlit must preserve the HTML inputs, not just static Chromium."""
        from playwright.sync_api import expect
        import test_data_center_mobile as existing_mobile
        helper = existing_mobile.DataCenterStreamlitTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        helper.seed()
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        marker = Path(helper.tmp.name) / 'blocked-provider-call.txt'
        log = Path(helper.tmp.name) / 'streamlit-browser.log'
        wrapper = '''import pathlib, requests, sys
marker = pathlib.Path(sys.argv[1])
def deny(*args, **kwargs):
    marker.write_text('provider call attempted')
    raise AssertionError('provider calls forbidden in UI browser test')
requests.sessions.Session.request = deny
from streamlit.web import cli
sys.argv = ['streamlit', 'run', 'app.py', '--server.address=127.0.0.1',
    '--server.port=' + sys.argv[2], '--server.headless=true', '--browser.gatherUsageStats=false']
sys.exit(cli.main())
'''
        env = dict(os.environ, THE_ODDS_API_KEY='', API_FOOTBALL_KEY='',
                   STREAMLIT_BROWSER_GATHER_USAGE_STATS='false',
                   VALUE_BET_DB=str(helper.path),
                   VALUE_BET_API_FOOTBALL_ENABLED='0', VALUE_BET_OBSERVATION_DB='')
        with log.open('w') as output:
            process = subprocess.Popen([sys.executable, '-c', wrapper, str(marker), str(port)],
                cwd=ROOT, env=env, stdout=output, stderr=subprocess.STDOUT)
        def stop():
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=10)
        self.addCleanup(stop)
        address = f'http://127.0.0.1:{port}'
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if process.poll() is not None:
                self.fail('Guarded Streamlit server exited: ' + log.read_text()[-2500:])
            try:
                with urllib.request.urlopen(address + '/_stcore/health', timeout=1) as response:
                    if response.status == 200:
                        break
            except (OSError, TimeoutError):
                time.sleep(.1)
        else:
            self.fail('Guarded Streamlit server did not start')
        page = self.browser.new_page(viewport={'width': 1280, 'height': 900})
        external = []
        def allow_local(route):
            if route.request.url.startswith(address):
                route.continue_()
            else:
                external.append(route.request.url)
                route.abort()
        page.route('**/*', allow_local)
        try:
            page.goto(address, wait_until='domcontentloaded')
            sidebar = page.get_by_test_id('stSidebar')
            selector = sidebar.get_by_role('combobox').first
            selector.wait_for(timeout=30000)
            selector.click()
            page.get_by_role('option', name='พรีเมียร์ลีก อังกฤษ', exact=True).click()
            # A multiselect remains open after choosing one option. BaseUI hides
            # the background from assistive technology while that popup is open.
            # Close it before testing the real accessible navigation contract.
            page.keyboard.press('Escape')
            card = page.locator('article.dc-card').first
            card.wait_for(timeout=30000)
            tabs = page.get_by_role('tab')
            expect(tabs).to_have_count(4, timeout=15000)
            expect(tabs).to_have_text(existing_mobile.TABS, timeout=15000)
            self.assertEqual(card.locator('input[type="radio"]').count(), 2)
            self.assertTrue(card.locator('.dc-tab-ah').is_enabled())
            with helper.f['db']() as con:
                before = [tuple(row) for row in con.execute('SELECT * FROM dc_batches ORDER BY rowid')]
            for width in (320, 430):
                page.set_viewport_size({'width': width, 'height': 844})
                close = page.get_by_test_id('stSidebarCollapseButton').get_by_role('button')
                if close.is_visible():
                    close.click()
                card.locator('[data-dc-tab="ou"]').click()
                self.assertTrue(card.locator('.dc-tab-ou').is_checked())
                self.assertTrue(card.locator('.dc-panel-ou').is_visible())
                self.assertFalse(card.locator('.dc-panel-ah').is_visible())
                self.assert_fits(page)
                self.save_image(f'mobile-dashboard-streamlit-{width}.png',
                                page.screenshot(animations='disabled', full_page=True))
                card.locator('[data-dc-tab="ah"]').click()
                self.assertTrue(card.locator('.dc-panel-ah').is_visible())
            with helper.f['db']() as con:
                after = [tuple(row) for row in con.execute('SELECT * FROM dc_batches ORDER BY rowid')]
            self.assertEqual(before, after)
            self.assertFalse(marker.exists(), 'UI attempted a provider request')
            self.assertEqual(external, [])
        except Exception:
            directory = os.environ.get('DASHBOARD_SCREENSHOTS')
            if directory:
                folder = Path(directory)
                folder.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(folder / 'mobile-dashboard-streamlit-failure.png'),
                                animations='disabled', full_page=True)
                (folder / 'mobile-dashboard-streamlit-failure.html').write_text(
                    page.content(), encoding='utf-8')
                diagnostics = page.evaluate('''() => ({
                    tabs: Array.from(document.querySelectorAll('[role="tab"],[data-testid="stTab"]'))
                        .map(e => ({text:e.textContent, html:e.outerHTML,
                            hidden:e.getAttribute('aria-hidden'), box:e.getBoundingClientRect().toJSON()})),
                    popups: Array.from(document.querySelectorAll('[role="listbox"],[role="dialog"]'))
                        .map(e => ({role:e.getAttribute('role'), hidden:e.getAttribute('aria-hidden')})),
                    cards: document.querySelectorAll('article.dc-card').length,
                    radios: Array.from(document.querySelectorAll('.dc-market-radio'))
                        .map(e => ({id:e.id, checked:e.checked, disabled:e.disabled})),
                    sidebar: document.querySelector('[data-testid="stSidebar"]')?.getAttribute('data-collapsed')
                })''')
                diagnostics['accessible_tab_count'] = page.get_by_role('tab').count()
                diagnostics['provider_call_attempted'] = marker.exists()
                (folder / 'mobile-dashboard-streamlit-failure.json').write_text(
                    json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding='utf-8')
                (folder / 'mobile-dashboard-streamlit-guarded-server.log').write_text(
                    log.read_text(), encoding='utf-8')
            raise
        finally:
            page.close()

    def test_only_ou_defaults_to_actual_available_market_without_making_ah_price(self):
        from data_center_ui import DATA_CENTER_CSS, data_center_card_html
        card = authored_cards()[0]
        card['quotes'] = [quote for quote in card['quotes'] if quote['market'] == 'Over/Under']
        page = self.browser.new_page(viewport={'width': 375, 'height': 844})
        try:
            page.set_content('<html><head>' + DATA_CENTER_CSS + '</head><body>'
                             + data_center_card_html(card) + '</body></html>')
            self.assertTrue(page.locator('.dc-panel-ou').is_visible())
            page.locator('[data-dc-tab="ah"]').click()
            self.assertIn('ไม่มีราคาตลาด', page.locator('.dc-panel-ah').inner_text())
            self.assertNotIn('1.95', page.locator('.dc-panel-ah').inner_text())
        finally:
            page.close()


if __name__ == '__main__':
    unittest.main()
