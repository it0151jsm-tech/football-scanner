"""Descriptive AH/OU settlement and model-B validation, with explicit units.

Settlement mathematics is supplied by the unchanged baseline callback. Calibration
uses its *three* outcome classes: full win, partial-or-push, full loss. A half win
and a half loss both belong to the middle class; profit sign is not a probability.
These reports do not establish calibration, significance, or future profitability.
"""
from collections import Counter
from collections.abc import Mapping
import math
from numbers import Real

MARKETS = {'AH': 'Asian Handicap', 'spreads': 'Asian Handicap', 'Asian Handicap': 'Asian Handicap',
           'OU': 'Over/Under', 'totals': 'Over/Under', 'Over/Under': 'Over/Under'}
CLASSES = ('full_win', 'partial_or_push', 'full_loss')
_LABELS = {'ชนะเต็ม': ('full_win', 'full_win', 1),
           'ชนะครึ่ง': ('half_win', 'partial_or_push', None),
           'คืนทุน': ('push', 'partial_or_push', None),
           'แพ้ครึ่ง': ('half_loss', 'partial_or_push', None),
           'แพ้เต็ม': ('full_loss', 'full_loss', 0)}
_OUTCOMES = {item[0]: item[1] for item in _LABELS.values()}


def _number(value):
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (ValueError, OverflowError, TypeError):
        return None


def _goals(value):
    number = _number(value)
    return int(number) if number is not None and number >= 0 and number.is_integer() else None


def _contract(quote):
    if not isinstance(quote, Mapping):
        return None, 'invalid_contract'
    market = MARKETS.get(quote.get('market')) if isinstance(quote.get('market'), str) else None
    if market is None:
        return None, 'unsupported_market'
    side = quote.get('side')
    if side not in (('home', 'away') if market == 'Asian Handicap' else ('over', 'under')):
        return None, 'invalid_side'
    line = _number(quote.get('point', quote.get('line')))
    scaled = line * 4 if line is not None else float('nan')
    if not math.isfinite(scaled) or abs(scaled - round(scaled)) > 1e-9:
        return None, 'invalid_line'
    if market == 'Over/Under' and line < 0:
        return None, 'invalid_line'
    odds = _number(quote.get('odds'))
    if odds is None or odds <= 1:
        return None, 'invalid_odds'
    return dict(market=market, side=side, point=line, odds=odds), None


