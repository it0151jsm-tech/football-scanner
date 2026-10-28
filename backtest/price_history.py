"""Read-only, point-in-time price evidence and report-only CLV.

No network, database, model, or forecast generation occurs here. Decimal CLV
compares the identical selection and line only; a changed line is reported in
points, without converting it into an odds, probability, EV, or profit estimate.
"""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
import re
from urllib.parse import parse_qsl, urlsplit

UTC = timezone.utc
MARKETS = ('Asian Handicap', 'Over/Under')
IDENTITY = ('event_id', 'league', 'season', 'home_team', 'away_team', 'market',
            'side', 'bookmaker', 'bookmaker_key')
TIMES = ('source_updated_at', 'received_at', 'captured_at')
SHA256 = re.compile(r'^[a-fA-F0-9]{64}$')
SECRET_QUERY_KEYS = {'key', 'api_key', 'apikey', 'token', 'access_token', 'x-apisports-key'}


def _instant(value):
    try:
        stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return stamp.astimezone(UTC) if stamp.tzinfo is not None and stamp.utcoffset() is not None else None
    except (ValueError, TypeError, OverflowError):
        return None


def _finite(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except (OverflowError, ValueError, TypeError):
        return False


def _point(value, market):
    return (_finite(value) and math.isfinite(value * 4) and abs(value * 4-round(value * 4)) < 1e-9
            and (market != 'Over/Under' or value >= 0))


def _identity(row):
    if not isinstance(row, dict) or any(not isinstance(row.get(key), str) or not row[key].strip() for key in IDENTITY):
        return None
    market, side = row['market'], row['side']
    if market not in MARKETS or side not in (('home', 'away') if market == MARKETS[0] else ('over', 'under')):
        return None
    kickoff = _instant(row.get('kickoff_utc'))
    if kickoff is None or row['home_team'] == row['away_team']:
        return None
    return tuple(row[key] for key in IDENTITY) + (kickoff.isoformat(),)


def _proof(evidence, deadline, *, strict=False):
    if not isinstance(evidence, dict):
        return False, 'missing_provider_evidence'
    url, checksum = evidence.get('url'), evidence.get('sha256', evidence.get('payload_sha256'))
    try:
        parsed = urlsplit(url) if isinstance(url, str) else None
        url_ok = (parsed is not None and parsed.scheme in ('http', 'https') and bool(parsed.hostname)
                  and parsed.username is None and parsed.password is None
                  and not any(key.lower() in SECRET_QUERY_KEYS for key, _ in parse_qsl(parsed.query)))
    except ValueError:
        url_ok = False
    if not url_ok or not isinstance(checksum, str) or not SHA256.fullmatch(checksum):
        return False, 'invalid_provider_evidence'
    received = _instant(evidence.get('received_at'))
    if received is None:
        return False, 'unverified_evidence_receipt'
    if received > deadline or (strict and received == deadline):
        return False, 'evidence_after_cutoff'
    return True, ''


def _evidence_public(value):
    if not isinstance(value, dict):
        return None
    # Never propagate arbitrary headers, raw payloads, or credentials to reports.
    result = {}
    for key in ('sha256', 'payload_sha256', 'provider'):
        if isinstance(value.get(key), str):
            result[key] = value[key]
    received = _instant(value.get('received_at'))
    result['received_at'] = received.isoformat() if received is not None else None
    try:
        parsed = urlsplit(value.get('url', ''))
        safe = parsed.scheme in ('http', 'https') and parsed.hostname and not parsed.username and not parsed.password
        safe = safe and not any(key.lower() in SECRET_QUERY_KEYS for key, _ in parse_qsl(parsed.query))
    except (ValueError, TypeError):
        safe = False
    result['url'] = value.get('url') if safe else None
    return result


def _public(quote):
    if quote is None:
        return None
    result = {key: quote[key] for key in IDENTITY + ('quote_id', 'point', 'odds', 'source',
              'source_payload_sha256', 'opening_verified', 'closing_verified', 'quote_kind',
              'snapshot_group_id', 'batch_id', 'snapshot_id') if key in quote}
    for key in TIMES + ('kickoff_utc',):
        result[key] = quote['_times'][key].isoformat()
    for key in ('opening_evidence', 'closing_evidence'):
        if key in quote:
            result[key] = _evidence_public(quote[key])
    return result


def _quote(row):
    identity = _identity(row)
    if identity is None:
        return None, 'invalid_identity'
    quote_id = row.get('quote_id')
    if (not isinstance(quote_id, (str, int)) or isinstance(quote_id, bool)
            or not str(quote_id).strip() or (isinstance(quote_id, int) and quote_id <= 0)):
        return None, 'missing_quote_id'
    if not _point(row.get('point'), row['market']) or not _finite(row.get('odds')) or row['odds'] <= 1:
        return None, 'invalid_line_or_odds'
    if (not isinstance(row.get('source'), str) or not row['source'].strip()
            or not isinstance(row.get('source_payload_sha256'), str)
            or not SHA256.fullmatch(row['source_payload_sha256'])):
        return None, 'missing_source_evidence'
    if row.get('quote_kind') not in ('observed', 'opening', 'closing'):
        return None, 'invalid_quote_kind'
    if any(key in row and not isinstance(row[key], bool) for key in ('opening_verified', 'closing_verified')):
        return None, 'invalid_verification_flag'
    if any(row.get(key) is not None and (not isinstance(row[key], str) or not row[key].strip())
           for key in ('snapshot_group_id', 'batch_id')):
        return None, 'invalid_snapshot_group'
    if row.get('snapshot_id') is not None and (not isinstance(row['snapshot_id'], (str, int)) or isinstance(row['snapshot_id'], bool)):
        return None, 'invalid_snapshot_identity'
    stamps = {key: _instant(row.get(key)) for key in TIMES + ('kickoff_utc',)}
    if any(stamp is None for stamp in stamps.values()):
        return None, 'unverified_quote_time'
    if not stamps['source_updated_at'] <= stamps['received_at'] <= stamps['captured_at']:
        return None, 'invalid_timestamp_order'
    if any(stamps[key] >= stamps['kickoff_utc'] for key in TIMES):
        return None, 'not_pre_kickoff'
    out = dict(row, _identity=identity, _times=stamps)
    return out, ''


def _signature(quote):
    return (quote['_identity'], quote['point'], quote['odds'], quote['source'],
            quote['source_payload_sha256'].lower(), quote['quote_kind'],
            tuple(quote['_times'][key].isoformat() for key in TIMES),
            json.dumps({key: quote.get(key) for key in ('opening_verified', 'closing_verified',
                       'snapshot_group_id', 'batch_id', 'snapshot_id', 'opening_evidence', 'closing_evidence')},
                       sort_keys=True, default=str))


def _validated(rows, *, identity=None, decision=None):
    """Reconcile only observations eligible for this decision.

    Later corrections and outcome-only closing evidence must not retroactively
    remove a price that was available to an earlier decision. Contract-scoped
    reconciliation also prevents an unrelated market/book from poisoning it.
    """
    accepted, reasons = [], Counter()
    ids, contracts = defaultdict(set), defaultdict(set)
    for row in rows:
        quote, reason = _quote(row)
        if quote is None:
            reasons[reason] += 1
            continue
        if identity is not None and quote['_identity'] != identity:
            reasons['contract_mismatch'] += 1
            continue
        if decision is not None:
            if quote['quote_kind'] == 'closing':
                reasons['closing_outcome_only'] += 1
                continue
            if any(quote['_times'][key] > decision for key in TIMES):
                reasons['unavailable_at_decision'] += 1
                continue
        accepted.append(quote)
        ids[str(quote['quote_id'])].add(_signature(quote))
        key = (quote['_identity'], quote['point'], tuple(quote['_times'][field] for field in TIMES),
               quote['source'], quote['source_payload_sha256'].lower())
        contracts[key].add(quote['odds'])
    clean, seen = [], set()
    for quote in accepted:
        key = (quote['_identity'], quote['point'], tuple(quote['_times'][field] for field in TIMES),
               quote['source'], quote['source_payload_sha256'].lower())
        if len(ids[str(quote['quote_id'])]) > 1:
            reasons['conflicting_quote_id'] += 1
        elif len(contracts[key]) > 1:
            reasons['conflicting_same_snapshot_price'] += 1
        elif _signature(quote) in seen:
            reasons['identical_duplicate'] += 1
        else:
            seen.add(_signature(quote))
            clean.append(quote)
    return clean, dict(reasons)


def _batch(quote):
    return quote['_times']['received_at'], quote['_times']['captured_at']


def _choose_line(batch, preferred=None):
    same = [quote for quote in batch if preferred is not None and quote['point'] == preferred]
    choices = same or batch
    signatures = {(quote['point'], quote['odds'], quote['source_payload_sha256'].lower(),
                   quote['_times']['source_updated_at']) for quote in choices}
    if len(signatures) != 1:
        return None, 'ambiguous_snapshot_or_lines'
    return min(choices, key=lambda quote: str(quote['quote_id'])), ''


def _fresh(quote, cutoff, max_age):
    return all(0 <= (cutoff-quote['_times'][key]).total_seconds() <= max_age for key in TIMES)


def _age_valid(value):
    return _finite(value) and 0 < value <= 900


def select_price_history(quotes, selection, decision_at, max_age_seconds=900):
    """Opening/first/current evidence available at the decision, never closing.

    Selection point, when present, chooses a line only from the latest observed
    snapshot. It cannot resurrect a removed line from an older snapshot.
    """
    result = dict(status='unavailable', reason=None, decision_quote=None,
                  first_observed_quote=None, first_observed_label='First Observed Price',
                  opening_quote=None, opening_verified=False, opening_label=None,
                  feature_use='decision_time_only', diagnostics={})
    identity, decision = _identity(selection), _instant(decision_at)
    if identity is None or decision is None or not _age_valid(max_age_seconds):
        result['reason'] = 'invalid_contract_decision_or_max_age'
        return result
    if decision >= _instant(selection['kickoff_utc']):
        result['reason'] = 'decision_not_before_kickoff'
        return result
    preferred = selection.get('point')
    if preferred is not None and not _point(preferred, selection['market']):
        result['reason'] = 'invalid_selected_line'
        return result
    history, rejected = _validated(quotes, identity=identity, decision=decision)
    exclusions = Counter(rejected)
    result['diagnostics'] = dict(rejected=exclusions, compatible_asof_quotes=len(history))
    if not history:
        result['reason'] = 'no_verified_quote_at_decision'
        return result
    first_batch = min(_batch(quote) for quote in history)
    first, first_reason = _choose_line([quote for quote in history if _batch(quote) == first_batch], preferred)
    result['first_observed_quote'] = _public(first)
    result['diagnostics']['first_observed_reason'] = first_reason or 'first_recorded_observation_not_opening'
    opening, opening_reasons = [], Counter()
    for quote in history:
        if quote.get('opening_verified') is not True or quote['quote_kind'] != 'opening':
            continue
        ok, reason = _proof(quote.get('opening_evidence'), decision)
        if ok:
            opening.append(quote)
        else:
            opening_reasons[reason] += 1
    if opening:
        relevant = [quote for quote in opening if quote['point'] == preferred] if preferred is not None else opening
        relevant = relevant or opening
        if len({(quote['point'], quote['odds']) for quote in relevant}) > 1:
            opened, reason = None, 'conflicting_verified_opening'
        else:
            first_opening = min(_batch(quote) for quote in relevant)
            opened, reason = _choose_line([quote for quote in relevant if _batch(quote) == first_opening], preferred)
        result.update(opening_quote=_public(opened), opening_verified=opened is not None,
                      opening_label='Opening Odds' if opened is not None else None)
        if reason:
            opening_reasons[reason] += 1
    result['diagnostics']['opening_rejected'] = dict(opening_reasons)
    last_batch = max(_batch(quote) for quote in history)
    latest = [quote for quote in history if _batch(quote) == last_batch]
    if preferred is not None and not any(quote['point'] == preferred for quote in latest):
        result['reason'] = 'selected_line_absent_latest_snapshot'
        return result
    current, reason = _choose_line(latest, preferred)
    if current is None or not _fresh(current, decision, max_age_seconds):
        result['reason'] = reason or 'stale_decision_quote'
        return result
    result.update(status='available', reason='verified_quote_available_at_decision', decision_quote=_public(current))
    return result


def _group(quote):
    return quote.get('snapshot_group_id') or quote.get('batch_id')


def _paired(selected, quotes, cutoff, max_age):
    opposite = {'home': 'away', 'away': 'home', 'over': 'under', 'under': 'over'}[selected['side']]
    wanted = dict(selected, side=opposite)
    identity = _identity(wanted)
    point = -selected['point'] if selected['market'] == 'Asian Handicap' else selected['point']
    compatible = []
    for other in quotes:
        if other['_identity'] != identity or other['point'] != point or not _fresh(other, cutoff, max_age):
            continue
        a, b = _group(selected), _group(other)
        if a and b and a != b:
            continue
        exact = (all(selected['_times'][key] == other['_times'][key] for key in TIMES)
                 and selected['source_payload_sha256'].lower() == other['source_payload_sha256'].lower())
        explicit = (isinstance(a, str) and bool(a) and a == b and
                    all(abs((selected['_times'][key]-other['_times'][key]).total_seconds()) <= 30 for key in TIMES))
        if selected['source'] == other['source'] and (exact or explicit):
            compatible.append(other)
    if not compatible:
        return None, 'missing_compatible_opposite_quote'
    if len({other['odds'] for other in compatible}) != 1:
        return None, 'conflicting_opposite_quotes'
    return min(compatible, key=lambda quote: str(quote['quote_id'])), ''


def closing_line_value(entry_quote, quotes, decision_at, max_close_age_seconds=900):
    """Report last observed pre-kickoff movement; never return ranking features.

    Actual closing requires provider evidence for both compatible sides. A
    same-line observed quote may still support explicitly labelled raw decimal
    CLV; paired no-vig price shares are not probabilities from a football model.
    """
    result = dict(status='unavailable', reason=None, feature_use='outcome_report_only',
                  clv_decimal=None, clv_definition='entry_decimal_odds / closing_decimal_odds - 1; identical line only',
                  closing_quote=None, closing_label=None, closing_verified=False,
                  line_change=None, no_vig=None, diagnostics={})
    entry, reason = _quote(entry_quote)
    decision = _instant(decision_at)
    if entry is None or decision is None or not _age_valid(max_close_age_seconds):
        result['reason'] = reason or 'invalid_decision_or_max_age'
        return result
    kickoff = entry['_times']['kickoff_utc']
    if decision >= kickoff or entry['quote_kind'] == 'closing' or not _fresh(entry, decision, 900):
        result['reason'] = 'entry_not_verified_at_decision'
        return result
    valid, rejected = _validated(quotes)
    matching = [quote for quote in valid if quote['_identity'] == entry['_identity']]
    result['diagnostics']['rejected'] = rejected
    if not matching:
        result['reason'] = 'no_compatible_pre_kickoff_snapshot'
        return result
    latest = max(_batch(quote) for quote in matching)
    close, reason = _choose_line([quote for quote in matching if _batch(quote) == latest], entry['point'])
    if close is None or not _fresh(close, kickoff, max_close_age_seconds):
        result['reason'] = reason or 'closing_observation_too_old'
        return result
    if any(close['_times'][key] < entry['_times'][key] for key in ('received_at', 'captured_at')):
        result['reason'] = 'closing_snapshot_precedes_entry'
        return result
    result.update(status='available', reason='last_observed_pre_kickoff_not_assumed_true_closing',
                  closing_quote=_public(close), closing_label='last_observed_pre_kickoff')
    entry_pair, entry_pair_reason = _paired(entry, valid, decision, 900)
    close_pair, close_pair_reason = _paired(close, valid, kickoff, max_close_age_seconds)
    result['diagnostics'].update(entry_pair_reason=entry_pair_reason, closing_pair_reason=close_pair_reason)
    proof_ok, proof_reason = _proof(close.get('closing_evidence'), kickoff, strict=True)
    opposite_proof_ok, opposite_proof_reason = _proof(close_pair.get('closing_evidence') if close_pair else None, kickoff, strict=True)
    if (close.get('closing_verified') is True and close['quote_kind'] == 'closing' and proof_ok and
            close_pair is not None and close_pair.get('closing_verified') is True and
            close_pair['quote_kind'] == 'closing' and opposite_proof_ok):
        result.update(closing_verified=True, closing_label='verified_closing', reason='provider_verified_closing_pair')
    else:
        result['diagnostics']['closing_evidence_reason'] = (proof_reason or opposite_proof_reason or
                                                          'both_sides_not_provider_verified_closing')
    delta = close['point'] - entry['point']
    direction = -1 if entry['market'] == 'Over/Under' and entry['side'] == 'over' else 1
    result['line_change'] = dict(entry_point=entry['point'], closing_point=close['point'], point_delta=delta,
        closing_line_favorability_delta=direction * delta, entry_line_advantage=-direction * delta,
        definition='Positive closing_line_favorability_delta means the closing offer gives this selection a more favorable line; entry_line_advantage is the opposite. No equivalent odds/EV is inferred.')
    if close['point'] != entry['point']:
        result['reason'] = 'line_changed_no_equivalent_odds_clv'
        return result
    result['clv_decimal'] = entry['odds'] / close['odds'] - 1
    if not math.isfinite(result['clv_decimal']):
        result.update(clv_decimal=None, reason='nonfinite_clv')
    if entry_pair is not None and close_pair is not None:
        entry_share = (1 / entry['odds']) / (1 / entry['odds'] + 1 / entry_pair['odds'])
        close_share = (1 / close['odds']) / (1 / close['odds'] + 1 / close_pair['odds'])
        ratio = close_share / entry_share-1
        if not all(math.isfinite(value) for value in (entry_share, close_share, ratio)):
            result['diagnostics']['no_vig_reason'] = 'nonfinite_price_share'
            return result
        result['no_vig'] = dict(entry_implied_share=entry_share, closing_implied_share=close_share,
            implied_share_change=close_share-entry_share, ratio_change=ratio,
            entry_opposite_quote=_public(entry_pair), closing_opposite_quote=_public(close_pair),
            definition='Normalize reciprocal decimal prices within each compatible two-sided snapshot. Ratio change = closing share / entry share - 1. These are market price shares, not win probabilities or model EV, especially on push/quarter lines.')
    return result
