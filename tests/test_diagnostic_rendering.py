"""Saved-evidence diagnostics render offline, escaped, inside existing details."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from html import escape
from html.parser import HTMLParser
import json
import os
import unittest
from unittest.mock import patch

from mobile_dashboard import fixture_dashboard_html, fixture_details_html
from ou_market import select_main_line


NOW = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)
STAMP = NOW.isoformat()


def quote(side='over', price=1.9, pick=1, line=3.0, **metadata):
    row = dict(id=pick, event_id='offline-event', market='Over/Under', side=side,
               home_team='Bayern Munich', away_team='Arsenal', match='Bayern Munich vs Arsenal',
               league='UEFA Champions League', kickoff_utc='2026-10-21T19:00:00+00:00',
               kickoff_th='22/10/2026 02:00', selection=side, point=line, odds=price,
               bookmaker='Offline book', bookmaker_key='offline-book', scanned_at=STAMP,
               received_at=STAMP, source_updated_at=STAMP, snapshot_id=100 + pick)
    row.update(metadata)
    return dict(row=row, a={}, b={}, model_valid=False, ev=None, tier=0,
                conclusion='ยังไม่แนะนำ', reason='ข้อมูลโมเดลไม่เพียงพอ')


def form_audit():
    rows = []
    for index, gf in enumerate((10, 7, 4, 3, 2)):
        rows.append(dict(date=f'2026-10-{index + 1:02}', home_team='Bayern Munich',
                         away_team=f'Opponent {index}', opponent=f'Opponent {index}',
                         venue='home', score=f'{gf}-1', gf=gf, ga=1,
                         league='Bundesliga', csv_code='D1', sport_key='soccer_germany_bundesliga',
                         season='2627', source='Football-Data CSV', source_url='https://example.org/2627/D1.csv',
                         source_urls=['https://example.org/2627/D1.csv'], received_at=STAMP,
                         payload_sha256='a' * 64, input_position=index,
                         provenance_status='source_url_recorded', review_flags=[]))
    window = dict(requested=5, count=5, rows=rows, sum_gf=26, sum_ga=5,
                  mean_gf=5.2, mean_ga=1.0, expected_mean_gf=5.2, expected_mean_ga=1.0,
                  expected_count=5, matches_profile=True, warnings=['high_mean_gf_review_only'])
    team = dict(team='Bayern Munich', status='ok', matched_name='Bayern Munich', mapping='exact',
                source_scope='per-team venue; whole-source exclusions separate',
                excluded={'duplicate_rows': 2, 'conflicting_rows': 2, 'invalid_season_rows': 1},
                warnings=['duplicate_rows'], upstream_quality={'duplicate_rows': 4, 'details': [{'reason': 'fixture_duplicate'}]},
                source_validation={'raw_rows': 100, 'valid_rows': 99},
                windows={'5': window, '10': dict(window, requested=10, warnings=['insufficient_sample'])})
    away = deepcopy(team)
    away.update(team='Arsenal', matched_name='Arsenal')
    return dict(scope='domestic_form_evidence_only', status='available', cutoff_date='2026-10-21',
                recorded_at=STAMP,
                review_only=True, review_thresholds={'mean_gf': 5.0, 'single_match_gf': 8},
                home=team, away=away,
                source_reports={'soccer_germany_bundesliga': {'source': {'quality_audit': {'invalid_season_rows': 1}}}})


def fixture(entries=None, audit=None):
    entries = entries if entries is not None else [quote(), quote('under', pick=2)]
    if audit is not None:
        entries[0]['a']['form_audit'] = audit
        entries[0]['row']['analysis_a_json'] = json.dumps(entries[0]['a'])
    main = select_main_line(entries, now=NOW)
    return dict(event_id='offline-event', row=deepcopy(entries[0]['row']),
                markets={'Asian Handicap': None, 'Over/Under': entries[0]},
                alternatives={'Asian Handicap': [], 'Over/Under': entries},
                ou_main=main, ou_direction=None, ou_reason='ยังไม่แนะนำ')


class Elements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class DiagnosticRenderingTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'VALUE_BET_QUOTE_MAX_AGE_SECONDS': '900'})
        self.env.start()
        self.addCleanup(self.env.stop)

    def render(self, value):
        return fixture_details_html(value, 'เกณฑ์เดิม')

    def test_complete_ou_pair_shows_sum_overround_balance_source_times_and_ids(self):
        html = self.render(fixture())
        for value in ['Over 1.90 / Under 1.90', 'S = 1.052632',
                      'Overround (S − 1) = 0.052632', '5.2632%',
                      'Balance = 0.000000', '0.98–1.20: ผ่าน', '≤ 0.08: ผ่าน',
                      'ผ่านเกณฑ์ตลาด: ผ่าน', 'offline-book', STAMP,
                      'เวลารับข้อมูล:', 'เจ้ามืออัปเดต:', 'รหัส Snapshot:', '101', '102']:
            self.assertIn(value, html)
        self.assertIn('ไม่ใช่โอกาสชนะ', html)
        self.assertNotIn('class="vbp-bar"', html)

    def test_four_failed_pairs_keep_each_real_price_and_rejection_reason(self):
        entries = []
        for index, (line, over, under) in enumerate([(2.5, 1.45, 2.80), (2.75, 1.50, 1.50),
                                                    (3.0, 2.50, 2.50), (3.25, 1.20, 4.00)]):
            entries += [quote('over', over, pick=2*index+1, line=line, bookmaker=f'Book {index}'),
                        quote('under', under, pick=2*index+2, line=line, bookmaker=f'Book {index}')]
        value = fixture(entries)
        html = self.render(value)
        self.assertEqual(html.count('data-ou-diagnostic="pair"'), 4)
        self.assertIn('ไม่มีคู่ผ่านเกณฑ์', html)
        self.assertIn('Balance สูงกว่า 0.08', html)
        self.assertIn('อยู่นอกช่วง 0.98–1.20', html)
        self.assertIn('Over 1.45 / Under 2.80', html)
        self.assertNotIn('ผ่านเกณฑ์ตลาด: ผ่าน', html)

    def test_missing_and_invalid_quotes_show_raw_evidence_without_fake_pair_metrics(self):
        value = fixture([quote(), quote('under', price=None, pick=2)])
        html = self.render(value)
        self.assertIn('data-ou-diagnostic="unpaired"', html)
        self.assertIn('data-ou-diagnostic="invalid"', html)
        self.assertIn('missing_compatible_side', html)
        self.assertIn('invalid_quote', html)
        self.assertIn('ยังไม่คำนวณ S / Overround / Balance', html)
        self.assertIn('ฝั่ง over · เส้น 3.00 · ราคา 1.90', html)
        self.assertNotIn('S = 0.000000', html)
        self.assertNotIn('data-ou-diagnostic="pair"', html)

    def test_stale_and_incompatible_snapshot_diagnostics_remain_visible(self):
        old = (NOW - timedelta(minutes=16)).isoformat()
        value = fixture([quote(source_updated_at=old), quote('under', pick=2, source_updated_at=old)])
        html = self.render(value)
        self.assertIn(old, html)
        self.assertIn('หลักฐานความสด: ไม่ผ่าน', html)
        self.assertIn('ราคาเก่า', html)
        value = fixture([quote(), quote('under', pick=2, source_updated_at=old)])
        html = self.render(value)
        self.assertEqual(html.count('data-ou-diagnostic="unpaired"'), 2)
        self.assertIn('Snapshot ตรงกัน', html)

    def test_saved_form_audit_exposes_exact_rows_denominator_and_provenance(self):
        html = self.render(fixture(audit=form_audit()))
        for value in ['5 นัดล่าสุดตามสนาม', '10 นัดล่าสุดตามสนาม',
                      'จำนวน 5 / 5 นัด · ผลรวมยิง 26 / เสีย 5',
                      'ยิงเฉลี่ย 5.200 / เสียเฉลี่ย 1.000', 'ตรงกับโปรไฟล์เดิม: ผ่าน',
                      '2026-10-01', 'Opponent 0', 'สกอร์ 10-1 · GF 10 / GA 1',
                      'Bundesliga', 'D1', '2627', 'Football-Data CSV',
                      'https://example.org/2627/D1.csv', 'SHA-256: ' + 'a' * 64,
                      'เวลารับข้อมูลจริง: ' + STAMP, 'high_mean_gf_review_only',
                      'เวลาบันทึกหลักฐานฟอร์มจริง: ' + STAMP,
                      'duplicate_rows', 'conflicting_rows', 'invalid_season_rows', 'fixture_duplicate']:
            self.assertIn(value, html)
        self.assertEqual(html.count('data-form-audit="saved"'), 1)

    def test_audit_lookup_searches_representative_markets_and_unselected_alternatives(self):
        for location in ('representative', 'market', 'alternative'):
            with self.subTest(location=location):
                value = fixture()
                audit = form_audit()
                if location == 'representative':
                    value['row']['analysis_a_json'] = json.dumps({'form_audit': audit})
                elif location == 'market':
                    value['markets']['Over/Under']['a']['form_audit'] = audit
                else:
                    value['alternatives']['Over/Under'][1]['a']['form_audit'] = audit
                self.assertIn('ยิงเฉลี่ย 5.200', self.render(value))

    def test_missing_or_corrupt_saved_audit_does_not_verify_old_mean(self):
        for raw in (None, '', '{broken', '[]', json.dumps({'home_form': {'last5_gf': 5.2}})):
            value = fixture()
            value['row']['analysis_a_json'] = raw
            value['markets']['Over/Under']['row']['analysis_a_json'] = raw
            html = self.render(value)
            self.assertIn('ยังยืนยันค่าเฉลี่ยเดิมไม่ได้', html)
            self.assertNotIn('data-form-audit="saved"', html)
            self.assertNotIn('ตรงกับโปรไฟล์เดิม: ผ่าน', html)
        self.assertIn('ยังยืนยันเส้นหลักไม่ได้', self.render({}))

    def test_missing_window_or_source_provenance_shows_unknown_not_generated_evidence(self):
        audit = form_audit()
        audit['recorded_at'] = None
        audit['home']['windows']['5']['rows'][0].update(source_url=None, source_urls=[],
                                                       received_at=None, payload_sha256=None)
        audit['away']['windows'] = {}
        html = self.render(fixture(audit=audit))
        self.assertIn('เวลารับข้อมูลจริง: ไม่มีข้อมูล', html)
        self.assertIn('เวลาบันทึกหลักฐานฟอร์มจริง: ไม่มีข้อมูล', html)
        self.assertIn('SHA-256: ไม่มีข้อมูล', html)
        self.assertIn('ยังยืนยันค่าเฉลี่ยไม่ได้', html)
        self.assertIn('จำนวน ไม่มีข้อมูล / ไม่มีข้อมูล', html)

    def test_external_evidence_and_diagnostic_keys_are_html_escaped(self):
        payload = '\"><img src=x onerror="PWNED=1"><script>PWNED=1</script>&\''
        value = fixture(audit=form_audit())
        main = value['ou_main']
        main['metric_definitions'] = {key: payload for key in ('implied_sum', 'overround', 'balance', 'boundary', 'interpretation')}
        main.update(rule=payload, reason=payload, diagnostics={payload: payload})
        for pair in main['pairs']:
            for key in ('bookmaker', 'bookmaker_key', 'event_id', 'scanned_at', 'received_at', 'source_updated_at', 'reason'):
                pair[key] = payload
            pair.update(quote_ids={payload: [payload]}, snapshot_ids={payload: [payload]}, reasons=[payload])
        audit = value['markets']['Over/Under']['a']['form_audit']
        audit['home'].update(team=payload, warnings=[payload], excluded={payload: payload})
        row = audit['home']['windows']['5']['rows'][0]
        for key in ('date', 'home_team', 'away_team', 'opponent', 'venue', 'score', 'league', 'season', 'source', 'source_url', 'received_at', 'payload_sha256'):
            row[key] = payload
        row.update(source_urls=[payload], review_flags=[payload], gf=payload)
        value['row']['analysis_a_json'] = json.dumps({'form_audit': audit})
        html = fixture_details_html(value, payload)
        self.assertIn(escape(payload, quote=True), html)
        self.assertNotIn('<script>', html)
        self.assertNotIn('<img ', html)
        parser = Elements()
        parser.feed(html)
        self.assertFalse(any(key.startswith('on') for _, attrs in parser.tags for key in attrs))
        self.assertFalse(any(tag in ('script', 'iframe', 'img') for tag, _ in parser.tags))

    def test_incomplete_quote_external_fields_are_escaped(self):
        payload = '<script>bad()</script>'
        value = fixture([quote(bookmaker=payload), quote('under', price=None, pick=2, bookmaker=payload)])
        html = self.render(value)
        self.assertIn(escape(payload), html)
        self.assertNotIn(payload, html)

    def test_rendering_does_not_change_saved_evidence_or_main_dashboard(self):
        value = fixture()
        before_dashboard = fixture_dashboard_html(value)
        value['markets']['Over/Under']['a']['form_audit'] = form_audit()
        before = deepcopy(value)
        self.render(value)
        self.assertEqual(value, before)
        self.assertEqual(fixture_dashboard_html(value), before_dashboard)


if __name__ == '__main__':
    unittest.main()
