"""Read-only projections for the Data Center dashboard.

This module neither calculates a prediction nor fetches or stores anything. An
existing analysis is shown only for the exact fixture, price contract and source
clocks displayed on the card. In particular, a nearby line, another bookmaker,
or a previous observation is not a substitute for a matching result.
"""
from copy import deepcopy
from datetime import datetime, timezone
from html import escape
import json
import math
from numbers import Real


def _text(value):
    return value if isinstance(value, str) and value.strip() else None


def _number(value, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    try:
        value = float(value)
        if not math.isfinite(value):
            return None
    except (ValueError, OverflowError):
        return None
    if minimum is not None and value < minimum:
        return None
    if maximum is not None and value > maximum:
        return None
    return value


def _instant(value):
    try:
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            return None
        return value.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _fixture_key(value):
    fields = tuple(_text(value.get(key)) for key in
                   ('event_id', 'league', 'home_team', 'away_team'))
    kickoff = _instant(value.get('kickoff_utc'))
    if any(field is None for field in fields) or kickoff is None or fields[2] == fields[3]:
        return None
    return (*fields, kickoff)


def _contract_key(value):
    market, side = value.get('market'), value.get('side')
    if market not in ('Asian Handicap', 'Over/Under'):
        return None
    if side not in (('home', 'away') if market == 'Asian Handicap' else ('over', 'under')):
        return None
    point, odds = _number(value.get('point')), _number(value.get('odds'))
    if point is None or odds is None or odds <= 1:
        return None
    if not math.isfinite(point * 4) or abs(point * 4 - round(point * 4)) > 1e-9 or (market == 'Over/Under' and point < 0):
        return None
    book, key = _text(value.get('bookmaker')), _text(value.get('bookmaker_key'))
    received, updated = _instant(value.get('received_at')), _instant(value.get('source_updated_at'))
    scope = value.get('source_update_scope')
    if not book or not key or received is None or updated is None or scope not in ('market', 'bookmaker'):
        return None
    return (market, side, point, odds, book, key, received, updated, scope)


def _json_dict(value):
    if isinstance(value, dict):
        return deepcopy(value)
    if not isinstance(value, str):
        return {}
    try:
        def invalid_constant(_):
            raise ValueError('nonfinite_json')
        result = json.loads(value, parse_constant=invalid_constant)
        return result if isinstance(result, dict) else {}
    except (TypeError, ValueError, RecursionError):
        return {}


def _records(rows):
    if rows is None:
        return []
    if hasattr(rows, 'to_dict'):
        try:
            rows = rows.to_dict('records')
        except (TypeError, ValueError, AttributeError):
            return []
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, (list, tuple)) else []


def _analysis(row, quote):
    a, b = _json_dict(row.get('analysis_a_json')), _json_dict(row.get('analysis_b_json'))
    if not a and not b:
        return None
    result = {key: deepcopy(row.get(key)) for key in
              ('market', 'side', 'point', 'odds', 'bookmaker', 'bookmaker_key',
               'selection', 'recommendation', 'analysis_reason', 'model_status',
               'received_at', 'source_updated_at', 'source_update_scope', 'scanned_at')}
    result.update(analysis_a=a, analysis_b=b,
                  freshness_status=quote.get('freshness_status'))
    # confidence_score and score in the legacy table duplicate A's score. They
    # must not be relabelled as a new, independently calculated score for C.
    return result


def _fingerprint(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                          default=lambda item: item.isoformat() if isinstance(item, datetime) else None)
    except (TypeError, ValueError, RecursionError):
        return None


