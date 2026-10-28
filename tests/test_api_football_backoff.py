"""No network: isolated optional-provider backoff and secret-safe persistence."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from api_football_data import api_football_status_text

from api_football_backoff import (
    BackoffPolicy, api_football_enabled, complete_request, policy_from_environment,
    reserve_request,
)

UTC = timezone.utc
KEY = 'synthetic-backoff-key-never-a-real-credential'


class ApiFootballBackoffTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'optional-provider.db'
        self.now = datetime(2026, 10, 24, 0, 0, tzinfo=UTC)
        self.policy = BackoffPolicy()

    def reserve(self, *, now=None, key=KEY, season=2026, sport='soccer_epl', **kwargs):
        return reserve_request(sport, season, key, ledger_path=self.path,
            enabled=True, now=now or self.now, policy=self.policy, **kwargs)

    def report(self, state='plan_unsupported', *, received=None, http=200):
        return {'state': state, 'status': 'provider_error' if state == 'plan_unsupported' else state,
                'received_at': (received or self.now).isoformat(), 'http_status': http,
                'error': KEY, 'provider_body': 'do not persist ' + KEY}

    def complete(self, reservation, state='plan_unsupported', *, received=None, now=None, http=200):
        return complete_request(reservation, self.report(state, received=received, http=http), now=now or self.now)

    def test_explicit_switch_off_has_no_filesystem_side_effect(self):
        decision = reserve_request('soccer_epl', 2026, KEY, enabled=False, ledger_path=self.path, now=self.now)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.diagnostic['reason'], 'disabled_by_owner')
        self.assertFalse(decision.diagnostic['request_attempted'])
        self.assertFalse(self.path.exists())

    def test_environment_switch_default_is_compatible_but_false_is_fail_closed(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(api_football_enabled())
            for value in ('0', 'false', 'off', 'no', '', 'mistyped', None):
                if value is None:
                    continue
                with self.subTest(value=value):
                    self.assertFalse(api_football_enabled(value))
            for value in ('1', 'true', 'yes', 'on', True):
                self.assertTrue(api_football_enabled(value))
        with patch.dict(os.environ, {'VALUE_BET_API_FOOTBALL_ENABLED': '0'}):
            decision = reserve_request('soccer_epl', 2026, KEY, ledger_path=self.path, now=self.now)
            self.assertFalse(decision.allowed)
            self.assertFalse(self.path.exists())

    def test_plan_restriction_blocks_repeated_calls_for_twenty_four_hours(self):
        first = self.reserve()
        self.assertTrue(first.allowed)
        self.assertTrue(self.complete(first.reservation))
        for hours in (0, 1, 6, 23):
            with self.subTest(hours=hours):
                blocked = self.reserve(now=self.now + timedelta(hours=hours))
                self.assertFalse(blocked.allowed)
                self.assertEqual(blocked.diagnostic['reason'], 'plan_backoff')
                self.assertEqual(blocked.diagnostic['provider_state'], 'plan_unsupported')
                self.assertEqual(blocked.diagnostic['received_at'], self.now.isoformat())
                self.assertEqual(blocked.diagnostic['next_retry_at'], (self.now + timedelta(days=1)).isoformat())
        self.assertTrue(self.reserve(now=self.now + timedelta(days=1)).allowed)

    def test_authentication_and_quota_have_different_bounded_backoffs(self):
        for http, expected in ((401, 86400), (403, 86400), (429, 3600), (500, 900)):
            with self.subTest(http=http):
                first = self.reserve(season=2020 + http % 100)
                self.assertTrue(first.allowed)
                self.assertTrue(self.complete(first.reservation, 'http_error', http=http))
                blocked = self.reserve(season=2020 + http % 100, now=self.now + timedelta(seconds=expected - 1))
                self.assertFalse(blocked.allowed)
                self.assertEqual(blocked.diagnostic['http_status'], http)
                self.assertTrue(self.reserve(season=2020 + http % 100, now=self.now + timedelta(seconds=expected)).allowed)

    def test_different_league_season_key_or_manual_revision_has_independent_scope(self):
        first = self.reserve()
        self.assertTrue(self.complete(first.reservation))
        for change in ({'sport': 'soccer_germany_bundesliga'}, {'season': 2025},
                       {'key': KEY + '-changed'}, {'revision': 'owner-approved-new-entitlement'}):
            with self.subTest(change=change):
                self.assertTrue(self.reserve(**change).allowed)
        self.assertFalse(self.reserve().allowed)

    def test_manual_revision_is_hashed_not_stored_as_owner_input(self):
        revision = 'private owner input ' + KEY
        first = self.reserve(revision=revision)
        self.complete(first.reservation)
        data = self.path.read_bytes()
        self.assertNotIn(revision.encode(), data)
        self.assertNotIn(KEY.encode(), data)

    def test_concurrent_reservations_allow_only_one_actual_attempt(self):
        # Initialize schema before racing so this tests atomic lease coordination.
        first = self.reserve()
        self.complete(first.reservation, 'success')
        with ThreadPoolExecutor(max_workers=6) as executor:
            decisions = list(executor.map(lambda _: self.reserve(), range(6)))
        self.assertEqual(sum(decision.allowed for decision in decisions), 1)
        for decision in decisions:
            if not decision.allowed:
                self.assertEqual(decision.diagnostic['reason'], 'request_in_progress')

    def test_crashed_request_lease_expires_after_sixty_seconds(self):
        first = self.reserve()
        self.assertTrue(first.allowed)
        self.assertFalse(self.reserve(now=self.now + timedelta(seconds=59)).allowed)
        self.assertTrue(self.reserve(now=self.now + timedelta(seconds=60)).allowed)

    def test_stale_completion_cannot_clear_newer_request_lease(self):
        first = self.reserve()
        newer = self.reserve(now=self.now + timedelta(seconds=61))
        self.assertTrue(newer.allowed)
        self.assertFalse(self.complete(first.reservation, 'success', now=self.now + timedelta(seconds=62)))
        blocked = self.reserve(now=self.now + timedelta(seconds=63))
        self.assertFalse(blocked.allowed)
        self.assertEqual(blocked.diagnostic['reason'], 'request_in_progress')
        self.assertTrue(self.complete(newer.reservation, now=self.now + timedelta(seconds=64)))
        self.assertEqual(self.reserve(now=self.now + timedelta(seconds=65)).diagnostic['reason'], 'plan_backoff')

    def test_success_releases_lease_without_inventing_backoff(self):
        first = self.reserve()
        self.assertTrue(self.complete(first.reservation, 'success'))
        self.assertTrue(self.reserve().allowed)

    def test_partial_success_uses_short_failure_backoff(self):
        first = self.reserve()
        report = self.report('success')
        report['rejected_rows'] = 2
        self.assertTrue(complete_request(first.reservation, report, now=self.now))
        blocked = self.reserve(now=self.now + timedelta(seconds=899))
        self.assertFalse(blocked.allowed)
        self.assertEqual(blocked.diagnostic['provider_state'], 'partial')
        self.assertTrue(self.reserve(now=self.now + timedelta(seconds=900)).allowed)

    def test_cached_old_receipt_does_not_restart_cooldown(self):
        old = self.now - timedelta(hours=23)
        first = self.reserve()
        cached = self.report(received=old)
        cached.update(state='cache', provider_state='plan_unsupported')
        self.assertTrue(complete_request(first.reservation, cached, now=self.now))
        blocked = self.reserve(now=self.now + timedelta(minutes=59))
        self.assertFalse(blocked.allowed)
        self.assertEqual(blocked.diagnostic['received_at'], old.isoformat())
        self.assertTrue(self.reserve(now=self.now + timedelta(hours=1)).allowed)

    def test_transport_failure_preserves_request_time_without_faking_receipt(self):
        first = self.reserve()
        report = {'state': 'download_error', 'requested_at': self.now.isoformat()}
        self.assertTrue(complete_request(first.reservation, report, now=self.now + timedelta(seconds=5)))
        blocked = self.reserve(now=self.now + timedelta(seconds=899))
        self.assertFalse(blocked.allowed)
        self.assertEqual(blocked.diagnostic['next_retry_at'], (self.now + timedelta(seconds=900)).isoformat())
        self.assertIsNone(blocked.diagnostic['received_at'])
        self.assertEqual(blocked.diagnostic['requested_at'], self.now.isoformat())

    def test_missing_source_time_is_not_filled_with_an_observed_receipt(self):
        first = self.reserve()
        self.assertTrue(complete_request(first.reservation, {'state': 'download_error'}, now=self.now))
        blocked = self.reserve()
        self.assertIsNone(blocked.diagnostic['received_at'])

    def test_untrusted_or_secret_error_fields_never_enter_database_or_diagnostic(self):
        first = self.reserve()
        self.assertTrue(self.complete(first.reservation))
        with sqlite3.connect(self.path) as connection:
            stored = '\n'.join(str(tuple(row)) for row in connection.execute('SELECT * FROM api_football_backoff'))
        blocked = self.reserve()
        self.assertNotIn(KEY, stored)
        self.assertNotIn(KEY, str(blocked))
        self.assertNotIn(KEY.encode(), self.path.read_bytes())
        self.assertNotIn('provider_body', stored)

    def test_unknown_status_is_allowlisted_instead_of_persisting_secret(self):
        first = self.reserve()
        self.assertTrue(complete_request(first.reservation, {'state': KEY, 'received_at': self.now.isoformat()}, now=self.now))
        blocked = self.reserve()
        self.assertEqual(blocked.diagnostic['provider_state'], 'unknown')
        self.assertNotIn(KEY, str(blocked.diagnostic))
        self.assertNotIn(KEY.encode(), self.path.read_bytes())

    def test_bill_or_foreign_database_is_refused_without_writing(self):
        with sqlite3.connect(self.path) as connection:
            connection.execute('CREATE TABLE picks(id INTEGER PRIMARY KEY, status TEXT)')
            connection.execute("INSERT INTO picks(status) VALUES ('settled-original')")
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        blocked = self.reserve()
        self.assertFalse(blocked.allowed)
        self.assertEqual(blocked.diagnostic['reason'], 'backoff_store_unavailable')
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute('SELECT status FROM picks').fetchone()[0], 'settled-original')
            self.assertEqual(connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(), [('picks',)])

    def test_symlink_to_foreign_database_is_refused(self):
        foreign = Path(self.tmp.name) / 'bill-fixture.db'
        with sqlite3.connect(foreign) as connection:
            connection.execute('CREATE TABLE slips(id INTEGER)')
        self.path.symlink_to(foreign)
        before = foreign.read_bytes()
        self.assertFalse(self.reserve().allowed)
        self.assertEqual(foreign.read_bytes(), before)

    def test_corrupt_file_fails_closed_without_echoing_contents(self):
        self.path.write_bytes(KEY.encode())
        blocked = self.reserve()
        self.assertFalse(blocked.allowed)
        self.assertEqual(blocked.diagnostic['reason'], 'backoff_store_unavailable')
        self.assertNotIn(KEY, str(blocked.diagnostic))
        self.assertEqual(self.path.read_bytes(), KEY.encode())

    def test_invalid_key_league_or_clock_does_not_create_ledger(self):
        for args in ({'key': ''}, {'sport': 'soccer_unknown'}, {'season': True},
                     {'now': datetime(2026, 10, 24)}):
            with self.subTest(args=args):
                blocked = self.reserve(**args)
                self.assertFalse(blocked.allowed)
                self.assertFalse(self.path.exists())

    def test_invalid_policy_never_creates_ledger(self):
        decision = reserve_request('soccer_epl', 2026, KEY, enabled=True, ledger_path=self.path,
            now=self.now, policy=BackoffPolicy(plan_seconds=0))
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.diagnostic['reason'], 'invalid_backoff_policy')
        self.assertFalse(self.path.exists())

    def test_schema_changed_ledger_fails_closed_without_migration(self):
        first = self.reserve()
        self.complete(first.reservation)
        with sqlite3.connect(self.path) as connection:
            connection.execute('ALTER TABLE api_football_backoff DROP COLUMN source_requested_at')
        before = self.path.read_bytes()
        blocked = self.reserve()
        self.assertFalse(blocked.allowed)
        self.assertEqual(blocked.diagnostic['reason'], 'backoff_store_unavailable')
        self.assertEqual(self.path.read_bytes(), before)

    def test_runtime_environment_config_is_bounded(self):
        with patch.dict(os.environ, {'VALUE_BET_API_FOOTBALL_PLAN_BACKOFF_SECONDS': '0',
            'VALUE_BET_API_FOOTBALL_AUTH_BACKOFF_SECONDS': 'mistyped',
            'VALUE_BET_API_FOOTBALL_QUOTA_BACKOFF_SECONDS': '99999999',
            'VALUE_BET_API_FOOTBALL_LEASE_SECONDS': '-1'}):
            policy = policy_from_environment()
        self.assertEqual(policy.plan_seconds, 3600)
        self.assertEqual(policy.auth_seconds, 86400)
        self.assertEqual(policy.throttle_seconds, 604800)
        self.assertEqual(policy.lease_seconds, 30)

    def test_backoff_status_text_shows_original_failure_and_safe_utc_retry(self):
        first = self.reserve()
        self.complete(first.reservation)
        text = api_football_status_text(self.reserve().diagnostic)
        self.assertIn('ไม่ได้เรียก', text)
        self.assertIn('แพ็กเกจไม่รองรับ', text)
        self.assertIn((self.now + timedelta(days=1)).isoformat(), text)
        self.assertNotIn(KEY, text)

    def test_status_text_does_not_echo_invalid_or_naive_retry_time(self):
        for retry in (KEY, '2026-10-24T01:00:00', {'unsafe': KEY}):
            with self.subTest(retry=retry):
                text = api_football_status_text({'state': 'not_called', 'reason': 'plan_backoff',
                    'provider_state': KEY, 'backoff_active': True, 'next_retry_at': retry})
                self.assertNotIn(KEY, text)
                self.assertNotIn('ลองใหม่ได้', text)

    def test_status_text_explicit_switch_off_is_clear(self):
        decision = reserve_request('soccer_epl', 2026, KEY, enabled=False, ledger_path=self.path, now=self.now)
        text = api_football_status_text(decision.diagnostic)
        self.assertIn('ปิด API-Football', text)
        self.assertIn('ไม่ใช้เครดิต', text)

    def test_invalid_completion_is_safe(self):
        self.assertFalse(complete_request(None, {}))
        first = self.reserve()
        self.assertFalse(complete_request(first.reservation, {}, now=datetime(2026, 10, 24)))


if __name__ == '__main__':
    unittest.main()