def settlement_detail(quote, home_goals, away_goals, *, settle_pick, stake_units=1.0):
    """Call baseline ``settle_pick(row, hg, ag)`` once for a valid contract/result.

    The callback must return its existing (Thai label, one-unit net profit,
    full-win/full-loss binary or None) tuple. No settlement formula is duplicated.
    Missing results/contracts return excluded records and never enter denominators.
    """
    excluded = {'eligible': False, 'status': 'excluded', 'stake_units': 0.0,
                'profit_units': None, 'unit_profit': None, 'calibration_class': None}
    contract, reason = _contract(quote)
    if reason:
        return {**excluded, 'reason': reason}
    home, away = _goals(home_goals), _goals(away_goals)
    if home is None or away is None:
        return {**excluded, **contract, 'reason': 'unknown_or_invalid_result'}
    stake = _number(stake_units)
    if stake is None or stake <= 0:
        return {**excluded, **contract, 'reason': 'invalid_stake'}
    try:
        result = settle_pick(dict(contract), home, away)
    except Exception:
        return {**excluded, **contract, 'reason': 'baseline_settlement_error'}
    if not isinstance(result, (list, tuple)) or len(result) != 3:
        return {**excluded, **contract, 'reason': 'invalid_baseline_settlement'}
    label, raw_profit, binary = result
    kind = _LABELS.get(label) if isinstance(label, str) else None
    unit_profit = _number(raw_profit)
    if (kind is None or unit_profit is None or binary != kind[2]
            or isinstance(binary, bool) or unit_profit < -1 - 0.000051
            or unit_profit > contract['odds'] - 1 + 0.000051):
        return {**excluded, **contract, 'reason': 'invalid_baseline_settlement'}
    outcome, actual_class, _ = kind
    if ((outcome in ('full_win', 'half_win') and unit_profit <= 0)
            or (outcome in ('full_loss', 'half_loss') and unit_profit >= 0)
            or (outcome == 'push' and abs(unit_profit) > 1e-9)):
        return {**excluded, **contract, 'reason': 'invalid_baseline_settlement'}
    profit = stake * unit_profit
    if not math.isfinite(profit) or not math.isfinite(stake + profit):
        return {**excluded, **contract, 'reason': 'invalid_stake'}
    detail = {**contract, 'line': contract['point'], 'eligible': True, 'status': 'settled',
        'reason': None, 'home_goals': home, 'away_goals': away, 'stake_units': stake,
        'unit_profit': unit_profit, 'profit_units': profit, 'return_units': stake + profit,
        'outcome': outcome, 'calibration_class': actual_class, 'settlement_label': label}
    for key in ('league', 'sport_key', 'event_id', 'fixture_id', 'case_id', 'system', 'model_available'):
        if key in quote:
            detail[key] = quote[key]
    for key in ('probabilities', 'baseline_selection'):
        if isinstance(quote.get(key), Mapping):
            detail[key] = dict(quote[key])
    if isinstance(quote.get('selection'), Mapping):
        detail['baseline_selection'] = dict(quote['selection'])
    baseline = detail.get('baseline_selection', {})
    ev = _number(quote.get('ev_pct', baseline.get('ev_pct')))
    if ev is not None and not -100.000051 <= ev <= 100 * (contract['odds'] - 1) + .000051:
        ev = None
    detail['forecast_ev_pct'] = ev
    detail['forecast_ev_unit'] = ev / 100 if ev is not None else None
    return detail


def _valid_detail(record):
    if not isinstance(record, Mapping) or record.get('eligible') is not True or record.get('status') != 'settled':
        return 'not_settled'
    _, reason = _contract(record)
    if reason:
        return reason
    if _goals(record.get('home_goals')) is None or _goals(record.get('away_goals')) is None:
        return 'unknown_or_invalid_result'
    stake, profit = _number(record.get('stake_units')), _number(record.get('profit_units'))
    if stake is None or stake <= 0 or profit is None or not math.isfinite(stake + profit):
        return 'invalid_financials'
    if profit < -stake - .000051 * stake or profit > stake * (record['odds'] - 1) + .000051 * stake:
        return 'invalid_financials'
    outcome = record.get('outcome')
    if not isinstance(outcome, str) or outcome not in _OUTCOMES or record.get('calibration_class') != _OUTCOMES[outcome]:
        return 'invalid_outcome_class'
    return None


def _review_minimum(value):
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError('minimum_n_for_review must be a positive integer')
    return value


def _finite_sum(values):
    try:
        total = math.fsum(values)
        return total if math.isfinite(total) else None
    except (OverflowError, ValueError):
        return None


def _event_counts(records):
    identities = []
    for row in records:
        value = row.get('event_id') or row.get('fixture_id')
        if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value).strip():
            identities.append(str(value))
    return {'unique_event_n': len(set(identities)) if identities else None,
            'missing_event_identity_n': len(records) - len(identities),
            'correlation_warning': 'multiple bets on one fixture are not independent samples'}


