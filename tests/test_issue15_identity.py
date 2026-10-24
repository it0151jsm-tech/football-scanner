"""Access limits must not become invented club identities or source availability."""
import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_issue9_scan as issue9

from history_data import alias_evidence, match_team


ROOT = Path(__file__).resolve().parents[1]


class Issue15IdentityEvidenceTests(unittest.TestCase):
    def test_connector_errors_cannot_claim_provider_payload_status_or_checksum(self):
        evidence = json.loads((ROOT/'fixtures/issue15_identity_evidence.json').read_text())
        self.assertEqual(evidence['source_payloads_observed'], 0)
        self.assertEqual(len(evidence['sources']), 5)
        self.assertEqual(len(evidence['cross_provider_identities_added']), 2)
        self.assertFalse(evidence['current_epl_roster']['verified'])
        for source in evidence['sources']:
            with self.subTest(url=source['url']):
                self.assertIsNone(source['provider_http_status'])
                self.assertIsNone(source['source_payload_sha256'])
                self.assertIsNone(source['source_received_at'])
                self.assertIsNone(source['valid_rows'])
                self.assertEqual(source['labels'], [])
                self.assertEqual(source['error_evidence_sha256'],
                                 hashlib.sha256(source['connector_message'].encode()).hexdigest())

    def test_reviewed_manchester_identity_has_historical_labels_and_official_proof(self):
        evidence = json.loads((ROOT/'fixtures/issue15_identity_evidence.json').read_text())
        mirror = evidence['mirror_identity_evidence']
        self.assertEqual(mirror['season'], '2324')
        self.assertEqual(mirror['git_blob_sha'], '080a33fc535bf75e4c3baeca32ce425760985758')
        self.assertRegex(mirror['sha256'], r'^[0-9a-f]{64}$')
        proofs = {proof['club_id']: proof for proof in evidence['official_name_evidence']}
        for identity in evidence['cross_provider_identities_added']:
            with self.subTest(team=identity['full_name']):
                self.assertIn(identity['csv_label'], mirror['labels'])
                matched, method = match_team(identity['full_name'], mirror['labels'])
                self.assertEqual((matched, method), (identity['csv_label'], 'verified_alias'))
                proof = proofs[identity['club_id']]
                self.assertEqual(identity['official_url'], proof['url'])
                self.assertEqual(proof['evidence_excerpt_sha256'], hashlib.sha256(
                    json.dumps(proof['retained_phrases'], ensure_ascii=False,
                               separators=(',', ':')).encode()).hexdigest())
                record = alias_evidence(identity['full_name'], matched)
                self.assertEqual(record['identity_evidence'], proof['url'])
                self.assertNotIn('api_evidence', record)

    def test_a_failed_identity_cannot_be_filled_from_a_similar_rival_or_youth_team(self):
        for name, candidates in [('Manchester City', ['Manchester United']),
                                 ('Nottingham Forest', ['Notts County']),
                                 ('Ipswich Town', ['Ipswich Town U21']),
                                 ('Arsenal', ['Arsenal Women']),
                                 ('Newcastle United', ['Newcastle Jets'])]:
            with self.subTest(name=name):
                self.assertEqual(match_team(name, candidates), (None, 'name_unmatched'))
                self.assertIsNone(alias_evidence(name, candidates[0]))

    def test_previously_reviewed_forest_alias_remains_evidenced_without_new_request(self):
        name, method = match_team('Nottingham Forest', ["Nott'm Forest"])
        self.assertEqual((name, method), ("Nott'm Forest", 'verified_alias'))
        evidence = alias_evidence('Nottingham Forest', name)
        self.assertEqual(evidence['api_evidence'],
                         'https://github.com/it0151jsm-tech/football-scanner/issues/9')
        self.assertEqual(evidence['csv_source'],
                         'https://www.football-data.co.uk/mmz4281/2627/E0.csv')

    def test_mock_city_united_scan_recovers_form_without_inventing_b_or_ev(self):
        # Synthetic pairing/scores only; neither a claimed live fixture nor live xG.
        case = issue9.Issue9ScanTests(
            methodName='test_csv_aliases_recover_stuttgart_gladbach_5_and_10_form')
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.prepare(english=True, blocked=True)
        case.csv = case.frame('Man City', 'Man United')
        case.api = ('Manchester City', 'Manchester United')
        case.event.update(id='synthetic-city-united-identity-regression',
                          home_team=case.api[0], away_team=case.api[1])
        case.event['bookmakers'][0]['markets'][0]['outcomes'][0]['name'] = case.api[0]
        with patch.dict('os.environ', {'API_FOOTBALL_KEY': ''}):
            rows, _, _, _ = case.scan()
        self.assertTrue(rows)
        for row in rows:
            a, b = case.analyses(row)
            self.assertIsNotNone(a['score'])
            for side, csv_name in [('home', 'Man City'), ('away', 'Man United')]:
                profile = a[side+'_form']
                self.assertEqual((profile['sample5'], profile['sample10']), (5, 10))
                self.assertEqual(profile['diagnostics']['matched_name'], csv_name)
                self.assertEqual(profile['diagnostics']['mapping'], 'verified_alias')
                api = profile['diagnostics']['api_football']
                self.assertEqual(api['state'], 'not_called')
                self.assertFalse(api['request_attempted'])
            self.assertFalse(b['available'])
            self.assertIsNone(row['ev_pct'])
            self.assertIsNone(row['lambda_home'])
            self.assertIsNone(row['lambda_away'])
        case.f['fetch_odds'].assert_called_once()
        case.f['fetch_history'].assert_called_once_with('E0')
        case.f['fetch_understat_history'].assert_called_once_with('soccer_epl')


if __name__ == '__main__':
    unittest.main()
