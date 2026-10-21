"""Offline walk-forward baseline replay; sample metrics are never live accuracy."""
import json
import math
import uuid
from collections import Counter
from datetime import timedelta

import pandas as pd

from .baseline import load_baseline
from .store import LEAGUES, connect, digest, encoded, timestamp, utc_now

OUTCOMES=('ชนะเต็ม','ชนะครึ่ง','คืนทุน','แพ้ครึ่ง','แพ้เต็ม')


def quality_report(db_path):
    now=timestamp(utc_now())
    with connect(db_path,readonly=True) as con:
        sources=[dict(r) for r in con.execute('SELECT id,dataset_kind,rights_status,registered_at FROM sources ORDER BY id')]
        leagues=[]
        for league in LEAGUES:
            count=con.execute('SELECT COUNT(*) FROM matches WHERE league=?',(league,)).fetchone()[0]
            data=con.execute('''SELECT MAX(m.kickoff_utc),COUNT(DISTINCT o.match_id) FROM observations o
                JOIN matches m ON m.id=o.match_id JOIN xg_stats x ON x.observation_id=o.id WHERE m.league=?''',(league,)).fetchone()
            latest=data[0]
            leagues.append(dict(league=league,matches=count,matches_with_xg=data[1],last_xg_match=latest,
                                stale_for_today=latest is None or (now-timestamp(latest)).days>120,
                                # This is coverage of stored rows only, never an entire-league census.
                                scope='accepted local imports only'))
        return dict(generated_at=now.isoformat(),sources=sources,leagues=leagues,
                    permission_note='verified means supplied rights attestation; no provider permission inferred or obtained by this tool',
                    real_matches=con.execute("SELECT COUNT(*) FROM matches m JOIN sources s ON m.source_id=s.id WHERE s.dataset_kind='research'").fetchone()[0],
                    quarantine=[dict(r) for r in con.execute('SELECT entity,source_row_id,reason,observed_at FROM quarantine ORDER BY id')],
                    missing=['No verified provider collection enabled','No real-data coverage claim','No automatic team alias approval'])


def metrics(rows):
    if not rows:
        return dict(n=0,full_win_brier=None,outcome_log_loss=None,calibration_bins=[],calibration_status='insufficient_data')
    bins=[]
    for i in range(10):
        group=[r for r in rows if min(9,int(r['probabilities'][0]*10))==i]
        if group:
            bins.append(dict(lower=i/10,upper=(i+1)/10,n=len(group),predicted=sum(r['probabilities'][0] for r in group)/len(group),
                             observed=sum(r['outcome']=='ชนะเต็ม' for r in group)/len(group)))
    return dict(n=len(rows),full_win_brier=sum((r['probabilities'][0]-(r['outcome']=='ชนะเต็ม'))**2 for r in rows)/len(rows),
                outcome_log_loss=-sum(math.log(max(1e-15,r['probabilities'][OUTCOMES.index(r['outcome'])])) for r in rows)/len(rows),
                calibration_bins=bins,calibration_status='descriptive_only_not_calibrated',
                independent_fixtures=len({r['match_id'] for r in rows}))


