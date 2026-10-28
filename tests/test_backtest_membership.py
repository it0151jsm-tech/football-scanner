"""Synthetic input-membership regressions; no live accuracy/profit evidence."""
from copy import deepcopy
import unittest

from backtest.baseline import Baseline
from backtest.protocol import canonical_sha256
from test_backtest_engine import AuthoredArchive


COLLECTIONS=('events','score_history','xg_history','quotes')


def bind_inputs(fixture, rows=None, **decision_changes):
    rows=rows or [row for kind in COLLECTIONS for row in fixture.document[kind]]
    decision=dict(fixture.document['decisions'][0],model_version=Baseline().version['bundle_sha256'],
        input_observation_ids=list(range(1,len(rows)+1)),
        input_normalized_sha256=[canonical_sha256({key:value for key,value in row.items()
            if key!='source_payload_sha256'}) for row in rows],
        input_source_payload_sha256=[row['source_payload_sha256'] for row in rows])
    decision.update(decision_changes)
    fixture.document['decisions'][0]=fixture.bound(decision)
    return fixture


class BacktestMembershipTests(unittest.TestCase):
    def rejected(self,fixture,reason):
        report=fixture.report()
        self.assertEqual(report['predictions'],[])
        self.assertEqual(report['settled_records'],[])
        self.assertEqual(report['decision_input_membership']['rejected'],1)
        self.assertEqual(report['exclusions'][reason],1)
        self.assertEqual(report['event_diagnostics'][0]['input_membership_status'],'rejected')
        self.assertFalse(report['archived_live_prediction_verified'])
        return report

    def test_complete_logged_membership_replays_only_bound_inputs(self):
        fixture=bind_inputs(AuthoredArchive())
        report=fixture.report()
        self.assertEqual(report['decision_input_membership']['bound_inputs_validated'],1)
        self.assertEqual(report['event_diagnostics'][0]['logged_input_n'],23)
        self.assertEqual(len(report['settled_records']),3)
        self.assertTrue(all(row['input_membership_status']=='bound_inputs_validated'
                            for row in report['settled_records']))
        self.assertTrue(all(row['prediction_provenance']=='recomputed_from_logged_inputs'
                            for row in report['predictions']))
        self.assertFalse(report['archived_live_prediction_verified'])
        self.assertEqual(report['systems']['B']['by_input_membership']['bound_inputs_validated']['n'],1)

    def test_legacy_without_membership_is_explicitly_descriptive_and_unbound(self):
        report=AuthoredArchive().report()
        self.assertEqual(report['decision_input_membership']['legacy_unbound_descriptive'],1)
        self.assertEqual(report['decision_input_membership']['bound_inputs_validated'],0)
        self.assertTrue(all(row['prediction_provenance']=='descriptive_unbound_replay_not_archived_live'
                            for row in report['predictions']))
        self.assertEqual(report['systems']['B']['by_input_membership']['legacy_unbound_descriptive']['n'],1)
        self.assertFalse(report['archived_live_prediction_verified'])

    def test_partial_or_empty_membership_never_falls_back_to_legacy(self):
        for key,value in [('input_observation_ids',[1]),('input_normalized_sha256',[]),
                          ('input_source_payload_sha256',None)]:
            with self.subTest(key=key):
                fixture=AuthoredArchive()
                fixture.replace('decisions',model_version=Baseline().version['bundle_sha256'],**{key:value})
                self.rejected(fixture,'decision_input_membership_missing_or_incomplete')

    def test_unknown_source_proof_rejects_entire_decision(self):
        fixture=bind_inputs(AuthoredArchive())
        proofs=list(fixture.document['decisions'][0]['input_source_payload_sha256'])
        proofs[-1]='f'*64
        fixture.replace('decisions',input_source_payload_sha256=proofs)
        self.rejected(fixture,'decision_input_proof_missing_or_ambiguous')

    def test_duplicate_source_proof_occurrences_are_ambiguous(self):
        fixture=bind_inputs(AuthoredArchive())
        fixture.document['quotes'].append(deepcopy(fixture.document['quotes'][0]))
        self.rejected(fixture,'decision_input_proof_missing_or_ambiguous')

    def test_repeated_ids_or_hashes_are_rejected(self):
        for key in ('input_observation_ids','input_normalized_sha256','input_source_payload_sha256'):
            with self.subTest(key=key):
                fixture=bind_inputs(AuthoredArchive())
                values=list(fixture.document['decisions'][0][key])
                values[1]=values[0]
                fixture.replace('decisions',**{key:values})
                self.rejected(fixture,'decision_input_membership_invalid_or_duplicated')

    def test_mismatched_list_lengths_rejected(self):
        fixture=bind_inputs(AuthoredArchive())
        fixture.replace('decisions',input_observation_ids=[1])
        self.rejected(fixture,'decision_input_membership_missing_or_incomplete')

    def test_wrong_original_normalized_checksum_is_not_trusted(self):
        fixture=bind_inputs(AuthoredArchive())
        hashes=list(fixture.document['decisions'][0]['input_normalized_sha256'])
        hashes[0]='e'*64
        fixture.replace('decisions',input_normalized_sha256=hashes)
        self.rejected(fixture,'decision_input_normalized_checksum_mismatch')

    def test_membership_with_wrong_model_version_rejects(self):
        fixture=bind_inputs(AuthoredArchive(),model_version='f'*64)
        self.rejected(fixture,'decision_model_version_mismatch')

    def test_membership_without_model_version_rejects(self):
        fixture=bind_inputs(AuthoredArchive())
        decision=dict(fixture.document['decisions'][0])
        decision.pop('model_version')
        fixture.document['decisions'][0]=fixture.bound(decision)
        self.rejected(fixture,'decision_model_version_mismatch')

    def test_bound_observation_received_later_is_not_a_feature(self):
        fixture=AuthoredArchive()
        fixture.replace('score_history',received_at='2020-10-20T13:00:00+00:00')
        bind_inputs(fixture)
        self.rejected(fixture,'decision_input_received_after_decision')

    def test_bound_row_recorded_after_decision_is_not_available(self):
        fixture=AuthoredArchive()
        fixture.replace('score_history',recorded_at='2020-10-20T13:00:00+00:00')
        bind_inputs(fixture)
        self.rejected(fixture,'decision_input_recording_clock_unverified')

    def test_decision_itself_recorded_after_cutoff_cannot_claim_bound_reconstruction(self):
        fixture=bind_inputs(AuthoredArchive(),recorded_at='2020-10-20T13:00:00+00:00')
        self.rejected(fixture,'decision_recording_clock_unverified')

    def test_target_result_as_an_input_rejects_even_with_a_valid_hash(self):
        fixture=AuthoredArchive()
        rows=[row for kind in COLLECTIONS for row in fixture.document[kind]]+fixture.document['results']
        bind_inputs(fixture,rows)
        self.rejected(fixture,'decision_input_contains_outcome_or_decision')

    def test_closing_quote_in_feature_membership_rejects(self):
        fixture=AuthoredArchive()
        fixture.replace('quotes',quote_kind='closing')
        bind_inputs(fixture)
        self.rejected(fixture,'decision_input_closing_or_unverified_quote')

    def test_other_fixture_quote_or_other_league_history_rejects(self):
        for collection,changes,reason in (
                ('quotes',{'event_id':'different'},'decision_input_fixture_mismatch'),
                ('score_history',{'league':'soccer_spain_la_liga'},'decision_input_league_mismatch')):
            with self.subTest(collection=collection):
                fixture=AuthoredArchive()
                fixture.replace(collection,**changes)
                bind_inputs(fixture)
                self.rejected(fixture,reason)

    def test_target_event_must_be_in_logged_membership_exactly_once(self):
        fixture=AuthoredArchive()
        rows=[row for kind in ('score_history','xg_history','quotes') for row in fixture.document[kind]]
        bind_inputs(fixture,rows)
        self.rejected(fixture,'decision_input_requires_exact_target_event')

    def test_missing_exported_event_mapping_does_not_silently_ignore_logged_decision(self):
        fixture=bind_inputs(AuthoredArchive())
        fixture.document['events']=[]
        self.rejected(fixture,'decision_input_proof_missing_or_ambiguous')

    def test_unlisted_available_history_and_event_revision_do_not_change_inputs(self):
        fixture=bind_inputs(AuthoredArchive())
        before=fixture.report()
        fixture.document['score_history'].append(fixture.bound(dict(fixture.document['score_history'][0],home_goals=99)))
        fixture.document['events'].append(fixture.bound(dict(fixture.document['events'][0],away_team='Conflicting export row')))
        after=fixture.report()
        self.assertEqual(before['predictions'],after['predictions'])
        self.assertEqual(after['event_diagnostics'][0]['score_history_n'],10)

    def test_first_observed_feature_cannot_come_from_outside_logged_input_set(self):
        fixture=bind_inputs(AuthoredArchive())
        row=fixture.document['quotes'][0]
        first=fixture.bound(dict(row,quote_id='unlisted-first',odds=2.2,
            source_updated_at='2020-10-20T11:50:00+00:00',received_at='2020-10-20T11:50:00+00:00',
            captured_at='2020-10-20T11:50:00+00:00',scanned_at='2020-10-20T11:50:00+00:00'))
        fixture.document['quotes'].append(first)
        report=fixture.report()
        self.assertTrue(report['price_reports'])
        for item in report['price_reports']:
            self.assertNotEqual(item['price_history']['first_observed_quote']['quote_id'],'unlisted-first')

    def test_closing_outside_inputs_remains_report_only(self):
        fixture=bind_inputs(AuthoredArchive())
        before=fixture.report()
        for row in list(fixture.document['quotes']):
            fixture.document['quotes'].append(fixture.bound(dict(row,quote_id='closing-'+row['side'],
                quote_kind='closing',odds=1.6 if row['side']=='over' else 2.5,
                source_updated_at='2020-10-20T14:55:00+00:00',received_at='2020-10-20T14:55:00+00:00',
                captured_at='2020-10-20T14:55:00+00:00',scanned_at='2020-10-20T14:55:00+00:00',
                snapshot_group_id='closing')))
        after=fixture.report()
        self.assertEqual(before['predictions'],after['predictions'])
        self.assertTrue(all(item['closing']['clv_decimal'] is not None for item in after['price_reports']))

    def test_catalog_revision_transform_checks_original_normalized_bytes(self):
        fixture=bind_inputs(AuthoredArchive())
        originals=deepcopy(fixture.document)
        policy=originals['source_catalog']['fixture']
        versioned='fixture@'+canonical_sha256(policy)[:16]
        fixture.document['source_catalog'][versioned]=policy
        old_to_new={}
        for collection in COLLECTIONS+('results',):
            for index,row in enumerate(list(fixture.document[collection])):
                exported=fixture.bound(dict(row,source=versioned))
                fixture.document[collection][index]=exported
                old_to_new[row['source_payload_sha256']]=exported['source_payload_sha256']
        hashes=fixture.document['decisions'][0]['input_source_payload_sha256']
        fixture.replace('decisions',input_source_payload_sha256=[old_to_new[value] for value in hashes])
        report=fixture.report()
        self.assertEqual(report['decision_input_membership']['bound_inputs_validated'],1)
        self.assertEqual(len(report['settled_records']),3)


if __name__=='__main__':
    unittest.main()
