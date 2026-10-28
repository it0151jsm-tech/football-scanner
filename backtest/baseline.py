"""Offline adapter to the unchanged application baseline.

Only named pure app/history functions are compiled. The app module, Streamlit,
fetch functions and bill database functions are never imported or executed.
The caller supplies source-validated, as-of histories and real quote evidence.
This adapter does not establish availability times or manufacture missing data.
"""
import ast
import builtins
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from types import ModuleType
import unicodedata

import pandas as pd


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ROOT = REPOSITORY_ROOT / 'fixtures/backtest/pr6-baseline'
REFERENCE_LOCK = REPOSITORY_ROOT / 'fixtures/backtest/pr6-source-lock.json'
UTC = timezone.utc
BASELINE_VERSION = 'pr6-frozen-offline-v2'
PROTECTED_FUNCTIONS = frozenset({
    'estimate_goals', 'estimate_form_signal', 'choose_recommendation',
    'poisson_probs', 'score_matrix', 'split_line', 'evaluate_market_poisson',
    'actual_xg_evidence', 'system_a_analysis', 'system_b_analysis', 'settle_pick',
})
APP_FUNCTIONS = PROTECTED_FUNCTIONS | {
    'norm_name', 'map_team', 'evaluate_historical_line', 'team_form_profile',
    'blended_form_rate', 'settle_leg', 'selection_spread', 'opponent_quality',
    'price_evidence', 'cup_model_status',
}
APP_CONSTANTS = {'LEAGUES', 'UNDERSTAT_LEAGUES', 'EUROPEAN_CUPS', 'MAX_XG_AGE_DAYS'}
HISTORY_FUNCTIONS = {'normalize', '_team_identity', 'alias_evidence', 'match_team',
                     'validate_rows', 'team_history'}
HISTORY_CONSTANTS = {'ALIAS_SOURCE', '_ISSUE9_SOURCE', '_UNDERSTAT_AUDIT',
                     'TEAM_ALIAS_RECORDS', 'TEAM_ALIASES', 'MESSAGES'}
POLICY_FILES = ('market_recommendations.py', 'ou_market.py', 'readiness.py', 'mobile_dashboard.py')


class BaselineIntegrityError(ValueError):
    """The checked-out formulas no longer match the protected baseline contract."""


def _instant(value):
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError('An explicit timezone is required')
        return parsed.astimezone(UTC)
    except (ValueError, TypeError, OverflowError):
        raise ValueError('An explicit valid timezone-aware instant is required') from None


def _sha(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _selected_tree(source, functions, constants):
    selected = []
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef) and node.name in functions:
            if node.decorator_list:
                raise BaselineIntegrityError('Pure baseline functions must not have executable decorators')
            selected.append(node)
        elif isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id in constants
                                                 for target in node.targets):
            selected.append(node)
    found = {node.name for node in selected if isinstance(node, ast.FunctionDef)}
    if found != set(functions):
        raise BaselineIntegrityError('Missing required pure baseline functions: '+', '.join(sorted(set(functions)-found)))
    return ast.Module(body=selected, type_ignores=[])


