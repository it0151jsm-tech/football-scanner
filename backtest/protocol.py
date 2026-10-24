"""Predeclared evaluation settings; no model fitting or outcome-driven tuning."""
import hashlib
import json
from datetime import datetime, timezone

MAJOR_LEAGUES = ('soccer_epl', 'soccer_spain_la_liga', 'soccer_italy_serie_a',
                 'soccer_germany_bundesliga', 'soccer_france_ligue_one')


def canonical_sha256(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def utc(value):
    try:
        result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return result.astimezone(timezone.utc) if result.tzinfo is not None else None
    except (ValueError, TypeError, OverflowError):
        return None


def validate_protocol(value, model_version):
    if not isinstance(value, dict) or type(value.get('schema_version')) is not int or value.get('schema_version') != 1:
        raise ValueError('invalid_protocol_schema')
    if value.get('model_version') != model_version:
        raise ValueError('protocol_model_version_mismatch')
    if utc(value.get('protocol_frozen_at')) is None:
        raise ValueError('protocol_time_unverified')
    if value.get('unit_stake') != 1 or isinstance(value.get('unit_stake'), bool):
        raise ValueError('only_predeclared_unit_stake_supported')
    if value.get('quote_max_age_seconds') != 900:
        raise ValueError('baseline_price_freshness_must_remain_900_seconds')
    if value.get('strategies') != {'A': 'evidence_55_sample5', 'B': 'positive_ev', 'C': 'original_market_policy'}:
        raise ValueError('unsupported_strategy_or_threshold_change')
    if value.get('selection_rule') != 'one_per_event_market_quality_ev_stable_contract':
        raise ValueError('selection_rule_not_predeclared')
    policy = value.get('review_policy')
    minimum = policy.get('minimum_sample_count') if isinstance(policy, dict) else None
    if type(minimum) is not int or minimum < 1:
        raise ValueError('review_minimum_not_predeclared')
    bins = value.get('calibration_bins')
    if type(bins) is not int or not 2 <= bins <= 20:
        raise ValueError('invalid_calibration_bins')
    leagues = value.get('leagues')
    if (not isinstance(leagues, list) or not leagues or any(not isinstance(league,str) or league not in MAJOR_LEAGUES for league in leagues)
            or len(leagues) != len(set(leagues))):
        raise ValueError('unsupported_league_scope')
    holdout = value.get('holdout')
    if holdout is not None:
        if not isinstance(holdout, dict):
            raise ValueError('invalid_holdout')
        train, start, end = [utc(holdout.get(key)) for key in ('training_end', 'start', 'end')]
        frozen = utc(value['protocol_frozen_at'])
        if any(t is None for t in (train, start, end)) or not train < start <= end or not frozen < start:
            raise ValueError('holdout_not_frozen_before_unseen_period')
    return value


def default_protocol(model_version, *, frozen_at=None):
    frozen_at = frozen_at or datetime.now(timezone.utc).isoformat()
    return {'schema_version': 1, 'protocol_id': 'pr6-fixed-baseline-v1',
        'model_version': model_version, 'protocol_frozen_at': frozen_at,
        'unit_stake': 1, 'quote_max_age_seconds': 900,
        'strategies': {'A': 'evidence_55_sample5', 'B': 'positive_ev', 'C': 'original_market_policy'},
        'selection_rule': 'one_per_event_market_quality_ev_stable_contract',
        'calibration_bins': 10, 'review_policy': {'minimum_sample_count': 100},
        'leagues': list(MAJOR_LEAGUES), 'holdout': None,
        'interpretation': 'Minimum sample count is a predeclared review threshold, not proof of profitability or calibration.'}
