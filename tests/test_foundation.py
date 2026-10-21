"""CC0 fictional fixtures exercise software; no football accuracy is claimed."""
import copy
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from foundation.baseline import ROOT,load_baseline
from foundation.ingest import import_dataset,import_file
from foundation.store import backup,connect,initialize,inventory
from foundation.validate import quality_report,validate


class FoundationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name);self.db=self.folder/'football_foundation.db'
        initialize(self.db)
        self.data=json.loads((ROOT/'fixtures/foundation/demo.json').read_text())

    def imported(self):
        return import_dataset(self.data,self.db)

    def count(self,table):
        with connect(self.db,readonly=True) as con:return con.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]

    def test_five_league_sample_has_provenance_but_zero_real_matches(self):
        r=self.imported();self.assertEqual((r['accepted'],r['quarantined']),(145,0))
        self.assertEqual((self.count('teams'),self.count('matches'),self.count('xg_stats'),self.count('odds_snapshots')),(20,65,60,60))
        q=quality_report(self.db)
        self.assertEqual(len(q['leagues']),5);self.assertEqual(q['real_matches'],0)
        self.assertTrue(all(x['stale_for_today'] for x in q['leagues']))
        with connect(self.db,readonly=True) as con:
            row=con.execute('SELECT * FROM source_permissions').fetchone()
            self.assertEqual(hashlib.sha256(row['evidence_text'].encode()).hexdigest(),row['evidence_sha256'])

    def test_import_is_idempotent_and_never_changes_first_observation(self):
        self.imported();before=inventory(self.db)
        r=self.imported()
        self.assertTrue(r['duplicate_batch']);self.assertEqual(before,inventory(self.db))
        d=copy.deepcopy(self.data);d['teams']=[];d['matches']=d['matches'][:1];d['odds']=[]
        import_dataset(d,self.db)
        self.assertEqual(self.count('matches'),65);self.assertEqual(self.count('observations'),65)

    def test_no_permission_or_evidence_means_no_stored_payload(self):
        for changes in [dict(rights_status='unverified'),dict(allowed_uses=['test']),dict(evidence_sha256='bad')]:
            d=copy.deepcopy(self.data);d['manifest'].update(changes)
            with self.assertRaises(ValueError):import_dataset(d,self.db)
            self.assertEqual(self.count('sources'),0);self.assertEqual(self.count('ingestion_runs'),0)

    def test_cannot_relabel_a_registered_sample_source(self):
        self.imported();d=copy.deepcopy(self.data);d['manifest']['dataset_kind']='research'
        with self.assertRaises(ValueError):import_dataset(d,self.db)
        self.assertEqual(self.count('sources'),1)

    def test_team_name_mismatch_is_quarantined_without_fuzzy_matching(self):
        self.data['matches'][0]['home_name']='Fictional 0 AA'
        r=self.imported();self.assertEqual(r['quarantined'],1)
        self.assertEqual(self.count('matches'),64)
        self.assertIn('unmatched',quality_report(self.db)['quarantine'][0]['reason'])

    def test_wrong_membership_or_youth_team_is_not_mapped_to_senior_league(self):
        for field,value in [('memberships',[{'league':'soccer_france_ligue_one','season':'2025-26'}]),('category','u21')]:
            d=copy.deepcopy(self.data);d['manifest']['source_id']+='-'+field
            d['teams'][0][field]=value
            result=import_dataset(d,self.db)
            self.assertGreater(result['quarantined'],0)
        self.assertTrue(any('category' in r['reason'] for r in quality_report(self.db)['quarantine']))

    def test_identity_conflict_blocks_dependent_quotes_in_same_batch(self):
        self.imported();d=copy.deepcopy(self.data);d['teams']=[]
        target=next(r for r in d['matches'] if r['source_match_id']=='target-0')
        target['home_id']='fiction-0-C';d['matches']=[target];d['odds']=d['odds'][:1]
        d['odds'][0]['source_quote_id']='new-quote'
        result=import_dataset(d,self.db)
        self.assertEqual(result['quarantined'],2);self.assertEqual(self.count('odds_snapshots'),60)

    def test_season_mismatch_invalid_dates_and_future_results_quarantined(self):
        for i,(field,value) in enumerate([('season','2024-25'),('kickoff_utc','bad-date'),('kickoff_utc','2026-04-01T12:00:00')]):
            self.data['matches'][i][field]=value
        self.data['matches'][3]['season']='2099-00';self.data['matches'][3]['kickoff_utc']='2099-04-01T12:00:00+00:00'
        result=self.imported();self.assertEqual(result['quarantined'],4)

    def test_duplicate_fixture_under_new_id_is_quarantined(self):
        duplicate=copy.deepcopy(self.data['matches'][0]);duplicate['source_match_id']='duplicate'
        self.data['matches'].append(duplicate)
        self.assertEqual(self.imported()['quarantined'],1);self.assertEqual(self.count('matches'),65)

    def test_missing_xg_is_not_filled_from_goals_or_zero(self):
        self.data['matches'][0].pop('xg')
        self.imported();self.assertEqual(self.count('xg_stats'),59)
        self.assertEqual(self.count('match_results'),65)

    def test_bad_xg_and_stats_roll_back_entire_row(self):
        self.data['matches'][0]['xg']['home']=-1
        self.data['matches'][1]['stats']={'home':{'shots':1,'shots_on_target':3}}
        r=self.imported();self.assertEqual(r['quarantined'],2)
        self.assertEqual(self.count('matches'),63)

    def test_availability_and_observed_time_overrides_rejected(self):
        for i,key in enumerate(['observed_at','available_at','ingested_at']):
            self.data['matches'][i][key]='2020-01-01T00:00:00+00:00'
        self.assertEqual(self.imported()['quarantined'],3)

    def test_actual_import_time_is_independent_of_historical_source_update(self):
        self.data['matches'][0]['source_updated_at']='2026-04-02T00:00:00+00:00'
        self.imported()
        with connect(self.db,readonly=True) as con:
            row=con.execute('SELECT * FROM observations WHERE source_updated_at IS NOT NULL').fetchone()
            self.assertGreater(row['observed_at'],row['source_updated_at'])

    def test_in_play_wrong_side_missing_book_and_bad_quarter_quote_rejected(self):
        for i,changes in enumerate([dict(captured_at='2026-05-10T15:00:00+00:00'),dict(side='home'),dict(bookmaker=''),dict(line=2.6)]):
            self.data['odds'][i].update(changes)
        self.assertEqual(self.imported()['quarantined'],4)
        self.assertEqual(self.count('odds_snapshots'),56)

    def test_quote_identity_cannot_overwrite_price_or_line(self):
        self.imported();before=self.count('odds_snapshots')
        self.data['teams']=[];self.data['matches']=[];self.data['odds']=self.data['odds'][:1]
        self.data['odds'][0]['line']=3.25
        self.assertEqual(import_dataset(self.data,self.db)['quarantined'],1)
        self.assertEqual(before,self.count('odds_snapshots'))

    def test_revisions_preserve_original_observation(self):
        self.imported();self.data['teams']=[];self.data['odds']=[];self.data['matches']=self.data['matches'][:1]
        self.data['matches'][0]['xg']['home']=1.6
        self.imported();self.assertEqual(self.count('observations'),66)
        with connect(self.db,readonly=True) as con:
            values=[r[0] for r in con.execute('SELECT home_xg FROM xg_stats WHERE observation_id IN (SELECT id FROM observations WHERE match_id=1) ORDER BY observation_id')]
        self.assertEqual(values,[1.5,1.6])

    def test_observed_validation_excludes_all_authored_samples(self):
        self.imported();report=validate(self.db)
        self.assertFalse(report['accuracy_claim_permitted'])
        self.assertFalse(report['calibrated']);self.assertEqual(sum(l['n'] for l in report['leagues']),0)
        self.assertTrue(all(l['roi_pct'] is None for l in report['leagues']))

    def test_research_replay_is_labeled_and_uses_each_real_fixture_line(self):
        self.imported();report=validate(self.db,'sample')
        self.assertTrue(report['sample_only']);self.assertIn('NOT point-in-time',report['timing_status'])
        self.assertEqual(sum(l['n'] for l in report['leagues']),60)
        self.assertTrue(all(l['independent_fixtures']==1 for l in report['leagues']))
        with connect(self.db,readonly=True) as con:
            rows=[json.loads(r[0]) for r in con.execute('SELECT prediction_json FROM model_predictions WHERE quote_id IS NOT NULL')]
        self.assertEqual({r['line'] for r in rows if r['market']=='Over/Under'},{2.5,2.75,3,3.25,3.5})
        self.assertTrue(all(abs(sum(r['probabilities'])-1)<1e-9 for r in rows))
        self.assertTrue(all(l['clv_n']==0 and l['clv_mean_pct'] is None for l in report['leagues']))

    def test_late_import_research_rows_never_become_observed_history(self):
        self.data['manifest'].update(dataset_kind='research',source_id='clock-test-research')
        self.imported();r=validate(self.db,'observed')
        self.assertEqual(sum(l['n'] for l in r['leagues']),0)
        self.assertTrue(all('match_not_observed_before_prediction' in l['skipped'] for l in r['leagues']))

    def test_stale_xg_and_inconsistent_methods_abstain(self):
        self.data['matches'][0]['xg']['method']='different-definition'
        self.imported();r=validate(self.db,'sample')
        self.assertEqual(r['leagues'][0]['n'],0)
        self.assertIn('inconsistent_xg_method',r['leagues'][0]['skipped'])

    def test_baseline_loader_never_imports_streamlit_or_initializes_app_database(self):
        import sys
        loaded_before='streamlit' in sys.modules
        with patch('sqlite3.connect',side_effect=AssertionError('baseline must not touch SQLite')):
            baseline,_=load_baseline();self.assertIn('settle_pick',baseline)
            self.assertAlmostEqual(sum(p for _,_,p in baseline['score_matrix'](1.7,1.1)),1)
        self.assertEqual('streamlit' in sys.modules,loaded_before)

    def test_backup_restore_preserves_every_table_and_validation_lineage(self):
        self.imported();validate(self.db,'sample')
        saved=self.folder/'copy.foundation-backup.db'
        backup(self.db,saved)
        restored=self.folder/'restored'/'football_foundation.db'
        self.assertTrue(backup(saved,restored,restore=True)['all_tables_equal'])
        self.assertEqual(inventory(self.db),inventory(restored))
        with self.assertRaises(ValueError):backup(saved,self.db,restore=True)

    def test_legacy_bill_database_refused_even_when_renamed(self):
        path=self.folder/'legacy'/'football_foundation.db';path.parent.mkdir()
        with sqlite3.connect(path) as con:
            con.execute('CREATE TABLE slips(id INTEGER,status TEXT)');con.execute("INSERT INTO slips VALUES (1,'final')")
        before=path.read_bytes()
        for operation in [lambda:initialize(path),lambda:import_dataset(self.data,path),lambda:validate(path),
                          lambda:backup(path,self.folder/'bad.foundation-backup.db')]:
            with self.assertRaises(ValueError):operation()
            self.assertEqual(before,path.read_bytes())

    def test_restore_does_not_overwrite_legacy_file_or_allow_legacy_env_path(self):
        self.imported();saved=self.folder/'copy.foundation-backup.db';backup(self.db,saved)
        legacy=self.folder/'value_bet_pro_v4.db';legacy.write_bytes(b'keep old bills')
        with self.assertRaises(ValueError):backup(saved,legacy,restore=True)
        self.assertEqual(legacy.read_bytes(),b'keep old bills')
        with patch.dict('os.environ',{'VALUE_BET_DB':str(self.db)}):
            with self.assertRaises(ValueError):validate(self.db)

    def test_no_network_for_any_foundation_operation(self):
        with patch('socket.socket.connect',side_effect=AssertionError('network forbidden')):
            self.imported();quality_report(self.db);validate(self.db,'sample')
            backup(self.db,self.folder/'offline.foundation-backup.db')

    def test_app_and_ui_bytes_remain_exactly_the_pr2_baseline(self):
        manifest=json.loads((ROOT/'foundation/baseline_manifest.json').read_text())
        self.assertEqual(manifest['base_commit'],'fac7ea0d389e72c171ae0fd154d1870cc4ec8b56')
        for path,expected in manifest['protected_files'].items():
            self.assertEqual(hashlib.sha256((ROOT/path).read_bytes()).hexdigest(),expected,path)

    def simulated_observed_dataset(self):
        # Clock simulation in a temporary unit-test DB ONLY, not historical evidence.
        self.data['manifest'].update(source_id='unit-test-clock-simulation',dataset_kind='research',reviewed_at='2026-01-01T00:00:00+00:00')
        targets=[copy.deepcopy(m) for m in self.data['matches'] if m['source_match_id'].startswith('target-')]
        before=copy.deepcopy(self.data);before['odds']=[]
        for m in before['matches']:
            if m['source_match_id'].startswith('target-'):m.pop('result')
        with patch('foundation.ingest.utc_now',return_value='2026-05-09T00:00:00+00:00'):import_dataset(before,self.db)
        prices=copy.deepcopy(self.data);prices['teams']=[];prices['matches']=[]
        with patch('foundation.ingest.utc_now',return_value='2026-05-10T12:05:00+00:00'):import_dataset(prices,self.db)
        after=copy.deepcopy(self.data);after['teams']=[];after['odds']=[];after['matches']=targets
        with patch('foundation.ingest.utc_now',return_value='2026-05-10T18:00:00+00:00'):import_dataset(after,self.db)

    def test_observed_clock_simulation_excludes_late_revision_but_accepts_earlier_inputs(self):
        self.simulated_observed_dataset()
        with patch('foundation.validate.utc_now',return_value='2026-05-11T00:00:00+00:00'):
            before=validate(self.db,'observed')
        self.assertEqual(sum(l['n'] for l in before['leagues']),60)
        with connect(self.db,readonly=True) as con:
            original=json.loads(con.execute('SELECT prediction_json FROM model_predictions WHERE validation_id=? AND quote_id IS NOT NULL ORDER BY id',(before['id'],)).fetchone()[0])
        late=copy.deepcopy(self.data);late['teams']=[];late['odds']=[];late['matches']=late['matches'][:1]
        late['matches'][0]['xg']['home']=9.0
        with patch('foundation.ingest.utc_now',return_value='2026-05-10T19:00:00+00:00'):import_dataset(late,self.db)
        with patch('foundation.validate.utc_now',return_value='2026-05-11T00:00:00+00:00'):
            observed=validate(self.db,'observed');research=validate(self.db,'research')
        with connect(self.db,readonly=True) as con:
            for report,same in [(observed,True),(research,False)]:
                row=con.execute('SELECT * FROM model_predictions WHERE validation_id=? AND quote_id IS NOT NULL ORDER BY id',(report['id'],)).fetchone()
                result=json.loads(row['prediction_json'])
                self.assertEqual(result['expected_goals']==original['expected_goals'],same)
                if same:
                    for oid in json.loads(row['input_observation_ids_json']):
                        obs=con.execute('SELECT observed_at FROM observations WHERE id=?',(oid,)).fetchone()
                        self.assertLessEqual(obs['observed_at'],row['prediction_at'])

    def test_clv_uses_only_same_contract_in_last_five_minutes(self):
        self.simulated_observed_dataset()
        late=copy.deepcopy(self.data);late['teams']=[];late['matches']=[];late['odds']=late['odds'][:1]
        late['odds'][0].update(source_quote_id='closing-over',captured_at='2026-05-10T14:58:00+00:00',odds=1.9)
        with patch('foundation.ingest.utc_now',return_value='2026-05-10T14:59:00+00:00'):import_dataset(late,self.db)
        with patch('foundation.validate.utc_now',return_value='2026-05-11T00:00:00+00:00'):r=validate(self.db,'observed')
        with connect(self.db,readonly=True) as con:
            rows=[json.loads(x[0]) for x in con.execute('SELECT prediction_json FROM model_predictions WHERE validation_id=? AND quote_id IS NOT NULL',(r['id'],))]
        only=[x for x in rows if x['clv_pct'] is not None]
        self.assertEqual(len(only),1)
        self.assertEqual((only[0]['line'],only[0]['side']),(2.5,'over'))
        self.assertAlmostEqual(only[0]['clv_pct'],(1.95/1.9-1)*100)
        self.assertIsNotNone(only[0]['closing_quote_id'])

    def test_no_out_of_time_result_used_in_validation(self):
        self.simulated_observed_dataset()
        with patch('foundation.validate.utc_now',return_value='2026-05-10T14:30:00+00:00'):
            r=validate(self.db,'observed')
        self.assertEqual(sum(l['n'] for l in r['leagues']),0)
        self.assertIn('no_final_ft90_result',r['leagues'][0]['skipped'])

    def test_cross_border_clubs_use_explicit_membership_not_country_guess(self):
        self.data['teams'][16]['country']='MC'
        self.assertEqual(self.imported()['quarantined'],0)

    def test_sample_and_research_records_never_share_metrics(self):
        self.imported()
        d=copy.deepcopy(self.data);d['manifest'].update(source_id='separate-research-clock-fixture',dataset_kind='research')
        import_dataset(d,self.db)
        sample=validate(self.db,'sample');research=validate(self.db,'research');observed=validate(self.db,'observed')
        self.assertEqual(sum(r['n'] for r in sample['leagues']),60)
        self.assertEqual(sum(r['n'] for r in research['leagues']),60)
        self.assertEqual(sum(r['n'] for r in observed['leagues']),0)
        self.assertEqual(sample['dataset_kinds'],['test_sample'])
        self.assertEqual(research['dataset_kinds'],['research'])
        self.assertFalse(research['sample_only'])

    def test_input_lineage_excludes_target_and_kickoff_day(self):
        self.imported();r=validate(self.db,'sample')
        with connect(self.db,readonly=True) as con:
            for prediction in con.execute('SELECT * FROM model_predictions WHERE validation_id=?',(r['id'],)):
                target=con.execute('SELECT kickoff_utc FROM matches WHERE id=?',(prediction['match_id'],)).fetchone()[0]
                for oid in json.loads(prediction['input_observation_ids_json']):
                    match=con.execute('SELECT m.id,m.kickoff_utc FROM observations o JOIN matches m ON o.match_id=m.id WHERE o.id=?',(oid,)).fetchone()
                    self.assertNotEqual(match['id'],prediction['match_id'])
                    self.assertLess(match['kickoff_utc'][:10],target[:10])

    def test_poisson_accuracy_does_not_require_or_invent_a_market_quote(self):
        self.data['odds']=[]
        self.imported();r=validate(self.db,'sample')
        self.assertTrue(all(l['poisson_fixture_count']>0 and l['hda_brier'] is not None for l in r['leagues']))
        self.assertTrue(all(l['n']==0 and l['roi_pct'] is None and l['clv_mean_pct'] is None for l in r['leagues']))
        self.assertEqual(self.count('odds_snapshots'),0)

    def test_multiple_providers_require_explicit_source_to_avoid_duplicate_accuracy_counts(self):
        self.imported();other=copy.deepcopy(self.data);other['manifest']['source_id']='other-sample-source'
        import_dataset(other,self.db)
        with self.assertRaises(ValueError):validate(self.db,'sample')
        r=validate(self.db,'sample',source_id=self.data['manifest']['source_id'])
        self.assertEqual(sum(l['n'] for l in r['leagues']),60)
