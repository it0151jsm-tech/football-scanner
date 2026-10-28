"""Prospective, offline ranking of baseline-qualified candidates.

This module never selects historical backtest picks, changes model formulas, or
turns form scores into probabilities. A reviewed artifact is an owner assertion
about a separate, completed holdout: hashes check attachment integrity, not the
reviewer's identity, data authenticity, profitability, or calibration.

Review bundles contain ``manifest`` and the actual ``report`` attachment. Both
repeat model/domain/data-mode and evaluation boundaries. The report additionally
contains ``training_case_ids``, ``training_fixture_ids`` and unique holdout
``results`` with case_id, fixture_id, quality_status, data_mode, result_split,
and a settled outcome. Each row also binds system C, model/league/market and
aware decision/kickoff/result-receipt clocks. The report must declare verified
training provenance and complete settlement_coverage (selected_count equals
settled_count equals result count, pending_count zero). These remain reviewed
assertions, not automatic proof of provenance or capture completeness. The full
holdout window must match protocol.holdout; missing holdout remains unvalidated.
Minimum sample count is predeclared in the hash-bound
protocol's review_policy; passing it alone is not statistical proof.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import re

from .protocol import validate_protocol


MARKETS = frozenset({'Asian Handicap', 'Over/Under'})
_SETTLED = frozenset({'win', 'loss', 'full_win', 'full_loss', 'push', 'half_win', 'half_loss',
                     'ชนะเต็ม', 'ชนะครึ่ง', 'คืนทุน', 'เจ๊า', 'แพ้ครึ่ง', 'แพ้เต็ม'})
_TIMES = ('evaluation_start', 'evaluation_end', 'holdout_start',
          'training_end', 'protocol_frozen_at')
_DOMAIN = ('model_version', 'league', 'market')


def canonical_sha256(value):
    """Hash the actual strict JSON attachment, never a declared ROI or filename."""
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False,
                         separators=(',', ':'), allow_nan=False).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def _instant(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return stamp.astimezone(timezone.utc) if stamp.utcoffset() is not None else None
    except (ValueError, TypeError, OverflowError):
        return None


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _label(value):
    return isinstance(value, str) and bool(value.strip())


def _ids(value):
    return (isinstance(value, list) and all(_label(item) for item in value)
            and len(value) == len(set(value)))


def _protocol_errors(protocol):
    if not isinstance(protocol, dict):
        return ['invalid_protocol']
    try:
        canonical_sha256(protocol)
        if not _label(protocol.get('model_version')):
            return ['protocol_model_version_required']
        validate_protocol(protocol, protocol['model_version'])
    except (TypeError, ValueError, OverflowError, AttributeError):
        return ['invalid_predeclared_protocol']
    return []


def candidate_reasons(candidate):
    """Only already-qualified original baseline C rows can enter this queue."""
    if not isinstance(candidate, dict):
        return ['invalid_candidate']
    reasons = []
    if candidate.get('quality_status') != 'eligible_observed':
        reasons.append('data_not_eligible_observed')
    if candidate.get('baseline_c_supported') is not True:
        reasons.append('original_baseline_c_not_supported')
    if not _number(candidate.get('ev_pct')) or candidate['ev_pct'] <= 0:
        reasons.append('positive_model_ev_required')
    if not isinstance(candidate.get('market'), str) or candidate['market'] not in MARKETS:
        reasons.append('unsupported_market')
    if not all(_label(candidate.get(key)) for key in ('event_id', 'league', 'model_version')):
        reasons.append('missing_candidate_identity')
    decision, kickoff = _instant(candidate.get('decision_at')), _instant(candidate.get('kickoff_utc'))
    if decision is None or kickoff is None or kickoff <= decision:
        reasons.append('prospective_aware_kickoff_required')
    if 'fixture_id' in candidate and not _label(candidate['fixture_id']):
        reasons.append('invalid_canonical_fixture_identity')
    return reasons


def validate_review_artifact(bundle, candidate, protocol):
    """Check pre-decision evidence; the result never claims a model is profitable.

    ``received_at`` is the system's receipt of the reviewed artifact (including
    its approval), so approval precedes receipt, and both must precede decision.
    Reviewed summaries cannot rewrite the attachment's evaluation boundaries.
    """
    errors = _protocol_errors(protocol) + candidate_reasons(candidate)
    def rejected(reason):
        return {'eligible': False, 'reasons': errors + [reason], 'review': None}
    if errors:
        return {'eligible': False, 'reasons': errors, 'review': None}
    if not isinstance(bundle, dict) or not isinstance(bundle.get('manifest'), dict) or not isinstance(bundle.get('report'), dict):
        return rejected('missing_review_attachment')
    manifest, report = bundle['manifest'], bundle['report']
    if manifest.get('schema_version') != 1 or isinstance(manifest.get('schema_version'), bool):
        return rejected('unsupported_review_schema')
    try:
        actual_report_hash, protocol_hash = canonical_sha256(report), canonical_sha256(protocol)
    except (TypeError, ValueError, OverflowError):
        return rejected('review_not_strict_json')
    declared_hash = manifest.get('report_sha256')
    if (not isinstance(declared_hash, str) or not re.fullmatch('[0-9a-f]{64}', declared_hash)
            or actual_report_hash != declared_hash):
        return rejected('report_hash_mismatch')
    if manifest.get('protocol_sha256') != protocol_hash or report.get('protocol_sha256') != protocol_hash:
        return rejected('protocol_hash_mismatch')
    if (candidate['model_version'] != protocol['model_version']
            or candidate['league'] not in protocol['leagues']):
        return rejected('candidate_protocol_domain_mismatch')
    if manifest.get('system') != 'C' or report.get('system') != 'C':
        return rejected('review_strategy_mismatch')
    if any(manifest.get(key) != candidate.get(key) or report.get(key) != candidate.get(key) for key in _DOMAIN):
        return rejected('review_domain_mismatch')
    if (manifest.get('data_mode') != 'observed' or report.get('data_mode') != 'observed'
            or manifest.get('result_split') != 'holdout' or report.get('result_split') != 'holdout'):
        return rejected('observed_independent_holdout_required')
    if (manifest.get('review_status') != 'approved' or manifest.get('independent_holdout') is not True
            or not _label(manifest.get('reviewed_by'))):
        return rejected('manual_independent_review_required')
    times = {key: _instant(manifest.get(key)) for key in (*_TIMES, 'reviewed_at', 'received_at')}
    if any(value is None for value in times.values()):
        return rejected('invalid_review_time')
    if any(_instant(report.get(key)) != times[key] for key in _TIMES):
        return rejected('attachment_time_mismatch')
    if times['protocol_frozen_at'] != _instant(protocol['protocol_frozen_at']):
        return rejected('protocol_freeze_mismatch')
    if not (times['training_end'] < times['holdout_start']
            and times['protocol_frozen_at'] < times['holdout_start']
            and times['holdout_start'] <= times['evaluation_start'] <= times['evaluation_end']
            and times['evaluation_end'] < times['reviewed_at'] <= times['received_at']
            <= _instant(candidate['decision_at'])):
        return rejected('future_or_overlapping_review_period')
    holdout = protocol.get('holdout')
    if not isinstance(holdout, dict):
        return rejected('predeclared_holdout_required')
    if (times['training_end'] != _instant(holdout.get('training_end'))
            or times['holdout_start'] != _instant(holdout.get('start'))
            or times['evaluation_start'] != _instant(holdout.get('start'))
            or times['evaluation_end'] != _instant(holdout.get('end'))):
        return rejected('full_predeclared_holdout_window_required')
    if report.get('training_provenance_status') != 'verified':
        return rejected('training_provenance_not_verified')
    if not _ids(report.get('training_case_ids')) or not _ids(report.get('training_fixture_ids')):
        return rejected('training_membership_required')
    results = report.get('results')
    if not isinstance(results, list) or not results:
        return rejected('settled_holdout_results_required')
    if not all(isinstance(row, dict) for row in results):
        return rejected('invalid_holdout_result')
    cases, fixtures = [row.get('case_id') for row in results], [row.get('fixture_id') for row in results]
    event_ids = [row.get('event_id') for row in results]
    if not _ids(cases) or not _ids(fixtures) or not _ids(event_ids):
        return rejected('duplicate_or_missing_holdout_identity')
    for row in results:
        if (row.get('data_mode') != 'observed' or row.get('quality_status') != 'eligible_observed'
                or row.get('result_split') != 'holdout'):
            return rejected('unqualified_or_synthetic_holdout_result')
        if not isinstance(row.get('outcome'), str) or row['outcome'] not in _SETTLED:
            return rejected('unsettled_holdout_result')
        if row.get('system') != 'C' or any(row.get(key) != candidate[key] for key in _DOMAIN):
            return rejected('holdout_result_domain_mismatch')
        decision, kickoff, result_receipt = [_instant(row.get(key)) for key in (
            'decision_at', 'kickoff_utc', 'result_received_at')]
        if (any(value is None for value in (decision, kickoff, result_receipt))
                or not (times['training_end'] < decision < kickoff < result_receipt <= times['reviewed_at'])
                or decision < times['protocol_frozen_at']
                or not times['evaluation_start'] <= kickoff <= times['evaluation_end']):
            return rejected('holdout_result_time_invalid')
    if (set(cases) & set(report['training_case_ids'])
            or set(fixtures) & set(report['training_fixture_ids'])):
        return rejected('train_holdout_identity_overlap')
    candidate_fixture = candidate.get('fixture_id', candidate['event_id'])
    all_fixtures = set(fixtures) | set(report['training_fixture_ids'])
    all_fixtures.update(row['event_id'] for row in results if _label(row.get('event_id')))
    if (candidate_fixture in all_fixtures or candidate['event_id'] in all_fixtures
            or _label(candidate.get('case_id')) and candidate['case_id'] in set(cases) | set(report['training_case_ids'])):
        return rejected('candidate_in_review_data')
    coverage = report.get('settlement_coverage')
    if (not isinstance(coverage, dict) or coverage.get('status') != 'complete'
            or type(coverage.get('selected_count')) is not int
            or type(coverage.get('settled_count')) is not int
            or type(coverage.get('pending_count')) is not int
            or coverage['selected_count'] != len(results)
            or coverage['settled_count'] != len(results) or coverage['pending_count'] != 0):
        return rejected('incomplete_or_unverified_settlement_coverage')
    count = manifest.get('sample_count')
    if (not isinstance(count, int) or isinstance(count, bool) or count != len(fixtures)
            or count < protocol['review_policy']['minimum_sample_count']):
        return rejected('insufficient_or_inconsistent_review_sample')
    return {
        'eligible': True, 'reasons': [],
        'review': {'report_sha256': actual_report_hash, 'protocol_sha256': protocol_hash,
                   'model_version': candidate['model_version'], 'league': candidate['league'],
                   'market': candidate['market'], 'system': 'C', 'sample_count': count,
                   'evaluation_end': times['evaluation_end'].isoformat(),
                   'reviewed_at': times['reviewed_at'].isoformat(),
                   'reviewed_by': manifest['reviewed_by'],
                   'received_at': times['received_at'].isoformat(),
                   'scope': 'manual_reviewed_holdout_history; not proof of profitability or calibration'},
    }


def rank_candidates(candidates, review_bundles=(), *, protocol):
    """Return reviewed candidates and a separate unvalidated research queue.

    Quality/baseline qualification is a gate; prior credible reviewed history
    precedes actual EV. All qualified rows have the same observed-quality class.
    Within that class use actual EV descending, then stable identity. No ROI,
    A-score, bookmaker price, new weights, or inferred probability affects rank.
    The historical backtest selector must never call this prospective helper.
    """
    errors = _protocol_errors(protocol)
    if errors:
        raise ValueError('Invalid predeclared review protocol: '+', '.join(errors))
    bundles = list(review_bundles)
    ranked, research, rejected = [], [], []
    for index, candidate in enumerate(candidates):
        reasons = candidate_reasons(candidate)
        if (isinstance(candidate, dict) and (candidate.get('model_version') != protocol['model_version']
                or candidate.get('league') not in protocol['leagues'])):
            reasons.append('candidate_protocol_domain_mismatch')
        entry = {'candidate': deepcopy(candidate), 'input_index': index,
                 'validation_status': 'rejected', 'reasons': reasons, 'review': None}
        if reasons:
            rejected.append(entry)
            continue
        reviews = [validate_review_artifact(bundle, candidate, protocol) for bundle in bundles]
        valid = [value['review'] for value in reviews if value['eligible']]
        if valid:
            # Latest completed evidence wins, never the report with the best ROI.
            entry['review'] = max(valid, key=lambda value: (
                value['evaluation_end'], value['reviewed_at'], value['report_sha256']))
            entry['validation_status'] = 'reviewed_history_available'
            ranked.append(entry)
        else:
            entry['validation_status'] = 'needs_validation'
            entry['reasons'] = sorted({reason for value in reviews for reason in value['reasons']}) or ['no_reviewed_holdout_history']
            research.append(entry)
    def order(entry):
        row = entry['candidate']
        return (-row['ev_pct'], row['event_id'], row['market'], row['model_version'], entry['input_index'])
    ranked.sort(key=order)
    research.sort(key=order)
    for position, entry in enumerate(ranked, 1):
        entry['rank'] = position
    return {'ranked': ranked, 'research_queue': research, 'rejected': rejected,
            'scope': 'prospective_offline_only; original baseline C eligibility preserved; no calibration/profit guarantee'}