def _source_manifest(root):
    # Operational fixes may change today's scan/database helpers. Historical
    # evaluation remains pinned to all reviewed PR6 source bytes, not whatever
    # production happens to run when a report is regenerated later.
    frozen_reference = root.resolve() == ROOT.resolve()
    if frozen_reference:
        lock = json.loads(REFERENCE_LOCK.read_text(encoding='utf-8'))
        for name, expected_hash in lock['protected_files_sha256'].items():
            try:
                actual_hash = hashlib.sha256((root / name).read_bytes()).hexdigest()
            except OSError:
                raise BaselineIntegrityError('Frozen PR6 source is missing: '+name) from None
            if actual_hash != expected_hash:
                raise BaselineIntegrityError('Frozen PR6 source differs: '+name)
    source = (root / 'app.py').read_text(encoding='utf-8')
    contract = json.loads((root / 'fixtures/readiness/pr2-contract.json').read_text(encoding='utf-8'))
    expected = contract.get('formula_source_sha256', {})
    if set(expected) != PROTECTED_FUNCTIONS:
        raise BaselineIntegrityError('The original eleven protected formula hashes are required')
    nodes = {node.name: node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef)}
    actual = {name: _sha(ast.get_source_segment(source, nodes[name])) for name in PROTECTED_FUNCTIONS if name in nodes}
    if actual != expected:
        changed = sorted(name for name in PROTECTED_FUNCTIONS if actual.get(name) != expected.get(name))
        raise BaselineIntegrityError('Protected formula source differs: '+', '.join(changed))
    pure_app = _selected_tree(source, APP_FUNCTIONS, APP_CONSTANTS)
    history_source = (root / 'history_data.py').read_text(encoding='utf-8')
    pure_history = _selected_tree(history_source, HISTORY_FUNCTIONS, HISTORY_CONSTANTS)
    # Source segments preserve evidence of helpers/aliases as well as formulas.
    sources = {
        'app_pure_sources': _sha('\n'.join(ast.get_source_segment(source, node) for node in pure_app.body)),
        'history_pure_sources': _sha('\n'.join(ast.get_source_segment(history_source, node) for node in pure_history.body)),
        'backtest/baseline.py': _sha(Path(__file__).read_text(encoding='utf-8')),
        **{name: _sha((root / name).read_text(encoding='utf-8')) for name in POLICY_FILES},
    }
    payload = {'version': BASELINE_VERSION, 'formula_sha256': dict(sorted(actual.items())),
               'source_sha256': dict(sorted(sources.items())),
               'reference_scope': 'frozen_pr6' if frozen_reference else 'explicit_custom_reference',
               'reference_commit': 'df4b836b7bfade57f009f1ec115ae2905f76ae7d' if frozen_reference else None}
    payload['baseline_sha256'] = _sha(json.dumps(payload, sort_keys=True, separators=(',', ':')))
    payload['bundle_sha256'] = payload['baseline_sha256']
    return payload


class _Runtime:
    """Private module namespaces with one clock and a read-only quote-age setting.

    No entries are installed in sys.modules, and no original module globals,
    datetime attributes, process environment or function source are patched.
    """
    def __init__(self, root, asof, quote_max_age_seconds):
        self.root, self.asof = root, _instant(asof)
        self.quote_max_age_seconds = quote_max_age_seconds
        self.modules = {}
        frozen = self.asof

        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return frozen.astimezone(tz) if tz is not None else frozen.replace(tzinfo=None)

            @classmethod
            def utcnow(cls):
                return frozen.replace(tzinfo=None)

            @classmethod
            def today(cls):
                return cls.now()

        self.datetime_module = ModuleType('datetime')
        self.datetime_module.datetime = FrozenDateTime
        self.datetime_module.timedelta = timedelta
        self.datetime_module.timezone = timezone
        self.os_module = ModuleType('os')
        self.os_module.getenv = lambda name, default=None: str(quote_max_age_seconds) if name == 'VALUE_BET_QUOTE_MAX_AGE_SECONDS' else default
        self.app = self._namespace('_backtest_original_app')
        source = (root / 'app.py').read_text(encoding='utf-8')
        exec(compile(_selected_tree(source, APP_FUNCTIONS, APP_CONSTANTS), str(root / 'app.py'), 'exec'), self.app)

    def _namespace(self, name):
        safe_builtins = dict(vars(builtins), __import__=self._import)
        return {'__name__': name, '__builtins__': safe_builtins, 'pd': pd, 'math': math,
                're': re, 'unicodedata': unicodedata, 'json': json, 'os': self.os_module,
                'datetime': self.datetime_module.datetime, 'timedelta': timedelta,
                'timezone': timezone, 'UTC': UTC, 'TH_TZ': timezone(timedelta(hours=7))}

    def _import(self, name, globals=None, locals=None, fromlist=(), level=0):
        if level:
            raise ImportError('Relative imports are unavailable in the offline baseline')
        if name == 'datetime':
            return self.datetime_module
        if name == 'os':
            return self.os_module
        if name in ('market_recommendations', 'ou_market', 'readiness', 'mobile_dashboard', 'history_data'):
            return self.module(name)
        if name in ('json', 'math', 're', 'unicodedata', 'html'):
            return builtins.__import__(name, globals, locals, fromlist, 0)
        if name == 'pandas':
            return pd
        raise ImportError('Dependency is not allowed in the offline baseline: '+name)

    def module(self, name):
        if name in self.modules:
            return self.modules[name]
        module = ModuleType(name)
        module.__dict__.update(self._namespace(name))
        self.modules[name] = module
        source = (self.root / (name+'.py')).read_text(encoding='utf-8')
        if name == 'history_data':
            tree = _selected_tree(source, HISTORY_FUNCTIONS, HISTORY_CONSTANTS)
        elif name == 'mobile_dashboard':
            tree = _selected_tree(source, {'mapping', 'model_ready'}, set())
        elif name == 'readiness':
            tree = _selected_tree(source, {'instant', 'quote_freshness'}, {'UTC'})
        else:
            tree = ast.parse(source)
        exec(compile(tree, str(self.root / (name+'.py')), 'exec'), module.__dict__)
        return module