def project_cards(cards, analysis_rows=None):
    """Return independent display cards; attach no unproven or ambiguous result.

    Nothing is ranked here. Multiple stored records for one exact quote are
    accepted only when their displayed analysis agrees; conflicting versions
    remain unavailable rather than silently selecting a preferred result.
    """
    projected = [deepcopy(card) for card in cards if isinstance(card, dict)] if isinstance(cards, (list, tuple)) else []
    indexed = {}
    for row in _records(analysis_rows):
        fixture, contract = _fixture_key(row), _contract_key(row)
        if fixture is not None and contract is not None:
            indexed.setdefault((fixture, contract), []).append(row)
    for card in projected:
        card['display_analysis'] = []
        fixture = _fixture_key(card)
        if fixture is None:
            continue
        seen = set()
        for quote in card.get('quotes', []) if isinstance(card.get('quotes'), list) else []:
            if not isinstance(quote, dict):
                continue
            contract = _contract_key(quote)
            if contract is None or contract in seen:
                continue
            seen.add(contract)
            versions = [_analysis(row, quote) for row in indexed.get((fixture, contract), [])]
            if not versions or any(version is None for version in versions):
                continue
            fingerprints = {_fingerprint(version) for version in versions}
            if None not in fingerprints and len(fingerprints) == 1:
                card['display_analysis'].append(versions[0])
    return projected


def _has_statistics(card):
    statistics = card.get('statistics')
    if not isinstance(statistics, dict):
        return False
    teams = statistics.get('teams')
    for team in teams.values() if isinstance(teams, dict) else []:
        if not isinstance(team, dict):
            continue
        for category in ('overall', 'venue'):
            windows = team.get(category)
            for window in windows.values() if isinstance(windows, dict) else []:
                if (isinstance(window, dict) and type(window.get('sample')) is int and window['sample'] > 0
                        and _number(window.get('gf_avg'), 0) is not None
                        and _number(window.get('ga_avg'), 0) is not None):
                    return True
        xg = team.get('xg')
        for category in ('overall', 'venue'):
            windows = xg.get(category) if isinstance(xg, dict) else None
            for window in windows.values() if isinstance(windows, dict) else []:
                if (isinstance(window, dict) and type(window.get('sample')) is int and window['sample'] > 0
                        and _number(window.get('xg_avg'), 0) is not None
                        and _number(window.get('xga_avg'), 0) is not None):
                    return True
    h2h = statistics.get('h2h')
    if isinstance(h2h, dict) and type(h2h.get('sample')) is int and h2h['sample'] > 0:
        for match in h2h.get('matches', []) if isinstance(h2h.get('matches'), list) else []:
            if (isinstance(match, dict) and _text(match.get('date'))
                    and _text(match.get('home_team')) and _text(match.get('away_team'))
                    and _number(match.get('home_goals'), 0) is not None
                    and _number(match.get('away_goals'), 0) is not None):
                return True
    return False


def summary_counts(cards):
    """Count actual displayed fixture identities, never quote or status rows."""
    fixtures, priced, statistics = set(), set(), set()
    for card in cards if isinstance(cards, (list, tuple)) else []:
        if not isinstance(card, dict):
            continue
        key = _fixture_key(card)
        if key is None:
            continue
        fixtures.add(key)
        for quote in card.get('quotes', []) if isinstance(card.get('quotes'), list) else []:
            if not isinstance(quote, dict):
                continue
            point, odds = _number(quote.get('point')), _number(quote.get('odds'))
            market = quote.get('market')
            valid_side = quote.get('side') in (('home', 'away') if market == 'Asian Handicap' else ('over', 'under'))
            if (market in ('Asian Handicap', 'Over/Under') and valid_side and point is not None
                    and math.isfinite(point * 4) and abs(point * 4 - round(point * 4)) <= 1e-9
                    and (market != 'Over/Under' or point >= 0) and odds is not None and odds > 1):
                priced.add(key)
                break
        if _has_statistics(card):
            statistics.add(key)
    return {'fixtures': len(fixtures), 'priced': len(priced), 'statistics': len(statistics)}


def _escaped(value, missing='ไม่มีข้อมูล'):
    return escape(_text(value) or missing, quote=True)


