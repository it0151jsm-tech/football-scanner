"""Personal evidence lineage and point-in-time checks; only synthetic in-memory data."""
import hashlib
import json
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from personal_stats import initialize, save_verified, read_history, eligible_history, clean_stats

UTC = timezone.utc
KICKOFF = datetime(2026, 10, 20, 19, tzinfo=UTC)
FIRST = datetime(2026, 10, 21, 8, tzinfo=UTC)
SECOND = datetime(2026, 10, 21, 12, tzinfo=UTC)
TARGET = datetime(2026, 10, 23, 19, tzinfo=UTC)


class PersonalStatsIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.args = dict(match_key='fixture', kickoff_utc=KICKOFF.isoformat(),
                         home_team='Home', away_team='Away', home_goals=2, away_goals=1,
                         source='Human reviewed fixture source', source_url='https://example.org/fixture',
                         source_match_id='provider-1', league='soccer_epl', season='2627',
                         reviewed=True, completed=True, evidence_mode='observed',
                         stats={'xG': {'home': 1.8, 'away': .7}, 'shots': {'home': 10, 'away': 7}})

    def save(self, when=FIRST, **changes):
        args = dict(self.args, **changes)
        with patch('personal_stats._utc_now', return_value=when):
            return save_verified(self.db, **args)

    def eligible(self, as_of=SECOND, **changes):
        args = dict(team='Home', before=TARGET, as_of=as_of, league='soccer_epl', season='2627')
        args.update(changes)
        return eligible_history(self.db, **args)

    def test_explicit_review_completed_and_real_backend_times_are_required(self):
        self.save(metadata={'claimed_historic_received_at': '2000-01-01T00:00:00Z'})
        row = self.eligible()[0]
        self.assertEqual(row['received_at'], FIRST.isoformat())
        self.assertEqual(row['recorded_at'], FIRST.isoformat())
        self.assertEqual(row['reviewed_at'], FIRST.isoformat())
        self.assertTrue(row['reviewed'] and row['verified'] and row['completed'])
        self.assertEqual(row['metadata']['claimed_historic_received_at'], '2000-01-01T00:00:00Z')
        with self.assertRaises(TypeError):
            save_verified(self.db, **self.args, received_at='2000-01-01T00:00:00Z')

    def test_no_review_or_finality_assertion_cannot_create_observed_evidence(self):
        for changes in (dict(reviewed=False), dict(completed=False), dict(reviewed='yes'),
                        dict(completed=1), dict(home_goals=None, away_goals=None)):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.save(**changes)
        self.assertEqual(read_history(self.db), [])

    def test_legacy_api_calls_remain_reviewable_but_not_model_eligible(self):
        args = {key: self.args[key] for key in ('match_key','kickoff_utc','home_team','away_team','home_goals','away_goals','source','stats')}
        with patch('personal_stats._utc_now', return_value=FIRST):
            save_verified(self.db, **args)
            save_verified(self.db, **args)
        self.assertEqual(len(read_history(self.db, team='Home')), 1)
        self.assertEqual(self.eligible(), [])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM personal_match_stats').fetchone()[0], 0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM personal_match_stats_revisions').fetchone()[0], 2)

    def test_research_import_received_now_cannot_backfill_historical_prediction(self):
        self.save(evidence_mode='research')
        self.assertEqual(self.eligible(), [])
        self.assertEqual(self.eligible(as_of=KICKOFF, before=KICKOFF), [])
        self.assertEqual(self.eligible(as_of=FIRST), [])

    def test_receipt_and_review_must_be_strictly_before_as_of_and_target(self):
        self.save()
        self.assertEqual(self.eligible(as_of=FIRST), [])
        self.assertEqual(len(self.eligible(as_of=FIRST+timedelta(microseconds=1))), 1)
        self.assertEqual(self.eligible(as_of=KICKOFF, before=KICKOFF), [])
        with self.assertRaises(ValueError):
            self.eligible(as_of=TARGET+timedelta(seconds=1))

    def test_append_revisions_preserve_the_version_known_before_cutoff(self):
        first_id = self.save()
        original = self.db.execute('SELECT * FROM personal_match_stats_revisions').fetchone()
        second_id = self.save(when=SECOND, home_goals=3)
        self.assertNotEqual(first_id, second_id)
        self.assertEqual(self.db.execute('SELECT * FROM personal_match_stats_revisions WHERE revision_id=?', (first_id,)).fetchone(), original)
        old = self.eligible(as_of=FIRST+timedelta(hours=1))[0]
        new = self.eligible(as_of=SECOND+timedelta(hours=1))[0]
        self.assertEqual((old['home_goals'], old['revision_id']), (2, first_id))
        self.assertEqual((new['home_goals'], new['supersedes_id']), (3, first_id))
        self.assertEqual(read_history(self.db)[0]['home_goals'], 3)

    def test_unreviewed_correction_supersedes_old_confidence_at_its_receipt(self):
        self.save()
        self.save(when=SECOND, evidence_mode='research', reviewed=False, completed=False, home_goals=4)
        self.assertEqual(len(self.eligible(as_of=FIRST+timedelta(hours=1))), 1)
        self.assertEqual(self.eligible(as_of=SECOND+timedelta(hours=1)), [])

    def test_revision_history_cannot_be_updated_or_deleted(self):
        self.save()
        for sql in ('UPDATE personal_match_stats_revisions SET home_goals=9',
                    'DELETE FROM personal_match_stats_revisions'):
            with self.subTest(sql=sql), self.assertRaises(sqlite3.IntegrityError):
                self.db.execute(sql)
        self.assertEqual(self.eligible()[0]['home_goals'], 2)

    def test_legacy_rows_and_all_bill_rows_stay_exactly_unchanged(self):
        initialize(self.db)
        self.db.execute('CREATE TABLE slips(id INTEGER PRIMARY KEY,selections_json,status,profit_units)')
        self.db.execute('INSERT INTO slips VALUES(1,?,?,?)', ('{damaged legacy payload', 'final', -1))
        self.db.execute('INSERT INTO personal_match_stats VALUES(?,?,?,?,?,?,?,?,?,?)',
                        ('legacy', KICKOFF.isoformat(), 'Home', 'Other', 1, 0, 'legacy provider', '{}', FIRST.isoformat(), 1))
        before = self.db.execute('SELECT * FROM slips').fetchall(), self.db.execute('SELECT * FROM personal_match_stats').fetchall()
        self.save()
        after = self.db.execute('SELECT * FROM slips').fetchall(), self.db.execute('SELECT * FROM personal_match_stats').fetchall()
        self.assertEqual(before, after)
        self.assertEqual({row['match_key'] for row in read_history(self.db)}, {'legacy', 'fixture'})
        self.assertEqual([row['match_key'] for row in self.eligible()], ['fixture'])

    def test_read_only_queries_do_not_create_tables_or_commit_pending_bill_changes(self):
        self.assertEqual(read_history(self.db), [])
        self.assertEqual(self.eligible(), [])
        self.assertEqual(self.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(), [])
        self.db.execute('CREATE TABLE slips(value)')
        self.db.execute('INSERT INTO slips VALUES (7)')
        self.save()
        self.db.rollback()
        self.assertEqual(self.db.execute('SELECT * FROM slips').fetchall(), [])
        self.assertEqual(read_history(self.db), [])

    def test_utc_conversion_replaces_lexicographic_timezone_filter(self):
        self.save(kickoff_utc='2026-10-21T01:00:00+07:00')  # Oct 20 18:00 UTC.
        rows = read_history(self.db, team='Home', before='2026-10-20T20:00:00Z')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['kickoff_utc'], '2026-10-20T18:00:00+00:00')
        self.assertEqual(read_history(self.db, before='2026-10-20T17:00:00Z'), [])
        with self.assertRaises(ValueError):
            read_history(self.db, before='2026-10-20T20:00:00')
        with self.assertRaises(ValueError):
            self.eligible(as_of='2026-10-22T00:00:00')

    def test_legacy_non_utc_offsets_are_filtered_by_instants_without_rewriting(self):
        initialize(self.db)
        self.db.execute('INSERT INTO personal_match_stats VALUES(?,?,?,?,?,?,?,?,?,?)',
                        ('legacy', '2026-10-21T01:00:00+07:00', 'Home', 'Away', 1, 0, 'old', '{}', FIRST.isoformat(), 1))
        original = self.db.execute('SELECT * FROM personal_match_stats').fetchone()
        self.assertEqual(len(read_history(self.db, before='2026-10-20T20:00:00Z')), 1)
        self.assertEqual(self.db.execute('SELECT * FROM personal_match_stats').fetchone(), original)
        self.assertEqual(self.eligible(), [])

    def test_wrong_team_scope_missing_metadata_and_self_event_never_qualify(self):
        self.save(home_team_id='home-id', away_team_id='away-id')
        for changes in (dict(team='HOME'), dict(league=None), dict(season=None),
                        dict(league='soccer_france_ligue_one'), dict(team_id='different'),
                        dict(exclude_match_key='fixture'), dict(exclude_match_key='provider-1')):
            with self.subTest(changes=changes):
                self.assertEqual(self.eligible(**changes), [])
        self.assertEqual(len(self.eligible(team_id='home-id')), 1)

    def test_match_key_cannot_change_recorded_fixture_identity(self):
        self.save()
        for changes in (dict(home_team='Similar Name'), dict(away_team='Other'),
                        dict(kickoff_utc=(KICKOFF-timedelta(days=1)).isoformat()),
                        dict(league='soccer_france_ligue_one')):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.save(when=SECOND, **changes)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM personal_match_stats_revisions').fetchone()[0], 1)

    def test_cross_season_ambiguous_identity_and_bad_scores_rejected(self):
        for changes in (dict(season='2526'), dict(season=True), dict(home_team='Away'),
                        dict(home_team_id='same', away_team_id='same'), dict(home_team_id='one'),
                        dict(home_goals=True), dict(home_goals=-1), dict(home_goals=1.2),
                        dict(away_goals=None), dict(source=''), dict(source_url='javascript:alert(1)'),
                        dict(source_url='https://user:password@example.org/fixture'),
                        dict(kickoff_utc='2026-10-20T19:00:00'), dict(kickoff_utc=TARGET.isoformat())):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.save(**changes)

    def test_duplicate_source_keys_do_not_inflate_sample_count(self):
        self.save()
        self.save(when=FIRST+timedelta(minutes=1), match_key='provider-alias', source_match_id='provider-2')
        rows = self.eligible()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['match_key'], 'provider-alias')

    def test_conflicting_duplicate_scores_or_kickoffs_are_quarantined(self):
        self.save()
        self.save(when=FIRST+timedelta(minutes=1), match_key='conflict', home_goals=9)
        self.assertEqual(self.eligible(), [])
        other = sqlite3.connect(':memory:');self.addCleanup(other.close)
        with patch('personal_stats._utc_now', return_value=FIRST):
            save_verified(other, **self.args)
            save_verified(other, **dict(self.args, match_key='other', kickoff_utc=(KICKOFF+timedelta(hours=1)).isoformat()))
        self.assertEqual(eligible_history(other,team='Home',before=TARGET,as_of=SECOND,league='soccer_epl',season='2627'), [])

    def test_arbitrary_labels_alias_collisions_and_nonfinite_values_are_not_saved(self):
        for stats in ({'custom_xg': {'home': 1, 'away': 1}},
                      {'xG': {'home': float('nan'), 'away': 1}},
                      {'xG': {'home': 1, 'away': 1}, 'expected_goals': {'home': 2, 'away': 2}}):
            with self.subTest(stats=stats), self.assertRaises(ValueError):
                self.save(stats=stats)
        self.assertEqual(read_history(self.db), [])


if __name__ == '__main__':
    unittest.main()
