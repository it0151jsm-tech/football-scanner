"""Fixed-baseline offline replay. Labels/closing prices never select a bet.

An explicit redacted archive is the only input. This module does not discover
databases, fetch providers, fit parameters or write to application storage.
"""
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re

from .baseline import Baseline
from .evidence import Archive, finite, _identity
from .metrics import (report_by_market_and_league,
                      settlement_detail, summarize_settlements)
from .price_history import closing_line_value, select_price_history
from .protocol import canonical_sha256, default_protocol, utc, validate_protocol

MARKETS = ('Asian Handicap', 'Over/Under')
FEATURE_COLLECTIONS = ('events','score_history','xg_history','quotes')
MEMBERSHIP_FIELDS = ('input_observation_ids','input_normalized_sha256','input_source_payload_sha256')
HASH = re.compile(r'^[0-9a-f]{64}$')


def _stable(row):
    return (row['event_id'], row['market'], row['side'], row['point'],
            row['bookmaker_key'], row['bookmaker'], row['quote_id'])


def _conflicts(rows, key):
    groups = defaultdict(set)
    for row in rows:
        if isinstance(row.get(key), str) and row[key].strip():
            groups[row[key]].add(canonical_sha256(row))
    return {identity for identity, values in groups.items() if len(values) > 1}


def _decision_inputs(archive, decision, version, proof_index):
    """Validate the logged input set, never add other available observations.

    Observer IDs and original normalized hashes remain an audit trail back to
    its append-only DB. Export source identities are policy-versioned, so their
    rewritten proof hashes are the replay binding; Archive.proof checks those
    exact bytes. This validates supplied membership, not authenticity of clocks
    or equality with a separately archived live prediction.
    """
    if not any(key in decision for key in MEMBERSHIP_FIELDS):
        if 'model_version' in decision and decision['model_version'] != version:
            return {'status':'rejected','reason':'decision_model_version_mismatch'}
        return {'status':'legacy_unbound_descriptive','reason':'logged_input_membership_not_available',
                'inputs':{key:getattr(archive,key) for key in FEATURE_COLLECTIONS}}
    if decision.get('model_version') != version:
        return {'status':'rejected','reason':'decision_model_version_mismatch'}
    values=[decision.get(key) for key in MEMBERSHIP_FIELDS]
    if (any(not isinstance(value,list) or not value for value in values)
            or len({len(value) for value in values}) != 1):
        return {'status':'rejected','reason':'decision_input_membership_missing_or_incomplete'}
    ids,normalized,proofs=values
    if (any(type(value) is not int or value < 1 for value in ids) or len(set(ids)) != len(ids)
            or any(not isinstance(value,str) or not HASH.fullmatch(value) for value in normalized+proofs)
            or len(set(normalized)) != len(normalized) or len(set(proofs)) != len(proofs)):
        return {'status':'rejected','reason':'decision_input_membership_invalid_or_duplicated'}
    inputs={key:[] for key in FEATURE_COLLECTIONS}
    cutoff=utc(decision['decision_at'])
    recorded_decision=utc(decision.get('recorded_at')) if 'recorded_at' in decision else cutoff
    if recorded_decision is None or not utc(decision['received_at']) <= recorded_decision <= cutoff:
        return {'status':'rejected','reason':'decision_recording_clock_unverified'}
    for digest,normalized_digest in zip(proofs,normalized):
        matches=proof_index.get(digest,[])
        if len(matches) != 1:
            return {'status':'rejected','reason':'decision_input_proof_missing_or_ambiguous'}
        collection,row=matches[0]
        if collection not in FEATURE_COLLECTIONS:
            return {'status':'rejected','reason':'decision_input_contains_outcome_or_decision'}
        if archive.proof(row):
            return {'status':'rejected','reason':'decision_input_evidence_unverified'}
        original={key:value for key,value in row.items() if key!='source_payload_sha256'}
        source=original['source']
        provider,separator,revision=source.rpartition('@')
        # The observer rewrites only the source catalog identity on export.
        # Reverse that documented transform only when its policy hash matches;
        # generic archives without a revision suffix keep their source intact.
        if separator and provider and revision==canonical_sha256(archive.catalog[source])[:16]:
            original['source']=provider
        if canonical_sha256(original) != normalized_digest:
            return {'status':'rejected','reason':'decision_input_normalized_checksum_mismatch'}
        receipt=utc(row.get('received_at'))
        if receipt is None or receipt > cutoff:
            return {'status':'rejected','reason':'decision_input_received_after_decision'}
        if 'recorded_at' in row:
            recorded=utc(row['recorded_at'])
            if recorded is None or not receipt <= recorded <= cutoff:
                return {'status':'rejected','reason':'decision_input_recording_clock_unverified'}
        if 'source_received_at' in row:
            source_received=utc(row['source_received_at'])
            if source_received is None or source_received > receipt:
                return {'status':'rejected','reason':'decision_input_source_clock_unverified'}
        inputs[collection].append(row)
    if len(inputs['events']) != 1 or inputs['events'][0].get('event_id') != decision.get('event_id'):
        return {'status':'rejected','reason':'decision_input_requires_exact_target_event'}
    event=inputs['events'][0]
    if archive.event_reason(event,cutoff):
        return {'status':'rejected','reason':'decision_input_event_unverified'}
    for collection,rows in inputs.items():
        for row in rows:
            if row.get('league') != event['league']:
                return {'status':'rejected','reason':'decision_input_league_mismatch'}
            if row.get('event_id',event['event_id']) != event['event_id']:
                return {'status':'rejected','reason':'decision_input_fixture_mismatch'}
            if collection=='quotes':
                if row.get('quote_kind') not in ('observed','opening'):
                    return {'status':'rejected','reason':'decision_input_closing_or_unverified_quote'}
                # Old, genuinely logged first-observed quotes can be features;
                # the separate current-price gate still enforces freshness.
                if archive.quote_reason(row,event,cutoff,closing=True):
                    return {'status':'rejected','reason':'decision_input_quote_unverified'}
            elif collection in ('score_history','xg_history'):
                if archive.history_frame([row],event,cutoff,xg=collection=='xg_history').empty:
                    return {'status':'rejected','reason':'decision_input_history_unverified'}
    return {'status':'bound_inputs_validated','reason':None,'inputs':inputs,
            'proof_sha256':proofs,'input_n':len(proofs)}