def analysis_summary_html(card):
    """Display existing A evidence, B EV and C text without making a new score."""
    rows = card.get('display_analysis') if isinstance(card, dict) else None
    rows = rows if isinstance(rows, list) else []
    if not rows:
        return '<section class="dc-analysis"><p class="dc-muted">ยังไม่มีผลวิเคราะห์ที่ตรง Snapshot นี้</p></section>'
    output = ['<details class="dc-analysis"><summary>ดูผลและเหตุผลการวิเคราะห์ A/B/C</summary>',
              '<p class="dc-analysis-note">คะแนนหลักฐานไม่ใช่ความน่าจะเป็นชนะที่ผ่านการปรับเทียบ; '
              'ค่าความน่าจะเป็นของโมเดลที่แสดงยังไม่ผ่าน Calibration</p>']
    for row in rows:
        if not isinstance(row, dict):
            continue
        a, b = row.get('analysis_a'), row.get('analysis_b')
        a, b = a if isinstance(a, dict) else {}, b if isinstance(b, dict) else {}
        score = _number(a.get('score'), 0, 100)
        selection = b.get('selection') if isinstance(b.get('selection'), dict) else {}
        ev = _number(selection.get('ev_pct')) if b.get('available') is True else None
        market = 'AH' if row.get('market') == 'Asian Handicap' else 'OU' if row.get('market') == 'Over/Under' else 'ไม่มีข้อมูลตลาด'
        point, odds = _number(row.get('point')), _number(row.get('odds'))
        label = row.get('selection') or {'home': card.get('home_team'), 'away': card.get('away_team'), 'over': 'สูง', 'under': 'ต่ำ'}.get(row.get('side'))
        output.append('<section class="dc-analysis-contract" data-dc-analysis-market="' + market + '"><p><strong>'
                      + market + ' · ' + _escaped(label) + '</strong> · เส้น '
                      + (f'{point:+g}' if point is not None and market == 'AH' else f'{point:g}' if point is not None else 'ไม่มีข้อมูล')
                      + ' · ราคา ' + (f'{odds:.2f}' if odds is not None else 'ไม่มีข้อมูล') + ' · '
                      + _escaped(row.get('bookmaker')) + '</p><div class="dc-analysis-grid">')
        output.append('<div class="dc-analysis-item"><span>ระบบ A · คะแนนหลักฐาน A</span><strong>'
                      + (f'{score:.1f}/100' if score is not None else 'ไม่มีคะแนนที่คำนวณได้')
                      + '</strong><p>' + _escaped(a.get('result')) + '</p></div>')
        output.append('<div class="dc-analysis-item"><span>ระบบ B</span><strong>'
                      + (f'EV {ev:+.2f}%' if ev is not None else 'ยังไม่มีค่าจากโมเดลที่พร้อมใช้')
                      + '</strong><p>EV ไม่ใช่โอกาสชนะ</p></div>')
        output.append('<div class="dc-analysis-item"><span>ระบบ C</span><strong>'
                      + _escaped(row.get('recommendation'), 'ยังไม่มีผลสรุปที่บันทึกไว้')
                      + '</strong><p>สรุป A/B ที่บันทึกไว้ ไม่มีคะแนน C แยกต่างหาก</p></div></div>')
        hda = b.get('hda') if isinstance(b.get('hda'), dict) else {}
        probabilities = [_number(hda.get(key), 0, 100) for key in ('home', 'draw', 'away')]
        if b.get('available') is True and all(value is not None for value in probabilities) and abs(sum(probabilities) - 100) <= .1:
            output.append('<p class="dc-analysis-note">โอกาสจากโมเดลที่ยังไม่ผ่าน Calibration: เหย้าชนะ '
                          + f'{probabilities[0]:.2f}% · เสมอ {probabilities[1]:.2f}% · เยือนชนะ {probabilities[2]:.2f}%</p>')
        if row.get('freshness_status') != 'recent_observed_snapshot':
            output.append('<p class="dc-analysis-note">ผลเดิม ณ Snapshot — ราคาเก่าหรือเวลาไม่ยืนยัน ไม่ใช่คำแนะนำเดิมพันใหม่</p>')
        output.append('<details class="dc-analysis-reasons"><summary>เหตุผลและข้อจำกัดของผลเดิม</summary><p>'
                      + _escaped(row.get('analysis_reason')) + '</p><p>' + _escaped(a.get('status'))
                      + '</p><p>' + _escaped(b.get('status')) + '</p><p>' + _escaped(row.get('model_status'))
                      + '</p><p>รับราคา ' + _escaped(row.get('received_at')) + ' · เจ้ามืออัปเดต '
                      + _escaped(row.get('source_updated_at')) + '</p></details></section>')
    output.append('</details>')
    return ''.join(output)