def summarize_settlements(records, *, minimum_n_for_review=100):
    """ROI and yield both use *all* settled stakes, including full pushes.

    This is stake-based ROI, not a return on an unspecified starting bankroll.
    The minimum is an operational review flag, never a significance threshold.
    """
    minimum = _review_minimum(minimum_n_for_review)
    rows = list(records)
    good, excluded = [], Counter()
    for row in rows:
        reason = _valid_detail(row)
        if reason:
            excluded[reason] += 1
        else:
            good.append(row)
    n = len(good)
    stake = _finite_sum(row['stake_units'] for row in good)
    profit = _finite_sum(row['profit_units'] for row in good)
    total_return = _number(stake + profit) if stake is not None and profit is not None else None
    finite_totals = stake is not None and profit is not None and total_return is not None
    roi = _number(profit / stake * 100) if finite_totals and stake else None
    counts = {kind: 0 for kind in _OUTCOMES}
    for row in good:
        counts[row['outcome']] += 1
    ev_rows = [row for row in good if _number(row.get('forecast_ev_unit')) is not None
               and -1.00000051 <= row['forecast_ev_unit'] <= row['odds'] - 1 + .00000051]
    expected = _finite_sum(row['stake_units'] * row['forecast_ev_unit'] for row in ev_rows) if ev_rows else None
    return {'status': ('descriptive' if finite_totals and roi is not None else 'numerical_error') if n else 'insufficient',
        'n': n, **_event_counts(good),
        'input_n': len(rows), 'excluded_n': len(rows) - n, 'excluded_reasons': dict(excluded),
        'stake_units': stake, 'profit_units': profit, 'return_units': total_return,
        'roi_pct': roi, 'yield_pct': roi,
        'numerical_overflow': not finite_totals or bool(n and roi is None),
        'denominator': 'all settled stake units, including pushes; not initial bankroll',
        'outcome_counts': counts, 'expected_profit_units': expected, 'ev_n': len(ev_rows),
        'minimum_n_for_review': minimum, 'review_minimum_reached': n >= minimum,
        'calibrated': False, 'interpretation': 'descriptive; review minimum is not statistical significance'}


def validate_probabilities(probabilities, *, scale='fraction'):
    """Validate explicit three-class B probabilities; never normalize an A score.

    Baseline percentages are rounded to 0.01%, so sums of 99.99/100.01 are
    tolerated and explicitly normalized. Fraction inputs have a stricter 1e-9
    tolerance. Arbitrary scores or incomplete classes are never converted.
    """
    invalid = {'valid': False, 'probabilities': None, 'normalized': False}
    if not isinstance(probabilities, Mapping) or not all(key in probabilities for key in CLASSES):
        return {**invalid, 'reason': 'missing_model_probabilities'}
    if scale not in ('fraction', 'percent'):
        return {**invalid, 'reason': 'invalid_probability_scale'}
    values = {key: _number(probabilities[key]) for key in CLASSES}
    ceiling = 100 if scale == 'percent' else 1
    if any(value is None or not 0 <= value <= ceiling for value in values.values()):
        return {**invalid, 'reason': 'invalid_model_probabilities'}
    scaled = {key: value / ceiling for key, value in values.items()}
    total = math.fsum(scaled.values())
    tolerance = .000150000001 if scale == 'percent' else 1e-9
    if abs(total - 1) > tolerance or total <= 0:
        return {**invalid, 'reason': 'probabilities_do_not_sum_to_one', 'input_sum': total}
    normalized = abs(total - 1) > 1e-12
    return {'valid': True, 'reason': None, 'probabilities': {key: value / total for key, value in scaled.items()},
        'normalized': normalized, 'input_sum': total, 'scale': scale}


def _prediction(record):
    if record.get('system', 'B') != 'B':
        return {'valid': False, 'reason': 'not_system_b'}
    if record.get('model_available') is False:
        return {'valid': False, 'reason': 'model_unavailable'}
    if 'probabilities' in record:
        return validate_probabilities(record['probabilities'])
    selection = record.get('baseline_selection')
    if not isinstance(selection, Mapping):
        return {'valid': False, 'reason': 'missing_model_probabilities'}
    return validate_probabilities({key: selection.get(source)
        for key, source in zip(CLASSES, ('p_win', 'p_partial', 'p_loss'))}, scale='percent')