def _split(protocol, event, decision):
    holdout = protocol.get('holdout')
    if holdout is not None and (utc(holdout['start']) <= utc(event['kickoff_utc']) <= utc(holdout['end'])
            and utc(protocol['protocol_frozen_at']) <= utc(decision)
            and utc(holdout['training_end']) < utc(decision)):
        return 'holdout'
    return 'descriptive_replay'


def _summary(records, bins, minimum):
    report = report_by_market_and_league(records, bins=bins, minimum_n_for_review=minimum)
    return report


def _empty_financials(value):
    if isinstance(value, dict):
        if value.get('n') == 0 and 'roi_pct' in value:
            for key in ('stake_units','profit_units','return_units'):
                value[key] = None
        for item in value.values():
            _empty_financials(item)
    elif isinstance(value, list):
        for item in value:
            _empty_financials(item)
    return value


def run_backtest(document, protocol=None, *, report_at=None, allow_test_fixtures=False):
    """Replay original A/B/C and return strict JSON-compatible evidence/report.

    A is a predeclared shadow comparator (A >=55 with >=5 form observations),
    not a value claim. B selects positive original EV; C runs original market
    eligibility. Each uses one unit/one event/market and the earliest recorded
    cutoff. Calibration additionally covers ALL B forecasts, not only winners
    of the positive-EV selection rule. Synthetic permission is test-only.
    """
    now = utc(report_at) if report_at is not None else datetime.now(timezone.utc)
    if now is None:
        raise ValueError('report_clock_must_be_timezone_aware')
    baseline = Baseline()
    version = baseline.version['bundle_sha256']
    protocol = deepcopy(protocol) if protocol is not None else default_protocol(version, frozen_at=now.isoformat())
    validate_protocol(protocol, version)
    if utc(protocol['protocol_frozen_at']) > now:
        raise ValueError('protocol_not_yet_frozen_at_report_clock')
    archive = Archive(document, allow_test_fixtures=allow_test_fixtures)
    mode = archive.mode
    quality = 'eligible_observed' if mode == 'observed' else 'synthetic_test_only'
    excluded, pending, selections, forecasts, price_records = Counter(), [], [], [], []
    event_diagnostics, prediction_records = [], []
    forecast_pending_n = 0
    membership_counts=Counter()
    proof_index=defaultdict(list)
    for collection in FEATURE_COLLECTIONS+('results','decisions'):
        for row in getattr(archive,collection):
            if isinstance(row.get('source_payload_sha256'),str):
                proof_index[row['source_payload_sha256']].append((collection,row))
    decision_groups = defaultdict(list)
    for row in archive.decisions:
        cutoff, received = utc(row.get('decision_at')), utc(row.get('received_at'))
        reason = archive.proof(row)
        if reason is None and (not isinstance(row.get('decision_id'), str) or not row['decision_id'].strip()
                or not isinstance(row.get('event_id'), str) or not row['event_id'].strip()):
            reason = 'decision_identity_unverified'
        if reason is None and (cutoff is None or cutoff > now or received > cutoff):
            reason = 'decision_clock_unverified_or_future'
        if reason:
            excluded[reason] += 1
        else:
            decision_groups[row['event_id']].append(row)

    # No future correction can poison an earlier decision: reconcile each
    # identity only after its actual receipt is inside the relevant cutoff.
    visited_fixtures = set()
    event_ids = sorted({row.get('event_id') for row in archive.events if isinstance(row.get('event_id'), str)}
                       | set(decision_groups))
    for event_id in event_ids:
        decisions = decision_groups.get(event_id, [])
        if not decisions:
            excluded['no_verified_recorded_decision'] += 1
            continue
        earliest = min(utc(row['decision_at']) for row in decisions)
        earliest_decisions = [row for row in decisions if utc(row['decision_at']) == earliest]
        if _conflicts(earliest_decisions, 'decision_id'):
            excluded['conflicting_decision_identity'] += 1
            continue
        decision = min(earliest_decisions, key=lambda row: row['decision_id'])
        cutoff = decision['decision_at']
        membership=_decision_inputs(archive,decision,version,proof_index)
        membership_counts[membership['status']]+=1
        if membership['status']=='rejected':
            excluded[membership['reason']]+=1
            event_diagnostics.append({'event_id':event_id,'decision_at':cutoff,
                'input_membership_status':'rejected','input_membership_reason':membership['reason'],
                'score_history_n':0,'xg_history_n':0,'current_quotes_n':0,
                'a_available_n':0,'b_available_n':0,'c_eligible_n':0})
            continue
        feature_inputs=membership['inputs']
        event_quality=('observed_inputs_unbound' if mode=='observed'
            and membership['status']=='legacy_unbound_descriptive' else quality)
        prediction_provenance=('recomputed_from_logged_inputs' if membership['status']=='bound_inputs_validated'
            else 'descriptive_unbound_replay_not_archived_live')
        excluded['later_decisions_not_independent_samples'] += len(decisions)-len(earliest_decisions)
        events = [row for row in feature_inputs['events'] if row.get('event_id') == event_id
                  and utc(row.get('received_at')) is not None and utc(row['received_at']) <= earliest]
        if not events:
            excluded['event_not_received_at_decision'] += 1
            continue
        if len({canonical_sha256({key: row.get(key) for key in
                ('event_id','league','season','home_team','away_team','kickoff_utc','period')}) for row in events}) != 1:
            excluded['conflicting_event_identity'] += 1
            continue
        event = min(events, key=lambda row: utc(row['received_at']))
        reason = archive.event_reason(event, cutoff)
        if reason is None and event['league'] not in protocol['leagues']:
            reason = 'league_outside_predeclared_scope'
        if reason:
            excluded[reason] += 1
            continue
        fixture = canonical_sha256({**{key: event[key] for key in
            ('league','season','period')}, 'kickoff_utc':utc(event['kickoff_utc']).isoformat(),
            'home_team':_identity(event['home_team']),'away_team':_identity(event['away_team'])})
        if fixture in visited_fixtures:
            excluded['duplicate_canonical_fixture'] += 1
            continue
        visited_fixtures.add(fixture)
        score_frame = archive.history_frame(feature_inputs['score_history'], event, cutoff)
        xg_frame = archive.history_frame(feature_inputs['xg_history'], event, cutoff, xg=True)
        quote_candidates, closing_quotes = [], []
        for quote in archive.quotes:
            if quote.get('event_id') != event_id:
                continue
            # Outcome-only prices are admitted to reporting only by actual
            # report receipt and kickoff; they are never baseline features.
            report_error = archive.quote_reason(quote, event, now, closing=True)
            if report_error is None:
                closing_quotes.append(quote)
        feature_price_history=[]
        for quote in feature_inputs['quotes']:
            if quote.get('event_id') != event_id:
                continue
            if archive.quote_reason(quote,event,cutoff,closing=True) is None and quote.get('quote_kind')!='closing':
                feature_price_history.append(quote)
            receipt = utc(quote.get('received_at'))
            if receipt is not None and receipt > earliest:
                continue
            reason = archive.quote_reason(quote, event, cutoff)
            if reason:
                excluded[reason] += 1
            elif quote.get('quote_kind') != 'closing':
                quote_candidates.append(quote)
        conflicting = _conflicts(quote_candidates, 'quote_id')
        excluded['conflicting_quote_identity'] += sum(row['quote_id'] in conflicting for row in quote_candidates)
        quote_candidates = [row for row in quote_candidates if row['quote_id'] not in conflicting]
        unique_quotes = {row['quote_id']: row for row in quote_candidates}
        current, histories = [], {}
        for quote in sorted(unique_quotes.values(), key=_stable):
            # Includes older evidence solely for the first-observed comparison;
            # decision/current must still pass the unchanged freshness gate.
            asof_quotes = feature_price_history
            history = select_price_history(asof_quotes, quote, cutoff)
            if history['status'] != 'available' or history['decision_quote']['quote_id'] != quote['quote_id']:
                excluded['not_latest_compatible_decision_quote'] += 1
                continue
            if 'scanned_at' in quote and utc(quote['scanned_at']) != utc(quote['captured_at']):
                excluded['scan_clock_contradicts_normalized_snapshot'] += 1
                continue
            supplied = dict(quote, id=quote['quote_id'], odds_in_range=quote.get('odds_in_range') is True)
            # Adapter field mapping of the SAME proven capture instant, never
            # a guessed receipt/provider update or a shifted historical clock.
            supplied['scanned_at'] = quote['captured_at']
            # Do not trust pre-enriched/archive analysis or an asserted first
            # quote; derive it strictly from the gated archive observations.
            supplied.pop('first_observed_quote', None)
            if history['first_observed_quote'] is not None:
                supplied['first_observed_quote'] = history['first_observed_quote']
            current.append(supplied)
            histories[quote['quote_id']] = history
        rows = baseline.evaluate(score_frame, xg_frame, event, current, cutoff) if current else []
        c_ids = baseline.c_pick_ids(rows, cutoff) if rows else set()
        # Unknown historical UI range settings cannot silently become True.
        c_ids &= {row['id'] for row in rows if row.get('odds_in_range') == 1}
        split = _split(protocol, event, cutoff)
        for row in rows:
            row.update(decision_at=utc(cutoff).isoformat(), fixture_id=fixture,
                data_mode=mode, quality_status=event_quality, model_version=version,
                input_membership_status=membership['status'],prediction_provenance=prediction_provenance,
                result_split=split, baseline_c_supported=row['id'] in c_ids)
            prediction_records.append({key: row.get(key) for key in
                ('event_id','fixture_id','quote_id','league','market','side','point','odds',
                 'decision_at','kickoff_utc','score','form_sample','ev_pct','p_win','p_partial','p_loss',
                 'model_status','result_split','baseline_c_supported','input_membership_status','prediction_provenance')})
        event_diagnostics.append({'event_id': event_id, 'fixture_id': fixture,
            'input_membership_status':membership['status'],'input_membership_reason':membership['reason'],
            'logged_input_n':membership.get('input_n'),'prediction_provenance':prediction_provenance,
            'decision_at': cutoff, 'score_history_n': len(score_frame), 'xg_history_n': len(xg_frame),
            'current_quotes_n': len(rows), 'a_available_n': sum(finite(row.get('score')) for row in rows),
            'b_available_n': sum(finite(row.get('ev_pct')) for row in rows), 'c_eligible_n': len(c_ids)})

        chosen = []
        for system in ('A','B','C'):
            candidates = [row for row in rows if (
                finite(row.get('score')) and row['score'] >= 55 and row['form_sample'] >= 5
                if system == 'A' else finite(row.get('ev_pct')) and row['ev_pct'] > 0
                if system == 'B' else row['id'] in c_ids)]
            for market in MARKETS:
                cohort = [row for row in candidates if row['market'] == market]
                if cohort:
                    # Fixed before result lookup. No price-only, closing, score
                    # of target fixture or historical ROI enters this sort.
                    picked = min(cohort, key=lambda row: (
                        -row['score'] if system == 'A' else -row['ev_pct'], _stable(row)))
                    chosen.append((system, picked))
        result, result_error = archive.result_for(event, asof=now)
        for system, picked in chosen:
            case = canonical_sha256({'fixture_id': fixture,'market': picked['market'],'system': system})
            header = {key: picked[key] for key in ('event_id','fixture_id','quote_id','league','market',
                'side','point','odds','decision_at','kickoff_utc','result_split','data_mode','quality_status','model_version',
                'input_membership_status','prediction_provenance')}
            header.update(case_id=case, system=system,
                baseline_selection={key: picked.get(key) for key in ('p_win','p_partial','p_loss','ev_pct')},
                model_available=finite(picked.get('ev_pct')))
            clv = closing_line_value(picked, closing_quotes, cutoff)
            price_records.append({'case_id':case,'system':system,'event_id':event_id,
                'market':picked['market'],'price_history':histories[picked['quote_id']],'closing':clv})
            if result_error:
                pending.append({**header,'status':'pending','reason':result_error,'proposed_stake_units':1})
                continue
            detail = settlement_detail(picked, result['home_goals'], result['away_goals'], settle_pick=baseline.settle_pick)
            if not detail['eligible']:
                excluded[detail['reason']] += 1
                continue
            selections.append({**detail, **header,'result_received_at':result['received_at']})
        # Calibration uses all unique contracts with model outputs, including
        # negative EV, so a betting filter cannot hide poor probabilities.
        unique_forecasts = {}
        for row in sorted(rows, key=_stable):
            if finite(row.get('ev_pct')):
                unique_forecasts.setdefault((row['market'],row['side'],row['point']), row)
        if result_error is None:
            for row in unique_forecasts.values():
                detail = settlement_detail(row, result['home_goals'], result['away_goals'], settle_pick=baseline.settle_pick)
                if detail['eligible']:
                    forecasts.append({**detail,'system':'B','event_id':event_id,'fixture_id':fixture,
                        'league':event['league'],'result_split':split,'decision_at':cutoff,
                        'input_membership_status':membership['status'],'quality_status':event_quality,
                        'kickoff_utc':event['kickoff_utc'],'result_received_at':result['received_at'],
                        'baseline_selection':{key:row[key] for key in ('p_win','p_partial','p_loss')},'model_available':True})
        else:
            forecast_pending_n += len(unique_forecasts)

    bins, minimum = protocol['calibration_bins'], protocol['review_policy']['minimum_sample_count']
    systems = {}
    for system in ('A','B','C'):
        records = [row for row in selections if row['system'] == system]
        unresolved = [row for row in pending if row['system'] == system]
        summary = _summary(records, bins, minimum)
        # Financial zero with zero observations is not an estimated performance.
        for cell in [summary['overall'], *summary['by_market'].values(),
                     *(value['overall'] for value in summary['by_league'].values()),
                     *(cell for value in summary['by_league'].values() for cell in value['by_market'].values())]:
            if cell['settlement']['n'] == 0:
                for key in ('stake_units','profit_units','return_units'):
                    cell['settlement'][key] = None
        systems[system] = {**summary,'selected_n':len(records)+len(unresolved),'pending_n':len(unresolved),
            'pending_proposed_stake_units':len(unresolved), 'coverage_complete':not unresolved,
            'interpretation':('shadow form comparator; not a value recommendation; no A probability' if system == 'A'
                else 'positive original model EV comparator' if system == 'B' else 'original C market policy'),
            'by_split':{split:summarize_settlements([r for r in records if r['result_split']==split],
                minimum_n_for_review=minimum) for split in ('descriptive_replay','holdout')},
            'by_input_membership':{status:summarize_settlements(
                [r for r in records if r['input_membership_status']==status],minimum_n_for_review=minimum)
                for status in ('bound_inputs_validated','legacy_unbound_descriptive')}}
    calibration = _summary(forecasts, bins, minimum)
    calibration['by_split'] = {split:_summary([row for row in forecasts if row['result_split']==split],bins,minimum)
        for split in ('descriptive_replay','holdout')}
    calibration['by_input_membership']={status:_summary(
        [row for row in forecasts if row['input_membership_status']==status],bins,minimum)
        for status in ('bound_inputs_validated','legacy_unbound_descriptive')}
    root = Path(__file__).resolve().parent
    evaluator = {path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(root.glob('*.py'))}
    n = len({row['fixture_id'] for row in selections})
    bound_real_n=len({row['fixture_id'] for row in selections if mode=='observed'
                     and row['input_membership_status']=='bound_inputs_validated'})
    return _empty_financials({'schema_version':1,'generated_at':now.isoformat(),'data_mode':mode,
        'status':'synthetic_test_only_not_real_performance' if mode == 'synthetic_test' else
                 'insufficient_real_data' if not n else 'descriptive_replay_requires_independent_review',
        'scope':'offline replay of fixed PR6 formulas; no fitting, production writes or live provider calls',
        'model_version':version,'baseline_manifest':baseline.version,'evaluator_sha256':canonical_sha256(evaluator),
        'evaluator_sources':evaluator,'archive_sha256':canonical_sha256(document),
        'protocol':protocol,'protocol_sha256':canonical_sha256(protocol),
        'input_counts':{key:len(getattr(archive,key)) for key in
            ('events','decisions','score_history','xg_history','quotes','results')},
        'data_availability_note':'This evaluator reads only the explicitly supplied archive; zero is not a live league/source coverage measurement.',
        'training_provenance_status':'unknown','training_case_ids':[],'training_fixture_ids':[],
        'performance_conclusion':'ไม่สามารถสรุปประสิทธิภาพหรือผลกำไรในอนาคตได้',
        'qualified_real_fixture_n':bound_real_n,
        'observed_descriptive_fixture_n':n if mode=='observed' else 0,
        'decision_input_membership':{key:membership_counts[key] for key in
            ('bound_inputs_validated','legacy_unbound_descriptive','rejected')},
        'archived_live_prediction_verified':False,
        'financial_summary_scope':'descriptive replay; use by_input_membership to separate logged-input reconstructions from legacy unbound replay',
        'evaluated_fixture_n':len(visited_fixtures),'systems':systems,
        'b_calibration_all_contracts':calibration,'b_calibration_contract_n':len(forecasts),
        'b_calibration_coverage':{'forecast_n':len(forecasts)+forecast_pending_n,
            'settled_count':len(forecasts),'pending_count':forecast_pending_n,
            'status':'complete' if not forecast_pending_n else 'incomplete'},
        'calibration_sampling':'all eligible B side/line forecasts once per event/market/side/point; bookmakers deduplicated; correlated contracts are not independent fixtures',
        'settled_records':selections,'pending_records':pending,'predictions':prediction_records,
        'price_reports':price_records,'event_diagnostics':event_diagnostics,
        'exclusions':dict(excluded),'history_exclusions':dict(archive.history_exclusions),
        'limitations':['Hashes bind supplied observations but cannot authenticate past clocks or source truth.',
            'Missing decision input membership is descriptive unbound replay, not an archived-live reconstruction.',
            'Retrospective fixed-formula replay is not an archived live prediction or proof of out-of-sample performance.',
            'No model fitting/calibration adjustment; original probabilities are uncalibrated.',
            'Missing source/timing/xG/prices/results are not imputed.',
            'Training provenance is unknown; this report cannot automatically approve validated prospective ranking.',
            'ROI uses settled stakes; pending cases and correlated bets must be reviewed before interpreting performance.',
            'Last observed pre-kickoff prices are not verified closing odds; changed lines have no equivalent-odds CLV.']})
