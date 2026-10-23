"""Canonical screenshot/manual statistics; never infer missing values or probabilities."""
import math
import re

# Broad safety limits reject extraction errors, not establish model eligibility.
# All count metrics remain counts; only xG metrics may be fractional expectations.
_LIMITS = {
    'xG': (100, False), 'xGA': (100, False), 'xGOT': (100, False),
    'shots': (200, True), 'shots_on_target': (200, True),
    'shots_off_target': (200, True), 'blocked_shots': (200, True),
    'shots_inside_box': (200, True), 'shots_outside_box': (200, True),
    'big_chances': (100, True), 'big_chances_missed': (100, True),
    'possession': (100, False), 'corners': (100, True), 'fouls': (200, True),
    'offsides': (100, True), 'yellow_cards': (50, True), 'red_cards': (22, True),
    'saves': (200, True), 'passes': (10000, True), 'accurate_passes': (10000, True),
    'pass_accuracy': (100, False), 'tackles': (500, True),
    'interceptions': (500, True), 'clearances': (500, True),
}
_ALIASES = {
    'xG': ('expected goals', 'expected goals (xG)', 'ประตูคาดการณ์'),
    'xGA': ('expected goals against', 'xG against', 'xG conceded'),
    'xGOT': ('expected goals on target', 'expected goals on target (xGOT)'),
    'shots': ('total shots', 'shots total', 'ยิงทั้งหมด'),
    'shots_on_target': ('shots on goal', 'on target', 'ยิงเข้ากรอบ'),
    'shots_off_target': ('shots off goal', 'off target', 'ยิงไม่เข้ากรอบ'),
    'blocked_shots': ('shots blocked',),
    'shots_inside_box': ('shots inside the box',),
    'shots_outside_box': ('shots outside the box',),
    'big_chances': ('big chances created',),
    'big_chances_missed': ('missed big chances',),
    'possession': ('ball possession', 'possession percentage', 'ครองบอล'),
    'corners': ('corner kicks', 'เตะมุม'), 'fouls': ('fouls committed', 'ฟาวล์'),
    'offsides': ('offside', 'ล้ำหน้า'), 'yellow_cards': ('yellow cards', 'ใบเหลือง'),
    'red_cards': ('red cards', 'ใบแดง'), 'saves': ('goalkeeper saves', 'keeper saves'),
    'passes': ('total passes',), 'accurate_passes': ('successful passes',),
    'pass_accuracy': ('pass accuracy percentage', 'passes accuracy'),
    'tackles': ('total tackles',), 'interceptions': (), 'clearances': (),
}


def _label(value):
    return re.sub(r'[\s_\-().%]+', '', value.casefold())


_NAMES = {_label(label): name for name, aliases in _ALIASES.items()
          for label in (name, *aliases)}


def canonical_name(name):
    if not isinstance(name, str) or not name.strip() or len(name) > 120:
        raise ValueError('Invalid metric name')
    result = _NAMES.get(_label(name.strip()))
    if result is None:
        raise ValueError('Unsupported metric name')
    return result


def _numeric(value, metric):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError('Statistics must be numeric or null')
    try:
        maximum, integer = _LIMITS[metric]
        if not math.isfinite(value) or not 0 <= value <= maximum:
            raise ValueError('Statistic outside supported range')
        if integer and value != int(value):
            raise ValueError('Count statistics must be integers')
    except OverflowError:
        raise ValueError('Statistic outside supported range') from None
    return int(value) if integer else float(value)


def normalize_stats(raw, *, conflict='raise'):
    """Return canonical values and warnings; optionally quarantine contradictions.

    Quarantine means null, never an average or an arbitrary winning source. This
    mode is for unverified image drafts only. Explicit saves use validate_stats.
    """
    if conflict not in ('raise', 'quarantine') or not isinstance(raw, dict) or len(raw) > 50:
        raise ValueError('Invalid statistics object')
    result, warnings, poisoned = {}, [], set()

    def contradict(fields, reason):
        if conflict == 'raise':
            raise ValueError(reason)
        for metric, side in fields:
            result[metric][side] = None
            poisoned.add((metric, side))
        warnings.append(reason + '; value quarantined for manual review')

    for label, sides in raw.items():
        name = canonical_name(label)
        if not isinstance(sides, dict) or set(sides) - {'home', 'away'}:
            raise ValueError('Each metric requires home/away numeric values or null')
        checked = {side: _numeric(sides.get(side), name) for side in ('home', 'away')}
        if name not in result:
            result[name] = checked
            continue
        for side in ('home', 'away'):
            existing, incoming = result[name][side], checked[side]
            if (name, side) in poisoned:
                continue
            if existing is not None and incoming is not None and existing != incoming:
                contradict([(name, side)], 'Conflicting source values for ' + name + '.' + side)
            elif existing is None:
                result[name][side] = incoming

    def present(name, side):
        return result.get(name, {}).get(side)

    for side in ('home', 'away'):
        for child, total in (('shots_on_target', 'shots'), ('shots_off_target', 'shots'),
                             ('blocked_shots', 'shots'), ('shots_inside_box', 'shots'),
                             ('shots_outside_box', 'shots'), ('accurate_passes', 'passes'),
                             ('big_chances_missed', 'big_chances')):
            value, maximum = present(child, side), present(total, side)
            if value is not None and maximum is not None and value > maximum:
                contradict([(child, side)], 'Inconsistent statistics: ' + child + '.' + side + ' exceeds ' + total)
    possession = [present('possession', side) for side in ('home', 'away')]
    if all(value is not None for value in possession) and abs(sum(possession) - 100) > 1:
        contradict([('possession', 'home'), ('possession', 'away')],
                   'Inconsistent possession total; expected 100 percent within rounding tolerance of 1 point')
    for side, other in (('home', 'away'), ('away', 'home')):
        xga, opponent_xg = present('xGA', side), present('xG', other)
        if xga is not None and opponent_xg is not None and not math.isclose(xga, opponent_xg, abs_tol=1e-6, rel_tol=0):
            contradict([('xGA', side), ('xG', other)], 'Conflicting xGA and opponent xG source values')
    return result, warnings


def validate_stats(raw):
    """Strict canonical validation for a separately user-confirmed save."""
    return normalize_stats(raw)[0]


def missing_fields(stats, required=('xG', 'shots', 'shots_on_target')):
    checked = validate_stats(stats)
    missing = []
    for metric in required:
        name = canonical_name(metric)
        pair = checked.get(name)
        for side in ('home', 'away'):
            if pair is None or pair[side] is None:
                missing.append(f'{name}.{side}')
    return missing
