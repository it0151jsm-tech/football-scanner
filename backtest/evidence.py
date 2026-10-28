"""Strict point-in-time archive validation; never opens a bill DB or the network."""
from collections import Counter
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
import re
from urllib.parse import urlsplit, parse_qsl

import pandas as pd

from history_data import TEAM_ALIAS_RECORDS, _team_identity

from .protocol import MAJOR_LEAGUES, utc

HASH = re.compile(r'^[0-9a-f]{64}$')
SENSITIVE = re.compile(r'api.?key|authorization|password|access.?token|secret', re.I)


def _credentials(value):
    if isinstance(value, dict):
        return any(SENSITIVE.search(str(key)) or _credentials(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_credentials(item) for item in value)
    if isinstance(value, str) and value.startswith(('https://', 'http://')):
        try:
            parsed = urlsplit(value)
        except ValueError:
            return True
        return bool(parsed.username or parsed.password or any(SENSITIVE.search(key) for key, _ in parse_qsl(parsed.query)))
    return False


def finite(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def score(value):
    return type(value) is int and value >= 0


def quarter_point(value):
    return finite(value) and finite(value*4) and abs(value*4-round(value*4)) <= 1e-9


def season_valid(label, date):
    if not isinstance(label, str) or not re.fullmatch(r'\d{4}', label):
        return False
    start = 2000+int(label[:2])
    return int(label[2:]) == (int(label[:2])+1) % 100 and (
        pd.Timestamp(start, 7, 1).date() <= date < pd.Timestamp(start+1, 7, 1).date())


def _identity(name):
    """Reviewed aliases only; never fuzzy-match or remove Unicode letters."""
    identity = _team_identity(name)
    for record in TEAM_ALIAS_RECORDS:
        if identity in {_team_identity(label) for label in record['aliases']}:
            return 'reviewed:'+record['id']
    return 'literal:'+identity


class Archive:
    """Normalized observations plus byte-checked evidence from an explicit export.

    Hashes prove internal binding, not authenticity of historical clocks. Source
    review and owner approval are still required before a performance claim.
    """
    def __init__(self, document, *, allow_test_fixtures=False):
        if not isinstance(document, dict) or document.get('schema_version') != 1:
            raise ValueError('invalid_archive_schema')
        if _credentials(document):
            raise ValueError('credential_fields_or_urls_present_export_must_be_redacted')
        self.document = document
        self.mode = document.get('data_mode')
        self.allowed = self.mode == 'observed' or (self.mode == 'synthetic_test' and allow_test_fixtures)
        self.catalog = document.get('source_catalog', {})
        self.payloads = document.get('evidence_payloads', {})
        if not isinstance(self.catalog, dict) or not isinstance(self.payloads, dict):
            raise ValueError('invalid_evidence_catalog')
        self.excluded = Counter()
        self.history_exclusions = Counter()
        for key in ('events', 'decisions', 'score_history', 'xg_history', 'quotes', 'results'):
            values = document.get(key, [])
            if not isinstance(values, list) or any(not isinstance(row, dict) for row in values):
                raise ValueError('invalid_archive_collection_'+key)
            setattr(self, key, values)

    def proof(self, row):
        if not self.allowed:
            return 'research_or_unverified_availability'
        if row.get('data_mode', self.mode) != self.mode:
            return 'mixed_or_mislabeled_data_mode'
        source_id = row.get('source')
        source = self.catalog.get(source_id) if isinstance(source_id, str) else None
        if not isinstance(source, dict) or source.get('rights_status') != 'approved' or not source.get('reviewed_by'):
            return 'source_rights_not_reviewed'
        if not source.get('rights_evidence_url') or utc(source.get('reviewed_at')) is None:
            return 'source_rights_evidence_missing'
        digest = row.get('source_payload_sha256')
        payload = self.payloads.get(digest) if isinstance(digest, str) else None
        if not isinstance(digest, str) or not HASH.fullmatch(digest) or not isinstance(payload, dict):
            return 'source_payload_missing'
        body = payload.get('body')
        if not isinstance(body, str) or hashlib.sha256(body.encode()).hexdigest() != digest:
            return 'source_payload_checksum_mismatch'
        try:
            observation = json.loads(body)
        except (ValueError, TypeError):
            return 'source_payload_invalid_json'
        expected = {key: value for key, value in row.items() if key != 'source_payload_sha256'}
        if observation != expected or payload.get('source') != row.get('source') or payload.get('received_at') != row.get('received_at'):
            return 'source_observation_not_bound_to_evidence'
        if utc(row.get('received_at')) is None:
            return 'source_receipt_unverified'
        return None

    def event_reason(self, event, decision_at):
        error = self.proof(event)
        if error:
            return error
        if event.get('period') != 'FT90':
            return 'unsupported_or_unverified_period'
        kickoff, decision, receipt = utc(event.get('kickoff_utc')), utc(decision_at), utc(event.get('received_at'))
        if kickoff is None or decision is None or not decision < kickoff or receipt > decision:
            return 'event_not_known_before_decision_or_kickoff'
        if event.get('league') not in MAJOR_LEAGUES or not season_valid(event.get('season'), kickoff.date()):
            return 'event_league_or_season_unverified'
        if not isinstance(event.get('event_id'), str) or not event['event_id'].strip() or any(not isinstance(event.get(key), str) or not event[key].strip() for key in ('home_team','away_team')) or _identity(event['home_team']) == _identity(event['away_team']):
            return 'event_identity_invalid'
        return None

    def quote_reason(self, quote, event, asof, *, closing=False):
        error = self.proof(quote)
        if error:
            return error
        if quote.get('period') != 'FT90' or event.get('period') != 'FT90':
            return 'unsupported_or_unverified_period'
        if any(quote.get(key) != event.get(key) for key in ('event_id','league','season','home_team','away_team')) or utc(quote.get('kickoff_utc')) != utc(event.get('kickoff_utc')):
            return 'quote_fixture_identity_mismatch'
        receipt, updated, captured, cutoff = [utc(value) for value in (quote.get('received_at'), quote.get('source_updated_at'), quote.get('captured_at'), asof)]
        kickoff = utc(event['kickoff_utc'])
        if any(value is None for value in (receipt, updated, captured, cutoff)) or not updated <= receipt <= captured <= cutoff or captured >= kickoff:
            return 'quote_clocks_unverified_or_after_cutoff'
        if not closing and max((cutoff-receipt).total_seconds(), (cutoff-updated).total_seconds()) > 900:
            return 'stale_decision_price'
        if quote.get('market') not in ('Asian Handicap', 'Over/Under') or not quarter_point(quote.get('point')) or (quote.get('market') == 'Over/Under' and quote['point'] < 0) or not finite(quote.get('odds')) or quote['odds'] <= 1:
            return 'invalid_market_contract'
        sides = ('home','away') if quote['market'] == 'Asian Handicap' else ('over','under')
        if quote.get('side') not in sides or any(not isinstance(quote.get(key), str) or not quote[key].strip() for key in ('quote_id','bookmaker','bookmaker_key')):
            return 'quote_identity_unverified'
        return None

    def history_frame(self, rows, event, asof, *, xg=False):
        decision, kickoff = utc(asof), utc(event['kickoff_utc'])
        if decision is None or kickoff is None:
            self.history_exclusions['history_cutoff_unverified'] += len(rows)
            return pd.DataFrame()
        good = []
        for row in rows:
            reason = self.proof(row)
            if reason is None and row.get('period') != 'FT90':
                reason = 'unsupported_or_unverified_period'
            receipt = utc(row.get('received_at'))
            if reason is None and (receipt > decision or row.get('league') != event['league']):
                reason = 'history_late_receipt_or_wrong_league'
            if reason is None and row.get('match_id') == event['event_id']:
                reason = 'target_result_is_not_a_feature'
            when = utc(row.get('match_time_utc'))
            calendar = None
            if reason is None:
                if when is not None:
                    upper, calendar = when, when.date()
                    if receipt < when:
                        reason = 'history_received_before_match'
                elif row.get('match_time_utc') is not None:
                    # An invalid explicit timestamp must not become date-only data.
                    reason = 'history_time_unverified'
                else:
                    try:
                        raw_date = row.get('source_date')
                        if not isinstance(raw_date, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', raw_date):
                            raise ValueError()
                        calendar = date.fromisoformat(raw_date)
                        # A conservative eligibility bound, never an invented kickoff.
                        upper = datetime.combine(calendar+timedelta(days=2), datetime.min.time(), timezone.utc)
                    except (ValueError, TypeError, OverflowError):
                        reason = 'history_time_unverified'
                if reason is None and not upper < min(decision, kickoff):
                    reason = 'history_after_decision_or_unknown_calendar_bound'
            if reason is None and (row.get('status') != 'FT' or not score(row.get('home_goals')) or not score(row.get('away_goals')) or not season_valid(row.get('season'), calendar)):
                reason = 'invalid_history_result_or_season'
            if reason is None and (not isinstance(row.get('match_id'), str) or not row['match_id'].strip() or any(not isinstance(row.get(key), str) or not row[key].strip() for key in ('home_team','away_team')) or _identity(row['home_team']) == _identity(row['away_team'])):
                reason = 'invalid_history_identity'
            if reason is None and xg and any(not finite(row.get(key)) or row[key] < 0 for key in ('xg_home','xg_away')):
                reason = 'real_xg_missing_or_invalid'
            if reason:
                self.history_exclusions[reason] += 1
                continue
            good.append({'Date_dt': pd.Timestamp(calendar), 'HomeTeam': row['home_team'], 'AwayTeam': row['away_team'],
                'FTHG': row['home_goals'], 'FTAG': row['away_goals'], '_history_season': row['season'],
                'xG_home': row.get('xg_home') if xg else None, 'xG_away': row.get('xg_away') if xg else None,
                '_match_id': row['match_id'], '_home_identity': _identity(row['home_team']),
                '_away_identity': _identity(row['away_team'])})
        frame = pd.DataFrame(good)
        if frame.empty:
            return frame
        keys = ['Date_dt','_home_identity','_away_identity']
        values = ['FTHG','FTAG']+(['xG_home','xG_away'] if xg else [])
        conflict = frame.groupby(keys)[values].transform('nunique').gt(1).any(axis=1)
        conflict |= frame.groupby('_match_id')[keys+values].transform('nunique').gt(1).any(axis=1)
        self.history_exclusions['conflicting_history_rows'] += int(conflict.sum())
        unique = frame[~conflict].drop_duplicates(keys)
        self.history_exclusions['duplicate_history_rows'] += len(frame[~conflict])-len(unique)
        return unique.sort_values('Date_dt').drop(columns=['_match_id','_home_identity','_away_identity']).reset_index(drop=True)

    def result_for(self, event, asof=None):
        """Resolve final outcomes available by the report clock, never future rows.

        Future corrections are invisible to an earlier report. Conflicting rows
        already received by that report clock stop settlement for manual review.
        """
        cutoff = utc(asof) if asof is not None else datetime.now(timezone.utc)
        if cutoff is None:
            return None, 'result_report_clock_unverified'
        candidates = []
        for row in self.results:
            if row.get('event_id') != event['event_id']:
                continue
            receipt = utc(row.get('received_at'))
            if receipt is not None and receipt > cutoff:
                continue
            if row.get('period') != 'FT90' or event.get('period') != 'FT90':
                return None, 'unsupported_or_unverified_period'
            if self.proof(row) or any(row.get(key) != event.get(key) for key in ('league','season','home_team','away_team')) or utc(row.get('kickoff_utc')) != utc(event['kickoff_utc']):
                return None, 'result_identity_or_evidence_unverified'
            if row.get('status') != 'FT' or not score(row.get('home_goals')) or not score(row.get('away_goals')) or receipt <= utc(event['kickoff_utc']):
                return None, 'result_not_final_or_clock_invalid'
            candidates.append(row)
        if not candidates:
            return None, 'result_not_available'
        if len({(row['home_goals'],row['away_goals']) for row in candidates}) != 1:
            return None, 'ambiguous_conflicting_result'
        return min(candidates, key=lambda row: utc(row['received_at'])), None