def validate(db_path,mode='observed',source_id=None):
    if mode not in ('observed','research','sample'):
        raise ValueError('mode must be observed, research or sample')
    baseline,model_hash=load_baseline()
    now=utc_now(); run_id=str(uuid.uuid4())
    reports=[]; predictions=[]
    with connect(db_path) as con:
        dataset_hash=digest([tuple(r) for r in con.execute('SELECT source_id,sha256 FROM ingestion_runs ORDER BY source_id,sha256')])
        all_matches=[dict(r) for r in con.execute('SELECT m.*,s.dataset_kind FROM matches m JOIN sources s ON s.id=m.source_id ORDER BY m.kickoff_utc,m.id')]
        stored_kinds=sorted({m['dataset_kind'] for m in all_matches})
        eligible_sources={m['source_id'] for m in all_matches if (m['dataset_kind']=='test_sample')==(mode=='sample')}
        if source_id is not None and source_id not in eligible_sources:
            raise ValueError('selected source has no eligible records for this mode')
        if source_id is None and len(eligible_sources)>1:
            raise ValueError('multiple sources: select --source to avoid counting cross-provider duplicates')
        selected_source=source_id or next(iter(eligible_sources),None)
        all_matches=[m for m in all_matches if m['source_id']==selected_source or m['dataset_kind']=='test_sample' and mode!='sample']
        for league in LEAGUES:
            rows=[]; skipped=Counter(); hda_rows={}; score_rows={}
            for match in [m for m in all_matches if m['league']==league]:
                kickoff=timestamp(match['kickoff_utc']); prediction_at=kickoff-timedelta(hours=1)
                if match['dataset_kind']=='test_sample' and mode!='sample':
                    skipped['test_sample_excluded']+=1;continue
                if mode=='sample' and match['dataset_kind']!='test_sample':
                    skipped['research_data_excluded_from_sample_report']+=1;continue
                if mode=='observed' and timestamp(match['observed_at'])>prediction_at:
                    skipped['match_not_observed_before_prediction']+=1;continue
                if prediction_at>timestamp(now):
                    skipped['prediction_time_in_future']+=1;continue
                final=con.execute('''SELECT r.* FROM match_results r JOIN observations o ON o.id=r.observation_id
                    WHERE o.match_id=? AND o.observed_at<=? ORDER BY o.observed_at DESC,o.id DESC LIMIT 1''',(match['id'],now)).fetchone()
                if not final:
                    skipped['no_final_ft90_result']+=1;continue
                quotes=[dict(r) for r in con.execute('SELECT * FROM odds_snapshots WHERE match_id=? ORDER BY captured_at,id',(match['id'],))]
                # One current quote per contract, never a retrospective best price.
                latest={}
                for q in quotes:
                    if timestamp(q['captured_at'])>prediction_at:
                        continue
                    if mode=='observed' and timestamp(q['observed_at'])>prediction_at:
                        continue
                    if (prediction_at-timestamp(q['captured_at'])).total_seconds()>86400:
                        continue
                    key=(q['bookmaker'],q['market'],q['side'],q['line_quarters'],q['period'])
                    latest[key]=q
                observations=[dict(r) for r in con.execute('''SELECT o.id,o.observed_at,o.source_updated_at,m.kickoff_utc,
                    m.home_id,m.away_id,r.home_goals,r.away_goals,x.home_xg,x.away_xg,x.method
                    FROM observations o JOIN matches m ON m.id=o.match_id
                    JOIN match_results r ON r.observation_id=o.id JOIN xg_stats x ON x.observation_id=o.id
                    WHERE m.league=? AND m.source_id=? AND m.id!=?
                    ORDER BY o.observed_at,o.id''',(league,match['source_id'],match['id']))]
                history={}
                cutoff=kickoff.replace(hour=0,minute=0,second=0,microsecond=0)
                for o in observations:
                    if timestamp(o['kickoff_utc'])>=cutoff or timestamp(o['kickoff_utc'])>=prediction_at:
                        continue
                    if mode=='observed' and timestamp(o['observed_at'])>prediction_at:
                        continue
                    history[(o['kickoff_utc'],o['home_id'],o['away_id'])]=o
                history=sorted(history.values(),key=lambda o:o['kickoff_utc'])
                if len({o['method'] for o in history})>1:
                    skipped['inconsistent_xg_method']+=1;continue
                frame=pd.DataFrame([dict(Date_dt=pd.Timestamp(o['kickoff_utc']).tz_convert(None),
                    HomeTeam=str(o['home_id']),AwayTeam=str(o['away_id']),FTHG=o['home_goals'],FTAG=o['away_goals'],
                    xG_home=o['home_xg'],xG_away=o['away_xg']) for o in history])
                estimate=baseline['estimate_goals'](frame,str(match['home_id']),str(match['away_id']),kickoff)
                if estimate is None:
                    skipped['missing_insufficient_or_stale_xg']+=1;continue
                matrix=baseline['score_matrix'](estimate['home_lambda'],estimate['away_lambda'])
                hda=[sum(p for h,a,p in matrix if (h>a if s==0 else h==a if s==1 else h<a)) for s in range(3)]
                actual=0 if final['home_goals']>final['away_goals'] else 1 if final['home_goals']==final['away_goals'] else 2
                hda_rows[match['id']]=(hda,actual)
                score_rows[match['id']]=next(p for h,a,p in matrix if h==min(12,final['home_goals']) and a==min(12,final['away_goals']))
                quality='test_sample_only' if match['dataset_kind']=='test_sample' else 'research_non_pit' if mode=='research' else 'observed_replay'
                predictions.append((run_id,match['id'],None,model_hash,mode,prediction_at.isoformat(),now,
                    encoded([o['id'] for o in history]),encoded(dict(model_stage='poisson',
                    expected_goals=[estimate['home_lambda'],estimate['away_lambda']],hda=hda,
                    result_observation_id=final['observation_id'],score_bucket_probability=score_rows[match['id']])),quality))
                if not latest:
                    skipped['no_eligible_pre_prediction_quote']+=1
                for q in latest.values():
                    line=q['line_quarters']/4
                    evaluation=baseline['evaluate_market_poisson'](matrix,'totals' if q['market']=='Over/Under' else 'spreads',q['side'],line,q['odds'])
                    contract=dict(market=q['market'],side=q['side'],point=line,odds=q['odds'])
                    outcome,profit,_=baseline['settle_pick'](contract,final['home_goals'],final['away_goals'])
                    probs=[0.]*5
                    for h,a,p in matrix:
                        label=baseline['settle_pick'](contract,h,a)[0]
                        probs[OUTCOMES.index(label)]+=p
                    closing=[c for c in quotes if all(c[k]==q[k] for k in ('bookmaker','market','side','line_quarters','period'))
                             and kickoff-timedelta(minutes=5)<=timestamp(c['captured_at'])<kickoff]
                    # A historical source timestamp alone never proves actual observation time.
                    if mode=='observed':
                        closing=[c for c in closing if timestamp(c['observed_at'])<kickoff]
                    close=max(closing,key=lambda c:(c['captured_at'],c['id'])) if closing else None
                    entry=dict(model_stage='market',match_id=match['id'],quote_id=q['id'],market=q['market'],side=q['side'],line=line,
                        ev_pct=evaluation['ev_pct'],profit_units=profit,outcome=outcome,probabilities=probs,
                        result_observation_id=final['observation_id'],closing_quote_id=close['id'] if close else None,
                        clv_pct=(q['odds']/close['odds']-1)*100 if close else None)
                    rows.append(entry)
                    predictions.append((run_id,match['id'],q['id'],model_hash,mode,prediction_at.isoformat(),now,
                        encoded([o['id'] for o in history]),encoded(dict(expected_goals=[estimate['home_lambda'],estimate['away_lambda']],evaluation=evaluation,**entry)),
                        'test_sample_only' if match['dataset_kind']=='test_sample' else 'research_non_pit' if mode=='research' else 'observed_replay'))
            # Fixed evaluation policy, separate from live A/B/C: EV>0, max one bet per fixture/market;
            # equal EV abstains. This is not the production A/C recommendation policy.
            bets=[]
            for key in sorted({(r['match_id'],r['market']) for r in rows}):
                choices=sorted([r for r in rows if (r['match_id'],r['market'])==key and r['ev_pct']>0],key=lambda r:r['ev_pct'],reverse=True)
                if choices and (len(choices)==1 or choices[0]['ev_pct']-choices[1]['ev_pct']>1e-9):
                    bets.append(choices[0])
            profits=sum(r['profit_units'] for r in bets);clv=[r['clv_pct'] for r in bets if r['clv_pct'] is not None]
            reports.append(dict(league=league,**metrics(rows),skipped=dict(skipped),
                by_market={market:metrics([r for r in rows if r['market']==market]) for market in ('Asian Handicap','Over/Under')},
                poisson_fixture_count=len(hda_rows),
                hda_brier=sum(sum((p-(i==actual))**2 for i,p in enumerate(ps)) for ps,actual in hda_rows.values())/len(hda_rows) if hda_rows else None,
                score_bucket_log_loss=-sum(math.log(max(p,1e-15)) for p in score_rows.values())/len(score_rows) if score_rows else None,
                score_bucket_definition='unchanged baseline tail bucket 12+',
                policy='offline EV>0 max one per fixture/market, EV tie abstains; not live A/C',
                flat_stake_bets=len(bets),profit_units=profits,staked_units=len(bets),
                roi_pct=profits/len(bets)*100 if bets else None,yield_pct=profits/len(bets)*100 if bets else None,
                roi_yield_definition='same denominator: total amount staked, including pushes; not return on starting bankroll',
                clv_n=len(clv),clv_mean_pct=sum(clv)/len(clv) if clv else None,
                clv_definition='decimal price ratio, same contract and bookmaker, last observed snapshot in final 5 pre-kickoff minutes; not de-vig CLV',
                clv_missing_reason='no qualifying same-line closing snapshot' if not clv else None))
        kinds={r['dataset_kind'] for r in all_matches if (r['dataset_kind']=='test_sample')==(mode=='sample')}
        report=dict(id=run_id,generated_at=now,mode=mode,source_id=selected_source,model_hash=model_hash,dataset_sha256=dataset_hash,
                    dataset_kinds=sorted(kinds),stored_dataset_kinds=stored_kinds,sample_only=bool(kinds) and kinds=={'test_sample'},
                    accuracy_claim_permitted=False,calibrated=False,
                    timing_status='fictional software test only; NOT point-in-time evidence' if mode=='sample' else 'reconstructed research inputs; NOT point-in-time evidence' if mode=='research' else 'actual ingestion time gated; replay, not an archived live forecast',
                    limits=['No model changes or fitting','No confidence intervals/claim of profitability',
                            'Quotes within a fixture are correlated; n is not independent sample size',
                            'Historical first-availability is unknown unless locally observed; source_updated_at never backdates ingestion',
                            'Research and sample results cannot establish live accuracy','No A/C strategy validation in this phase'],leagues=reports)
        con.execute('INSERT INTO validation_runs VALUES (?,?,?,?,?,?)',(run_id,now,mode,dataset_hash,model_hash,encoded(report)))
        con.executemany('''INSERT INTO model_predictions(validation_id,match_id,quote_id,model_hash,mode,prediction_at,evaluated_at,
                          input_observation_ids_json,prediction_json,quality) VALUES (?,?,?,?,?,?,?,?,?,?)''',predictions)
    return report
