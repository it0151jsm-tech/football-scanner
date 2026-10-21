"""Real, pinned explicit-offset proofs; no synthetic proof is imported as real."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from foundation.openfootball import FIXTURES, import_openfootball, read_json
from foundation.store import connect, digest, initialize, inventory, timestamp, utc_now
from foundation.timezone_revision import (EVIDENCE, explicit_utc, import_timezone_revision,
                                          load_evidence, parse_proof, prepare_revision)
from foundation.ingest import import_dataset
from foundation.validate import validate
from scripts.timezone_pilot import run


class TimezoneRevisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name); self.db = self.folder / 'football_foundation.db'
        initialize(self.db); import_openfootball(FIXTURES / 'sample.json', self.db)
        self.sample, _ = read_json(FIXTURES / 'sample.json')
        self.registry, _ = read_json(FIXTURES / 'teams.json')
        self.collection, self.mapping, self.rights, self.review = load_evidence()
        with connect(self.db, readonly=True) as con:
            self.manifest = json.loads(con.execute('SELECT manifest_json FROM sources').fetchone()[0])
            self.old = {r['source_row_id']: dict(r) for r in con.execute('SELECT * FROM quarantine')}

    def prepared(self):
        return prepare_revision(self.sample, self.registry, self.collection, self.mapping, self.manifest, self.old)

    def count(self, table):
        with connect(self.db, readonly=True) as con:
            return con.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]

    def test_all_fifteen_primary_records_have_explicit_offsets_and_checked_identities(self):
        batch, decisions = self.prepared()
        self.assertEqual(len(batch['matches']), 15)
        self.assertEqual(len(decisions), 15)
        self.assertTrue(all(d['status'] == 'verified_pending_import' for d in decisions))
        self.assertEqual(len({m['source_match_id'] for m in batch['matches']}), 15)
        self.assertTrue(all(not d['independent_provider'] for d in decisions))

    def test_real_utc_times_are_per_record_not_one_timezone_per_league(self):
        batch, _ = self.prepared()
        by_home = {m['home_name']: m['kickoff_utc'] for m in batch['matches']}
        self.assertEqual(by_home['Liverpool FC'], '2025-08-15T19:00:00+00:00')
        self.assertEqual(by_home['Aston Villa FC'], '2025-08-16T11:30:00+00:00')
        self.assertEqual(by_home['US Sassuolo Calcio'], '2025-08-23T16:30:00+00:00')
        self.assertEqual(by_home['Genoa CFC'], '2025-08-23T16:30:00+00:00')
        self.assertEqual(by_home['FC Bayern München'], '2025-08-22T18:30:00+00:00')
        self.assertEqual(by_home['Stade Rennais FC 1901'], '2025-08-15T18:45:00+00:00')

    def test_import_appends_revision_and_preserves_original_quarantine_and_evidence(self):
        old = inventory(self.db)
        before = timestamp(utc_now()); report = import_timezone_revision(self.db); after = timestamp(utc_now())
        self.assertEqual((report['passed'], report['unresolved']), (15, 0))
        current = inventory(self.db)
        for name in ('quarantine', 'source_permissions', 'sources', 'teams', 'team_season_memberships'):
            self.assertEqual(old[name], current[name])
        self.assertEqual(old['ingestion_runs'][0], current['ingestion_runs'][0])
        self.assertEqual((self.count('matches'), self.count('observations'), self.count('match_results')), (15, 15, 15))
        self.assertLessEqual(before, timestamp(report['revision_ingested_at']))
        self.assertLessEqual(timestamp(report['revision_ingested_at']), after)

    def test_repeat_revision_is_idempotent_and_keeps_first_observation(self):
        first = import_timezone_revision(self.db); old = inventory(self.db)
        second = import_timezone_revision(self.db)
        self.assertTrue(second['import_result']['duplicate_batch'])
        self.assertEqual(first['revision_ingested_at'], second['revision_ingested_at'])
        self.assertEqual(old, inventory(self.db))

    def test_backup_restore_after_real_revision_compares_every_table(self):
        output = self.folder / 'artifacts'
        report = run(self.db, output)
        self.assertTrue(report['replay_idempotent'])
        self.assertTrue(report['restore']['all_tables_equal'])
        self.assertEqual(inventory(self.db), inventory(output / 'restored/football_foundation.db'))
        with self.assertRaises(ValueError): run(self.db, output)

    def test_proofs_receipt_urls_checksums_and_rights_survive_database_backup_payload(self):
        import_timezone_revision(self.db)
        with connect(self.db, readonly=True) as con:
            stored = json.loads(con.execute('SELECT raw_json FROM ingestion_runs ORDER BY id DESC').fetchone()[0])
        provenance = stored['provenance']
        self.assertEqual(provenance['rights_snapshots'], self.rights)
        self.assertEqual(provenance['checksum_review'], self.review)
        for item in provenance['decisions']:
            self.assertIn('/blob/', item['source_url'])
            self.assertIn('#L', item['source_url'])
            self.assertEqual(len(item['raw_excerpt_sha256']), 64)
            self.assertEqual(item['evidence_received_at'], self.collection['received_at'])
            self.assertEqual(item['original_quarantine_sha256'], digest(self.old[item['target_ref']]))

    def test_missing_proof_leaves_one_game_unresolved_without_deleting_history(self):
        self.collection['documents'][0]['rows'].pop(0)
        batch, decisions = self.prepared()
        self.assertEqual(len(batch['matches']), 14)
        held = [d for d in decisions if d['status'] == 'still_quarantined']
        self.assertEqual(held[0]['reason'], 'timezone_proof_missing')
        import_dataset(batch, self.db)
        self.assertEqual(self.count('quarantine'), 15)

    def test_naive_or_invalid_offset_is_not_inferred_from_country_or_season(self):
        for value in ('20:00', '20:00 BST', '25:00 UTC+1', '20:00 UTC+99', '20:00 Europe/London'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                explicit_utc('2025-08-15', value)
        # Pure synthetic math case, never added to real evidence or the pilot.
        self.assertEqual(explicit_utc('2025-01-01', '00:30 UTC+2'), '2024-12-31T22:30:00+00:00')

    def test_reviewed_aliases_are_exact_not_suffix_stripping_or_fuzzy(self):
        self.mapping['matches'][0]['evidence_home'] = 'Liverpool FC'
        batch, decisions = self.prepared()
        self.assertEqual(len(batch['matches']), 14)
        self.assertEqual(decisions[0]['reason'], 'reviewed_evidence_team_mismatch')

    def test_swapped_sides_wrong_date_and_wrong_score_do_not_pass(self):
        for key, replacement, expected in [('evidence_home', 'Bournemouth', 'reviewed_evidence_team_mismatch'),
                                            ('original_home', 'AFC Bournemouth', 'original_team_identity_mismatch')]:
            before = copy.deepcopy(self.mapping)
            self.mapping['matches'][0][key] = replacement
            _, decisions = self.prepared(); self.assertEqual(decisions[0]['reason'], expected)
            self.mapping = before
        self.sample['documents'][0]['document']['matches'][0]['score'] = [9, 9]
        _, decisions = self.prepared()
        self.assertEqual(decisions[0]['reason'], 'original_quarantine_payload_or_reason_mismatch')

    def test_wrong_league_season_or_time_checksum_rejected(self):
        doc = self.collection['documents'][0]; row = doc['rows'][0]
        doc['source_title'] = 'Italy | Serie A 2025/26'
        with self.assertRaises(ValueError): parse_proof(doc, row)
        doc['source_title'] = 'England | Premier League 2024/25'
        with self.assertRaises(ValueError): parse_proof(doc, row)
        doc['source_title'] = 'England | Premier League 2025/26'
        row['confirmed_utc'] = '2025-08-15T20:00:00+00:00'
        with self.assertRaises(ValueError): parse_proof(doc, row)

    def test_evidence_file_tampering_fails_before_any_database_write(self):
        copied = self.folder / 'evidence'; copied.mkdir()
        for file in EVIDENCE.glob('*.json'):
            (copied / file.name).write_bytes(file.read_bytes())
        collection, _ = read_json(copied / 'collection.json'); collection['documents'][0]['rows'][0]['confirmed_utc'] = '2000-01-01T00:00:00Z'
        (copied / 'collection.json').write_text(json.dumps(collection))
        before = inventory(self.db)
        with self.assertRaises(ValueError): import_timezone_revision(self.db, copied)
        self.assertEqual(before, inventory(self.db))

    def test_duplicate_identity_and_scope_expansion_are_refused(self):
        self.mapping['matches'].append(copy.deepcopy(self.mapping['matches'][0]))
        with self.assertRaises(ValueError): self.prepared()
        self.mapping['matches'][-1]['target_ref'] = '2025-26/en.1.json#matches[100]'
        with self.assertRaises(ValueError): self.prepared()

    def test_original_quarantine_missing_or_changed_is_not_silently_recreated(self):
        ref = next(iter(self.old)); del self.old[ref]
        batch, decisions = self.prepared(); self.assertEqual(len(batch['matches']), 14)
        self.assertEqual(decisions[0]['reason'], 'original_quarantine_record_missing')

    def test_existing_match_time_is_immutable_even_if_new_evidence_conflicts(self):
        batch, _ = self.prepared()
        target = copy.deepcopy(batch['matches'][0])
        target['kickoff_utc'] = '2025-08-15T18:00:00+00:00'
        import_dataset(dict(manifest=self.manifest, teams=[], matches=[target], odds=[]), self.db)
        report = import_timezone_revision(self.db)
        self.assertEqual((report['passed'], report['unresolved']), (14, 1))
        with connect(self.db, readonly=True) as con:
            self.assertEqual(con.execute('SELECT kickoff_utc FROM matches WHERE source_match_id=?', (target['source_match_id'],)).fetchone()[0], target['kickoff_utc'])

    def test_duplicate_fixture_under_another_id_is_quarantined(self):
        batch, _ = self.prepared(); import_dataset(batch, self.db)
        duplicate = copy.deepcopy(batch['matches'][0]); duplicate['source_match_id'] = 'different-id'
        result = import_dataset(dict(manifest=self.manifest, teams=[], matches=[duplicate], odds=[]), self.db)
        self.assertEqual(result['quarantined'], 1)
        self.assertEqual(self.count('matches'), 15)

    def test_research_revision_never_becomes_observed_history_or_filled_xg(self):
        import_timezone_revision(self.db)
        with connect(self.db, readonly=True) as con:
            self.assertEqual(con.execute('SELECT dataset_kind FROM sources').fetchone()[0], 'research')
            for table in ('xg_stats', 'team_stats', 'odds_snapshots'):
                self.assertEqual(con.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], 0)
        report = validate(self.db, 'observed')
        self.assertTrue(all(row['poisson_fixture_count'] == 0 and row['roi_pct'] is None for row in report['leagues']))

    def test_provenance_metadata_cannot_override_actual_observation_clock(self):
        batch, _ = self.prepared()
        batch['provenance']['observed_at'] = '2000-01-01T00:00:00Z'
        before = timestamp(utc_now()); import_dataset(batch, self.db)
        with connect(self.db, readonly=True) as con:
            self.assertTrue(all(timestamp(row[0]) >= before for row in con.execute('SELECT observed_at FROM observations')))

    def test_changed_proof_date_or_score_is_held_even_with_matching_names(self):
        original = copy.deepcopy(self.collection)
        # Negative in-memory tampering cases; never saved as real evidence.
        for old, new, utc in [
            ('"date": "2025-08-15"', '"date": "2025-08-14"', '2025-08-14T19:00:00+00:00'),
            ('"ft": [4, 2]', '"ft": [3, 2]', '2025-08-15T19:00:00+00:00'),
        ]:
            self.collection = copy.deepcopy(original)
            proof = self.collection['documents'][0]['rows'][0]
            proof['raw_excerpt'] = proof['raw_excerpt'].replace(old, new)
            proof['raw_excerpt_sha256'] = hashlib.sha256(proof['raw_excerpt'].encode()).hexdigest()
            proof['confirmed_utc'] = utc
            batch, decisions = self.prepared()
            self.assertEqual(len(batch['matches']), 14)
            self.assertEqual(decisions[0]['reason'], 'date_or_final_score_conflict')


if __name__ == '__main__': unittest.main()
