"""Actual app boundaries retain cooldowns even with all cache decorators removed."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import pandas as pd
import test_regressions as regression
from api_football_data import api_football_status_text

UTC = timezone.utc
KEY = 'synthetic-issue15-provider-backoff-key'


class ApiFootballBackoffIntegrationTests(unittest.TestCase):
    setUp = regression.Regressions.setUp

    def configure(self, *, http=200, plan=True):
        self.now = datetime.now(UTC)
        payload = {'errors': {'requests': 'Free plans do not have access to this season ' + KEY}, 'response': []} if plan else {'response': []}
        self.get = Mock(return_value=SimpleNamespace(status_code=http, json=Mock(return_value=payload)))
        self.f.update(requests=SimpleNamespace(get=self.get), HISTORY_TIMEOUT=(1, 1))
        key_patch = patch.dict(os.environ, {'API_FOOTBALL_KEY': KEY,
            'VALUE_BET_API_FOOTBALL_ENABLED': '1',
            'VALUE_BET_API_FOOTBALL_BACKOFF_REVISION': 'test-baseline',
            'VALUE_BET_API_FOOTBALL_PLAN_BACKOFF_SECONDS': '86400',
            'VALUE_BET_API_FOOTBALL_AUTH_BACKOFF_SECONDS': '86400',
            'VALUE_BET_API_FOOTBALL_QUOTA_BACKOFF_SECONDS': '3600',
            'VALUE_BET_API_FOOTBALL_FAILURE_BACKOFF_SECONDS': '900',
            'VALUE_BET_API_FOOTBALL_LEASE_SECONDS': '60'})
        key_patch.start()
        self.addCleanup(key_patch.stop)

    def fetch(self):
        return self.f['fetch_api_football_with_diagnostics']('soccer_epl', 2026, KEY)

    def fallback(self):
        return self.f['optional_api_football_fallback']('soccer_epl', pd.DataFrame(), [
            {'home_team': 'Arsenal', 'away_team': 'Chelsea',
             'commence_time': (self.now + timedelta(hours=3)).isoformat()}], self.now)

    def test_repeated_http200_plan_failure_calls_provider_once_after_cache_clear(self):
        self.configure()
        first_frame, first = self.fetch()
        self.assertTrue(first_frame.empty)
        self.assertEqual(first['state'], 'plan_unsupported')
        for _ in range(3):
            frame, blocked = self.fetch()  # extracted real functions have no cache decorator
            self.assertTrue(frame.empty)
            self.assertEqual(blocked['state'], 'not_called')
            self.assertEqual(blocked['provider_state'], 'plan_unsupported')
            self.assertEqual(blocked['reason'], 'plan_backoff')
            self.assertFalse(blocked['request_attempted'])
            self.assertEqual(blocked['received_at'], first['received_at'])
            expected = datetime.fromisoformat(first['received_at']) + timedelta(days=1)
            self.assertEqual(datetime.fromisoformat(blocked['next_retry_at']), expected)
            self.assertNotIn(KEY, json.dumps(blocked))
        self.get.assert_called_once()

    def test_fallback_retains_original_plan_state_in_backoff_and_visible_reason(self):
        self.configure()
        first = self.fallback().attrs['api_football_diagnostics']
        self.assertEqual(first['state'], 'plan_unsupported')
        second = self.fallback().attrs['api_football_diagnostics']
        self.assertEqual(second['state'], 'not_called')
        self.assertEqual(second['provider_state'], 'plan_unsupported')
        self.assertEqual(second['reason'], 'plan_backoff')
        self.assertEqual(second['received_at'], first['received_at'])
        self.assertFalse(second['used_for_form'])
        text = api_football_status_text(second)
        self.assertIn('แพ็กเกจไม่รองรับ', text)
        self.assertIn('ไม่ได้เรียก', text)
        self.assertIn('ลองใหม่ได้ UTC', text)
        self.get.assert_called_once()

    def test_owner_switch_zero_prevents_all_requests_and_ledger_creation(self):
        self.configure()
        with patch.dict(os.environ, {'VALUE_BET_API_FOOTBALL_ENABLED': '0'}):
            _, report = self.fetch()
            self.assertEqual(report['reason'], 'disabled_by_owner')
            self.assertFalse(report['request_attempted'])
            fallback = self.fallback()
            self.assertEqual(fallback.attrs['api_football_diagnostics']['reason'], 'disabled_by_owner')
        self.get.assert_not_called()
        self.assertFalse(Path(os.environ['VALUE_BET_API_FOOTBALL_LEDGER']).exists())

    def test_owner_cloud_secret_false_prevents_request(self):
        self.configure()
        self.f['st'].secrets['VALUE_BET_API_FOOTBALL_ENABLED'] = False
        with patch.dict(os.environ, {'VALUE_BET_API_FOOTBALL_ENABLED': ''}):
            _, report = self.fetch()
        self.assertEqual(report['reason'], 'disabled_by_owner')
        self.assertFalse(report['request_attempted'])
        self.get.assert_not_called()

    def test_foreign_bill_fixture_as_ledger_is_refused_without_write_or_api(self):
        self.configure()
        foreign = Path(self.tmp.name) / 'foreign-bill-fixture.db'
        with sqlite3.connect(foreign) as connection:
            connection.execute('CREATE TABLE slips(id INTEGER PRIMARY KEY, status TEXT)')
            connection.execute("INSERT INTO slips VALUES (1, 'original settled result')")
        checksum = hashlib.sha256(foreign.read_bytes()).hexdigest()
        with patch.dict(os.environ, {'VALUE_BET_API_FOOTBALL_LEDGER': str(foreign)}):
            frame, report = self.fetch()
        self.assertTrue(frame.empty)
        self.assertEqual(report['reason'], 'backoff_store_unavailable')
        self.assertFalse(report['request_attempted'])
        self.assertEqual(hashlib.sha256(foreign.read_bytes()).hexdigest(), checksum)
        self.get.assert_not_called()

    def test_http429_is_not_repeated_after_failure_cache_cleared(self):
        self.configure(http=429, plan=False)
        _, first = self.fetch()
        self.assertEqual(first['http_status'], 429)
        _, blocked = self.fetch()
        self.assertEqual(blocked['reason'], 'quota_backoff')
        self.assertEqual(blocked['http_status'], 429)
        self.assertFalse(blocked['request_attempted'])
        self.assertEqual(datetime.fromisoformat(blocked['next_retry_at']),
                         datetime.fromisoformat(first['received_at']) + timedelta(hours=1))
        self.get.assert_called_once()

    def test_persistence_failure_is_visible_and_preserves_validated_evidence(self):
        self.configure(plan=False)
        candidate = pd.DataFrame([dict(Date_dt=pd.Timestamp('2026-10-01'),
            HomeTeam='Arsenal', AwayTeam='Chelsea', FTHG=2, FTAG=1)])
        diagnostic = {'status': 'ok', 'state': 'success', 'usable_rows': 1,
                      'received_at': self.now.isoformat()}
        with patch('api_football_data.fetch_finished_fixtures', return_value=(candidate, diagnostic)), \
                patch('api_football_backoff.complete_request', return_value=False):
            returned, report = self.fetch()
        self.assertIs(returned, candidate)
        self.assertEqual(report['state'], 'success')
        self.assertEqual(report['usable_rows'], 1)
        self.assertEqual(report['received_at'], diagnostic['received_at'])
        self.assertEqual(report['reason'], 'backoff_store_unavailable')
        self.assertTrue(report['backoff_persistence_failed'])
        self.assertEqual(report['retry_safety'], 'not_persisted')
        self.get.assert_not_called()

    def test_only_explicit_manual_revision_can_retry_before_backoff_expiry(self):
        self.configure()
        self.fetch()
        self.fetch()
        self.assertEqual(self.get.call_count, 1)
        with patch.dict(os.environ, {'VALUE_BET_API_FOOTBALL_BACKOFF_REVISION': 'owner-changed-confirmed-access'}):
            _, report = self.fetch()
            self.assertEqual(report['state'], 'plan_unsupported')
        self.assertEqual(self.get.call_count, 2)
        _, blocked = self.fetch()
        self.assertEqual(blocked['reason'], 'plan_backoff')
        self.assertEqual(self.get.call_count, 2)


if __name__ == '__main__':
    unittest.main()
