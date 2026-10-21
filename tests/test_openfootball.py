"""Real CC0 excerpts stay quarantined; synthetic parser cases never enter the pilot."""
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from foundation.openfootball import (AdapterError, FIXTURES, confirmed_kickoff, convert,
                                     ft_score, import_openfootball, local_team_id)
from foundation.ingest import import_dataset
from foundation.store import connect, initialize, inventory, timestamp, utc_now
from foundation.validate import validate
from scripts.openfootball_pilot import run


class OpenfootballTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.db = self.folder / 'lab' / 'football_foundation.db'
        initialize(self.db)
        self.sample = json.loads((FIXTURES / 'sample.json').read_text())
        self.registry = json.loads((FIXTURES / 'teams.json').read_text())
        self.manifest = {'dataset_kind': 'research'}

    def converted(self):
        return convert(self.sample, self.registry, self.manifest)

    def imported(self):
        return import_openfootball(FIXTURES / 'sample.json', self.db)

    def count(self, table):
        with connect(self.db, readonly=True) as con:
            return con.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]

    def test_real_cc0_sample_has_five_leagues_and_source_reported_scores(self):
        batch, report = self.converted()
        self.assertEqual(report['input_matches'], 15)
        self.assertEqual(len(report['leagues']), 5)
        self.assertTrue(all(item['input_matches'] == 3 for item in report['leagues']))
        self.assertEqual(len(batch['teams']), 30)
        rows = [row for doc in self.sample['documents'] for row in doc['document']['matches']]
        expected = [('Liverpool FC', 'AFC Bournemouth', [4, 2]),
                    ('FC Bayern München', 'RB Leipzig', [6, 0]),
                    ('Genoa CFC', 'US Lecce', [0, 0])]
        for home, away, goals in expected:
            row = next(r for r in rows if r['team1'] == home and r['team2'] == away)
            result = ft_score(row)
            self.assertEqual([result['home_goals'], result['away_goals']], goals)

    def test_all_real_clock_times_have_unverified_timezone_not_assumed_utc(self):
        report = self.imported()
        self.assertEqual((report['accepted_matches'], report['quarantined_matches']), (0, 15))
        self.assertEqual(report['accepted_teams'], 30)
        self.assertEqual(report['quarantine_reasons'], {'timezone_unverified': 15})
        self.assertEqual(self.count('matches'), 0)
        self.assertEqual(self.count('match_results'), 0)

    def test_raw_results_and_exact_source_row_provenance_survive_quarantine(self):
        self.imported()
        with connect(self.db, readonly=True) as con:
            held = con.execute('SELECT * FROM quarantine ORDER BY id').fetchall()
        for row in held:
            item = json.loads(row['payload_json'])['adapter_rejection']
            self.assertIn('score', item['raw'])
            self.assertIn(self.sample['source_commit'], item['provenance']['source_url'])
            self.assertEqual(item['provenance']['source_commit'], self.sample['source_commit'])
            raw = json.dumps(item['raw'], sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
            self.assertEqual(hashlib.sha256(raw.encode()).hexdigest(), item['provenance']['raw_row_sha256'])

    def test_real_ingestion_clock_and_research_classification_cannot_be_backdated(self):
        before = timestamp(utc_now())
        report = self.imported()
        after = timestamp(utc_now())
        self.assertLessEqual(before, timestamp(report['actual_ingested_at']))
        self.assertLessEqual(timestamp(report['actual_ingested_at']), after)
        with connect(self.db, readonly=True) as con:
            self.assertEqual(con.execute('SELECT dataset_kind FROM sources').fetchone()[0], 'research')
            self.assertTrue(all(before <= timestamp(r[0]) <= after for r in con.execute('SELECT observed_at FROM quarantine')))
        with self.assertRaises(ValueError):
            convert(self.sample, self.registry, {'dataset_kind': 'test_sample'})
        with self.assertRaises(ValueError):
            convert(self.sample, self.registry, {'dataset_kind': 'observed'})

    def test_missing_xg_stats_and_odds_are_not_manufactured(self):
        self.imported()
        for table in ('xg_stats', 'team_stats', 'odds_snapshots', 'model_predictions'):
            self.assertEqual(self.count(table), 0)
        observed = validate(self.db, 'observed')
        self.assertFalse(observed['accuracy_claim_permitted'])
        self.assertTrue(all(row['poisson_fixture_count'] == 0 and row['roi_pct'] is None for row in observed['leagues']))

    def test_reimport_is_idempotent_preserving_original_recording_times(self):
        first = self.imported()
        before = inventory(self.db)
        second = self.imported()
        self.assertTrue(second['foundation_import']['duplicate_batch'])
        self.assertEqual(first['actual_ingested_at'], second['actual_ingested_at'])
        self.assertEqual(before, inventory(self.db))

    def test_date_only_record_is_held_without_midnight_placeholder(self):
        del self.sample['documents'][0]['document']['matches'][0]['time']
        batch, _ = self.converted()
        row = next(r for r in batch['quarantine'] if r['payload']['reason_code'] == 'kickoff_time_missing')
        self.assertEqual(row['payload']['raw']['date'], '2025-08-15')
        self.assertNotIn('kickoff_utc', row['payload']['raw'])
        self.assertEqual(batch['matches'], [])

    def test_invalid_dates_and_wrong_season_are_distinct_rejections(self):
        rows = self.sample['documents'][0]['document']['matches']
        rows[0]['date'] = '2025-02-30'
        rows[1]['date'] = '2024-08-16'
        batch, _ = self.converted()
        reasons = [r['payload']['reason_code'] for r in batch['quarantine']]
        self.assertIn('invalid_match_date', reasons)
        self.assertIn('season_mismatch', reasons)

    def test_ambiguous_alias_is_not_fuzzy_matched(self):
        self.sample['documents'][3]['document']['matches'][0]['team1'] = 'Bayern Munich'
        batch, _ = self.converted()
        self.assertIn('team_unmatched', [r['payload']['reason_code'] for r in batch['quarantine']])
        self.assertEqual(len(batch['matches']), 0)

    def test_registry_ids_are_local_scoped_and_cannot_be_reassigned(self):
        team = self.registry['teams'][0]
        league = team['memberships'][0]['league']
        self.assertEqual(team['source_team_id'], local_team_id(league, team['name']))
        self.assertNotEqual(local_team_id(league, team['name']), local_team_id('other-league', team['name']))
        team['source_team_id'] = 'provider-id-that-does-not-exist'
        with self.assertRaises(ValueError):
            self.converted()

    def test_wrong_league_or_source_commit_rejected_before_import(self):
        self.sample['documents'][0]['document']['name'] = 'Italian Serie A 2025/26'
        with self.assertRaises(ValueError):
            self.converted()
        self.sample['documents'][0]['document']['name'] = 'English Premier League 2025/26'
        self.registry['source_commit'] = '0' * 40
        with self.assertRaises(ValueError):
            self.converted()

    def test_duplicate_and_conflicting_fixtures_do_not_silently_replace_results(self):
        doc = self.sample['documents'][0]
        doc['document']['matches'].append(copy.deepcopy(doc['document']['matches'][0]))
        doc['source_row_indexes'].append(100)
        batch, _ = self.converted()
        self.assertIn('duplicate_fixture', [r['payload']['reason_code'] for r in batch['quarantine']])
        doc['document']['matches'][-1]['score'] = [9, 9]
        batch, _ = self.converted()
        self.assertEqual(sum(r['payload']['reason_code'] == 'conflicting_duplicate_fixture' for r in batch['quarantine']), 2)

    def test_score_arrays_and_ft_objects_supported_but_bad_or_missing_scores_held(self):
        for score in ([0, 0], {'ft': [2, 1], 'ht': [1, 0]}):
            self.assertEqual(ft_score({'score': score})['period'], 'FT90')
        for score in (None, [-1, 0], [True, 1], [1.0, 0], [1], {'ht': [0, 0]},
                      {'ft': [0, 0], 'ht': [1, 0]}, {'ft': [1, 1], 'et': [2, 1]}):
            with self.subTest(score=score), self.assertRaises(AdapterError):
                ft_score({'score': score})

    def test_synthetic_offset_parser_case_uses_supplied_offset_only(self):
        # Parser-only synthetic input: never changes the real Openfootball fixture.
        self.assertEqual(confirmed_kickoff({'date': '2020-01-04', 'time': '15:00+01:00'}), '2020-01-04T14:00:00+00:00')
        self.assertEqual(confirmed_kickoff({'date': '2020-01-04', 'time': '15:00Z'}), '2020-01-04T15:00:00+00:00')
        for clock, reason in [('15:00', 'timezone_unverified'), ('25:90', 'invalid_match_time'), ('noon', 'invalid_match_time')]:
            with self.assertRaises(AdapterError) as caught:
                confirmed_kickoff({'date': '2020-01-04', 'time': clock})
            self.assertEqual(caught.exception.code, reason)

    def test_synthetic_converter_accepts_explicit_offset_without_filling_missing_data(self):
        # Entirely fictional converter-only input, NOT a claimed Openfootball match.
        # No import into the real pilot, no invented time attached to a real club.
        sample = copy.deepcopy(self.sample)
        sample['source_commit'] = '0' * 40
        sample['documents'] = [sample['documents'][0]]
        doc = sample['documents'][0]
        doc['source_url'] = 'https://github.com/openfootball/football.json/blob/' + '0' * 40 + '/' + doc['path']
        doc['source_row_indexes'] = [0]
        doc['document']['matches'] = [dict(date='2025-08-15', time='15:00+01:00',
                                          team1='Synthetic Test Home', team2='Synthetic Test Away', score=[1, 0])]
        league = 'soccer_epl'
        teams = [dict(source_team_id=local_team_id(league, name), name=name, country='unknown', category='men_first',
                      memberships=[dict(league=league, season='2025-26')],
                      evidence=dict(source_commit='0' * 40, source_path='2025-26/en.1.json'))
                 for name in ('Synthetic Test Home', 'Synthetic Test Away')]
        registry = dict(provider='openfootball/football.json', source_commit='0' * 40, teams=teams)
        batch, report = convert(sample, registry, self.manifest)
        self.assertEqual((len(batch['matches']), len(batch['quarantine'])), (1, 0))
        normalized = batch['matches'][0]
        self.assertEqual(normalized['kickoff_utc'], '2025-08-15T14:00:00+00:00')
        self.assertNotIn('xg', normalized)
        self.assertNotIn('stats', normalized)
        self.assertEqual(batch['odds'], [])
        self.assertEqual(report['accepted_match_provenance'][0]['source_row'], '2025-26/en.1.json#matches[0]')

    def test_sample_and_registry_checksum_tampering_stops_before_source_registration(self):
        changed = self.folder / 'changed.json'
        self.sample['documents'][0]['document']['matches'][0]['score'] = [8, 8]
        changed.write_text(json.dumps(self.sample))
        with self.assertRaises(ValueError):
            import_openfootball(changed, self.db)
        changed.write_text(json.dumps(self.registry))
        with self.assertRaises(ValueError):
            import_openfootball(FIXTURES / 'sample.json', self.db, registry_path=changed)
        self.assertEqual(self.count('sources'), 0)

    def test_original_cc0_evidence_blob_hashes_and_database_snapshot_match(self):
        self.imported()
        review = json.loads((FIXTURES / 'rights-review.json').read_text())
        self.assertEqual(review['files']['LICENSE.md']['git_blob_sha1'], '670154e3538863b2d9891fd5483160fbdfc89164')
        self.assertEqual(review['files']['README.md']['git_blob_sha1'], '81a70d4a7df63ec019c77b33e98fff8b51b4196a')
        with connect(self.db, readonly=True) as con:
            row = con.execute('SELECT * FROM source_permissions').fetchone()
            self.assertEqual(row['evidence_sha256'], hashlib.sha256(row['evidence_text'].encode()).hexdigest())
            self.assertIn('CC0 1.0 Universal', row['evidence_text'])
            self.assertIn('schema, data and scripts are dedicated to the public domain', row['evidence_text'])

    def test_malformed_adapter_quarantine_envelope_stores_nothing(self):
        # A valid existing manifest is reused only inside this temporary test DB.
        self.imported()
        with connect(self.db, readonly=True) as con:
            data = json.loads(con.execute('SELECT raw_json FROM ingestion_runs').fetchone()[0])
        data['quarantine'][0]['entity'] = 'quote'
        before = inventory(self.db)
        with self.assertRaises(ValueError):
            import_dataset(data, self.db)
        self.assertEqual(before, inventory(self.db))

    def test_bill_sentinel_is_unchanged_and_not_used_as_foundation(self):
        sentinel = self.folder / 'bill-sentinel.db'
        with sqlite3.connect(sentinel) as con:
            con.execute('CREATE TABLE slips(id INTEGER, result TEXT)')
            con.execute("INSERT INTO slips VALUES (1, 'won')")
        before = sentinel.read_bytes()
        with self.assertRaises(ValueError):
            import_openfootball(FIXTURES / 'sample.json', sentinel)
        self.assertEqual(before, sentinel.read_bytes())

    def test_real_pilot_backup_restore_includes_raw_quarantine_and_rights(self):
        output = self.folder / 'artifacts'
        report = run(self.db, output)
        self.assertTrue(report['restore']['all_tables_equal'])
        self.assertEqual(inventory(self.db), inventory(output / 'restored/football_foundation.db'))
        self.assertEqual(report['quarantined_matches'], 15)
        self.assertTrue((output / 'import-report.json').exists())
        with self.assertRaises(ValueError):
            run(self.db, output)


if __name__ == '__main__':
    unittest.main()