def existing_functions(repository_root=None):
    """Return original whitelisted callables after checking all eleven hashes."""
    root = Path(repository_root) if repository_root is not None else ROOT
    _source_manifest(root)
    runtime = _Runtime(root, datetime(2000, 1, 1, tzinfo=UTC), 900)
    return {name: runtime.app[name] for name in APP_FUNCTIONS}


def current_source_hashes(repository_root=None):
    """Independently recheck formulas and return the canonical source manifest."""
    return _source_manifest(Path(repository_root) if repository_root is not None else ROOT)


class Baseline:
    def __init__(self, repository_root=None, *, quote_max_age_seconds=900):
        if (isinstance(quote_max_age_seconds, bool) or not isinstance(quote_max_age_seconds, int)
                or not 1 <= quote_max_age_seconds <= 900):
            raise ValueError('Quote age must preserve the original 1–900 second safety limit')
        self.root = Path(repository_root) if repository_root is not None else ROOT
        self.quote_max_age_seconds = quote_max_age_seconds
        self.version = _source_manifest(self.root)
        self.baseline_hash = self.version['baseline_sha256']
        self.functions = existing_functions(self.root)

    def _ensure_version(self):
        if current_source_hashes(self.root)['bundle_sha256'] != self.version['bundle_sha256']:
            raise BaselineIntegrityError('Source bundle changed after baseline initialization')

    def evaluate(self, score_history, xg_history, event, quotes, decision_at):
        """Run the original formulas on caller-gated, supplied source evidence.

        Event keys: event_id/id, kickoff_utc/commence_time, home_team, away_team,
        league and optional sport_key. Quotes retain actual receipt/update/scan
        timestamps. Missing provider timestamps remain missing and fail C safely.
        """
        self._ensure_version()
        decision = _instant(decision_at)
        kickoff = _instant(event.get('kickoff_utc') or event.get('commence_time'))
        if kickoff <= decision:
            raise ValueError('A decision must precede the actual fixture kickoff')
        event_id = event.get('event_id') or event.get('id')
        home, away = event.get('home_team'), event.get('away_team')
        if not all(isinstance(value, str) and value.strip() for value in (event_id, home, away)) or home == away:
            raise ValueError('Verified event and distinct team identities are required')
        runtime = _Runtime(self.root, decision, self.quote_max_age_seconds)
        f = runtime.app
        scores = score_history.copy(deep=True) if isinstance(score_history, pd.DataFrame) else pd.DataFrame()
        xg = xg_history.copy(deep=True) if isinstance(xg_history, pd.DataFrame) else pd.DataFrame()
        home_profile = f['team_form_profile'](scores, home, kickoff, 'home')
        away_profile = f['team_form_profile'](scores, away, kickoff, 'away')
        opponents = {side: f['opponent_quality'](scores, team, kickoff, side)
                     for side, team in (('home', home), ('away', away))}
        xg_evidence = f['actual_xg_evidence'](xg, home, away, kickoff)
        is_cup = event.get('sport_key') in f['EUROPEAN_CUPS']
        metrics = None if is_cup else f['estimate_goals'](xg, home, away, kickoff)
        matrix = f['score_matrix'](metrics['home_lambda'], metrics['away_lambda']) if metrics is not None else None
        output = []
        for index, original in enumerate(quotes, 1):
            quote = deepcopy(original)
            if quote.get('event_id', event_id) != event_id:
                raise ValueError('A quote cannot be evaluated against a different event')
            market = {'spreads': 'Asian Handicap', 'totals': 'Over/Under'}.get(quote.get('market'), quote.get('market'))
            side, point, odds = quote.get('side'), quote.get('point'), quote.get('odds')
            if market not in ('Asian Handicap', 'Over/Under') or side not in (('home', 'away') if market == 'Asian Handicap' else ('over', 'under')):
                raise ValueError('A genuine AH/OU market and side are required')
            if (any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in (point, odds))
                    or odds <= 1 or abs(point*4-round(point*4)) > 1e-9 or (market == 'Over/Under' and point < 0)):
                raise ValueError('A real finite decimal quote at its own quarter line is required')
            key = 'spreads' if market == 'Asian Handicap' else 'totals'
            forms = [f['estimate_form_signal'](scores, home, away, kickoff, key, side, point, window=size)
                     for size in (5, 10, None)]
            observed_at = quote.get('received_at') or quote.get('scanned_at')
            price = f['price_evidence'](quote.get('first_observed_quote'), point, odds, observed_at)
            a = f['system_a_analysis'](*forms, home_profile, away_profile, opponents, xg_evidence, price)
            evaluation = f['evaluate_market_poisson'](matrix, key, side, point, odds) if matrix is not None else None
            ou_line = point if market == 'Over/Under' else 2.5
            b = f['system_b_analysis'](metrics, matrix, evaluation, f['split_line'](ou_line), ou_line)
            if is_cup:
                b.update(status=f['cup_model_status'](xg_evidence), scope='domestic_context_only')
            selection = f['selection_spread'](home if side == 'home' else away, side, point) if market == 'Asian Handicap' else ('สูง' if side == 'over' else 'ต่ำ')+f' ({point:g})'
            ev = evaluation['ev_pct'] if evaluation is not None else None
            score = a['score']
            row = dict(quote, id=quote.get('id', quote.get('quote_id', index)), event_id=event_id,
                kickoff_utc=kickoff.isoformat(), home_team=home, away_team=away,
                league=event.get('league') or event.get('sport_key'), match=f'{home} vs {away}',
                market=market, side=side, point=point, odds=odds, selection=selection,
                lambda_home=metrics['home_lambda'] if metrics else None,
                lambda_away=metrics['away_lambda'] if metrics else None,
                history_matches=metrics['history_matches'] if metrics else 0,
                ev_pct=ev, p_win=evaluation['p_win'] if evaluation else None,
                p_partial=evaluation['p_partial'] if evaluation else None,
                p_loss=evaluation['p_loss'] if evaluation else None,
                confidence_score=score, score=score, confidence_label=a['result'],
                form_rate=forms[0]['form_rate'], form_rate_10=forms[1]['form_rate'],
                form_rate_season=forms[2]['form_rate'], form_sample=forms[0]['form_sample'],
                odds_in_range=int(bool(quote.get('odds_in_range', True))),
                recommendation=f['choose_recommendation'](score, ev, bool(quote.get('odds_in_range', True)), score is not None),
                analysis_a_json=json.dumps(a, ensure_ascii=False, allow_nan=False),
                analysis_b_json=json.dumps(b, ensure_ascii=False, allow_nan=False),
                model_status=b['status'], baseline_sha256=self.baseline_hash)
            output.append(row)
        return output

    evaluated_rows = evaluate

    def c_pick_ids(self, rows, asof):
        self._ensure_version()
        runtime = _Runtime(self.root, asof, self.quote_max_age_seconds)
        policy = runtime.module('market_recommendations')
        return policy.recommended_pick_ids(deepcopy(list(rows)), runtime.app['choose_recommendation'])

    def settle_pick(self, row, home_goals, away_goals):
        self._ensure_version()
        return self.functions['settle_pick'](row, home_goals, away_goals)


def evaluated_rows(score_history, xg_history, event, quotes, decision_at):
    return Baseline().evaluate(score_history, xg_history, event, quotes, decision_at)


def c_pick_ids(rows, asof):
    return Baseline().c_pick_ids(rows, asof)


def settle_pick(row, home_goals, away_goals):
    return Baseline().settle_pick(row, home_goals, away_goals)