def calibration_metrics(records, *, bins=10, minimum_n_for_review=100):
    """Multiclass Brier=sum of three squared errors, log loss in natural logs.

    ECE is reported for each one-vs-rest class. ece_macro is their unweighted
    mean; predictions receive equal weight, independent of stake. Reliability
    bins include p=1 in the final bin. Log loss clips zero at 1e-15, reported.
    """
    minimum = _review_minimum(minimum_n_for_review)
    if not isinstance(bins, int) or isinstance(bins, bool) or not 1 <= bins <= 100:
        raise ValueError('bins must be an integer from 1 to 100')
    rows, accepted, accepted_records, excluded = list(records), [], [], Counter()
    normalized_n = 0
    for row in rows:
        reason = _valid_detail(row)
        if reason:
            excluded[reason] += 1
            continue
        prediction = _prediction(row)
        if not prediction['valid']:
            excluded[prediction['reason']] += 1
            continue
        normalized_n += int(prediction.get('normalized', False))
        accepted.append((prediction['probabilities'], row['calibration_class']))
        accepted_records.append(row)
    n = len(accepted)
    reliability, ece = {}, {}
    for key in CLASSES:
        groups = [[] for _ in range(bins)]
        for probabilities, truth in accepted:
            p = probabilities[key]
            groups[min(int(p * bins), bins - 1)].append((p, int(truth == key)))
        reliability[key] = []
        error = 0.0
        for index, group in enumerate(groups):
            count = len(group)
            mean = math.fsum(item[0] for item in group) / count if count else None
            observed = math.fsum(item[1] for item in group) / count if count else None
            reliability[key].append({'lower': index / bins, 'upper': (index + 1) / bins,
                'upper_inclusive': index == bins - 1, 'n': count,
                'mean_probability': mean, 'observed_frequency': observed})
            if count:
                error += count / n * abs(mean - observed)
        ece[key] = error if n else None
    epsilon = 1e-15
    brier = math.fsum(math.fsum((p[key] - int(truth == key)) ** 2 for key in CLASSES)
                       for p, truth in accepted) / n if n else None
    logloss = math.fsum(-math.log(max(epsilon, p[truth])) for p, truth in accepted) / n if n else None
    return {'status': 'descriptive' if n else 'insufficient', 'n': n, **_event_counts(accepted_records), 'input_n': len(rows),
        'excluded_n': len(rows) - n, 'excluded_reasons': dict(excluded), 'classes': list(CLASSES),
        'brier_multiclass': brier, 'brier_definition': 'mean sum of three squared errors; range 0 to 2',
        'log_loss': logloss, 'log_base': 'natural', 'log_loss_epsilon': epsilon,
        'zero_probability_truth_n': sum(p[truth] == 0 for p, truth in accepted),
        'ece_by_class': ece, 'ece_macro': math.fsum(ece.values()) / 3 if n else None,
        'ece_definition': 'one-vs-rest frequency bins per class; macro mean across three classes',
        'reliability_bins': reliability, 'normalized_rounded_prediction_n': normalized_n,
        'minimum_n_for_review': minimum, 'review_minimum_reached': n >= minimum,
        'calibrated': False, 'weighting': 'one settled prediction per weight; not stake-weighted',
        'interpretation': 'descriptive; review minimum is not statistical significance'}


def report_by_market_and_league(records, *, bins=10, minimum_n_for_review=100):
    """Report overall and isolated AH/OU + league cohorts without pooling labels."""
    rows = list(records)
    def report(cohort):
        return {'settlement': summarize_settlements(cohort, minimum_n_for_review=minimum_n_for_review),
                'calibration': calibration_metrics(cohort, bins=bins, minimum_n_for_review=minimum_n_for_review)}
    by_market = {market: report([row for row in rows if isinstance(row, Mapping) and row.get('market') == market])
                 for market in ('Asian Handicap', 'Over/Under')}
    leagues = sorted({str(row.get('league') or row.get('sport_key')) for row in rows
                      if isinstance(row, Mapping) and (row.get('league') or row.get('sport_key'))})
    by_league = {}
    for league in leagues:
        cohort = [row for row in rows if isinstance(row, Mapping) and str(row.get('league') or row.get('sport_key')) == league]
        by_league[league] = {'overall': report(cohort),
            'by_market': {market: report([row for row in cohort if row.get('market') == market])
                          for market in ('Asian Handicap', 'Over/Under')}}
    return {'overall': report(rows), 'by_market': by_market, 'by_league': by_league}
