"""Local Streamlit/Chromium upload checks at iPhone widths, with HTTP blocked."""
from io import BytesIO
import importlib.util
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(importlib.util.find_spec('streamlit') and importlib.util.find_spec('playwright'),
                     'Install Streamlit and Playwright for local browser tests')
class PersonalBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.runtime = sync_playwright().start()
        options = {'headless': True, 'args': ['--no-sandbox']}
        executable = shutil.which('chromium')
        if executable:
            options['executable_path'] = executable
        try:
            cls.browser = cls.runtime.chromium.launch(**options)
        except Exception as exc:
            cls.runtime.stop()
            if os.environ.get('CI'):
                raise
            raise unittest.SkipTest('Local sandbox cannot launch Chromium') from exc
        cls.tmp = tempfile.TemporaryDirectory()
        folder = Path(cls.tmp.name)
        # Only this disposable wrapper forbids requests. The production app is
        # executed unchanged and receives no provider credentials.
        cls.wrapper = folder / 'offline_personal_app.py'
        cls.wrapper.write_text(
            'import requests\n'
            'def forbidden_http(*args, **kwargs):\n'
            '    raise AssertionError("browser fixture forbids provider HTTP")\n'
            'requests.sessions.Session.request = forbidden_http\n'
            f'exec(compile(open({str(ROOT / "app.py")!r}).read(), {str(ROOT / "app.py")!r}, "exec"), globals())\n')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        cls.url = f'http://127.0.0.1:{port}'
        env = os.environ.copy()
        env.update(VALUE_BET_DB=str(folder / 'browser-disposable.db'), VALUE_BET_PERSONAL_STATS_PILOT='true',
                   VALUE_BET_API_LEDGER=str(folder / 'unused-api-ledger.db'),
                   OPENAI_API_KEY='', THE_ODDS_API_KEY='', OPENAI_BASE_URL='',
                   STREAMLIT_BROWSER_GATHER_USAGE_STATS='false',
                   PYTHONPATH=str(ROOT) + os.pathsep + env.get('PYTHONPATH', ''))
        cls.log = (folder / 'streamlit.log').open('w+')
        cls.server = subprocess.Popen(
            [sys.executable, '-m', 'streamlit', 'run', str(cls.wrapper),
             '--server.headless=true', '--server.address=127.0.0.1', f'--server.port={port}',
             '--server.fileWatcherType=none', '--browser.gatherUsageStats=false'],
            cwd=str(ROOT), env=env, stdout=cls.log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if cls.server.poll() is not None:
                    raise RuntimeError('Disposable Streamlit server exited before readiness')
                try:
                    with urlopen(cls.url + '/_stcore/health', timeout=1) as response:
                        if response.status == 200:
                            return
                except OSError:
                    time.sleep(.15)
            raise RuntimeError('Disposable Streamlit server did not become ready')
        except Exception:
            cls.tearDownClass()
            raise

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'server'):
            cls.server.terminate()
            try:
                cls.server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                cls.server.kill()
                cls.server.wait(timeout=5)
        if hasattr(cls, 'log'):
            cls.log.close()
        if hasattr(cls, 'tmp'):
            cls.tmp.cleanup()
        cls.browser.close()
        cls.runtime.stop()

    def image_upload(self, name, color):
        from PIL import Image
        buffer = BytesIO()
        Image.new('RGB', (64, 64), color).save(buffer, format='PNG')
        return {'name': name, 'mimeType': 'image/png', 'buffer': buffer.getvalue()}

    def test_multi_image_preview_and_review_controls_fit_iphone_widths(self):
        from playwright.sync_api import expect
        for width in (320, 375, 390, 430):
            for dark in (False, True):
                with self.subTest(width=width, dark=dark):
                    page = self.browser.new_page(viewport={'width': width, 'height': 844},
                                                 color_scheme='dark' if dark else 'light')
                    outbound = []
                    def local_only(route):
                        if route.request.url.startswith(self.url + '/'):
                            route.continue_()
                        else:
                            outbound.append(route.request.url)
                            route.abort()
                    page.route('**/*', local_only)
                    try:
                        page.goto(self.url, wait_until='domcontentloaded')
                        personal = page.get_by_role('tab', name='📸 คลังสถิติ', exact=True)
                        expect(personal).to_be_visible(timeout=30000)
                        self.assertEqual(page.get_by_role('tab').count(), 5)
                        personal.click()
                        panel = page.get_by_role('tabpanel').nth(3)
                        upload = panel.locator('input[type="file"]')
                        expect(upload).to_have_attribute('multiple', '')
                        upload.set_input_files([self.image_upload('offline-home.png', 'green'),
                                                self.image_upload('offline-away.png', 'blue')])
                        images = panel.locator('[data-testid="stImage"]')
                        expect(images).to_have_count(2)
                        expect(images.filter(has_text='offline-home.png')).to_be_visible()
                        expect(images.filter(has_text='offline-away.png')).to_be_visible()
                        expect(panel.get_by_role('button', name='🔎 อ่านสถิติจากภาพ (ต้องตรวจสอบก่อนบันทึก)', exact=True)).to_be_disabled()
                        expect(panel.get_by_role('checkbox', name='ตรวจตัวเลขและยืนยันว่าเป็นข้อมูลหลังแข่งจริง', exact=True)).not_to_be_checked()
                        self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
                        self.assertEqual(page.locator('[data-testid="stException"]').count(), 0)
                        self.assertFalse(any('api.the-odds-api.com' in url or 'api.openai.com' in url for url in outbound))
                        if width==375 and os.environ.get('DASHBOARD_SCREENSHOTS'):
                            folder=Path(os.environ['DASHBOARD_SCREENSHOTS']);folder.mkdir(parents=True,exist_ok=True)
                            page.screenshot(path=str(folder/('personal-import-dark.png' if dark else 'personal-import-light.png')),full_page=True)
                    finally:
                        page.close()


if __name__ == '__main__':
    unittest.main()
