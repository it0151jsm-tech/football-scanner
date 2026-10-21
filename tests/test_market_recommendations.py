import json
from datetime import datetime, timezone
import tempfile
import unittest
from pathlib import Path

import test_regressions as regression
from test_dashboard import complete_row, Elements
from market_recommendations import group_fixture_recommendations, TREND
from mobile_dashboard import fixture_dashboard_html, fixture_details_html


def quote(market='Over/Under', point=2.5, side='over', book='OU Book', event='event', pick=1, odds=2.1):
    f = regression.load_functions(Path(__file__).resolve().parents[1] / 'app.py')
    row = complete_row()
    row.update(event_id=event, id=pick, market=market, point=point, side=side, bookmaker=book, odds=odds,
               scanned_at='2026-10-20T12:00:00+07:00', kickoff_utc='2026-10-21T12:00:00+00:00')
    row.update(bookmaker_key="fixture-book", received_at=datetime.now(timezone.utc).replace(second=0,microsecond=0).isoformat(), source_updated_at=datetime.now(timezone.utc).replace(second=0,microsecond=0).isoformat())
    a = json.loads(row['analysis_a_json'])
    a['price'] = f['price_evidence'](None, point, odds, row['scanned_at'])
    matrix = f['score_matrix'](1.7, 1.1)
    evaluation = f['evaluate_market_poisson'](matrix, 'totals' if market == 'Over/Under' else 'spreads', side, point, odds)
    ou_line = point if market == 'Over/Under' else 2.5
    b = f['system_b_analysis'](dict(home_lambda=1.7, away_lambda=1.1), matrix, evaluation, f['split_line'](ou_line), ou_line)
    row['analysis_a_json'], row['analysis_b_json'], row['ev_pct'] = json.dumps(a), json.dumps(b), evaluation['ev_pct']
    row['selection'] = f'{market} {side} {point}'
    return row


class MarketRecommendationsTests(unittest.TestCase):
    def setUp(self):
        self.f = regression.load_functions(Path(__file__).resolve().parents[1] / 'app.py')

    def groups(self, rows):
        return group_fixture_recommendations(rows, self.f['choose_recommendation'])

    def test_one_fixture_combines_ah_and_ou_without_crossing_books(self):
        ah, ou = quote('Asian Handicap', -.25, 'home', 'AH Book', pick=1), quote(pick=2)
        fixtures = self.groups([ah, ou, quote(side="under", odds=1.85, pick=3)])
        self.assertEqual(len(fixtures), 1)
        markets = fixtures[0]['markets']
        self.assertEqual(markets['Asian Handicap']['row']['bookmaker'], 'AH Book')
        self.assertEqual(markets['Over/Under']['row']['bookmaker'], 'OU Book')
        html = fixture_dashboard_html(fixtures[0])
        self.assertEqual(html.count('<article class="vbp-dashboard"'), 1)
        self.assertIn('AH Book', html)
        self.assertIn('OU Book', html)
        self.assertEqual(html.count('class="vbp-tile vbp-ou"'), 2)

    def test_same_match_name_with_different_event_ids_never_merges(self):
        self.assertEqual(len(self.groups([quote(event='a'), quote(event='b', pick=2)])), 2)
        a, b = quote(), quote(pick=2)
        a['event_id'] = b['event_id'] = None
        self.assertEqual(len(self.groups([a, b])), 2)

    def test_bookmakers_remain_independent_but_latest_line_per_book_wins(self):
        old = quote(pick=1, point=2.5)
        newer = quote(pick=2, point=2.75)
        newer['scanned_at'] = '2026-10-20T13:00:00+07:00'
        other = quote(pick=3, book='Other Book', point=3)
        fixture = self.groups([old, newer, other])[0]
        choices = fixture['alternatives']['Over/Under']
        self.assertEqual({e['row']['id'] for e in choices}, {2, 3})
        for entry in choices:
            self.assertEqual(entry['b']['ou_line'], entry['row']['point'])

    def test_high_odds_or_a_alone_does_not_outrank_supported_positive_ev(self):
        good = quote(pick=1)
        bad = quote(pick=2, book='High price', odds=9)
        a = json.loads(bad['analysis_a_json'])
        a['score'] = 99
        bad.update(analysis_a_json=json.dumps(a), analysis_b_json=json.dumps({'available': False}), ev_pct=None)
        fixture = self.groups([bad, good])[0]
        self.assertEqual(fixture['markets']['Over/Under']['row']['id'], 1)
        self.assertEqual(fixture['alternatives']['Over/Under'][1]['conclusion'], TREND)

    def test_missing_ev_with_form_uses_exact_trend_label_without_value_claim(self):
        row = quote()
        row.update(ev_pct=None, analysis_b_json=json.dumps({'available': False}))
        entry = self.groups([row])[0]['markets']['Over/Under']
        self.assertEqual(entry['conclusion'], TREND)
        self.assertIsNone(entry['ev'])
        self.assertNotIn('ตัวเลือกเด่น', fixture_dashboard_html(self.groups([row])[0]))

    def test_no_evidence_and_negative_ev_are_not_recommendations(self):
        empty = quote()
        empty.update(analysis_a_json=None, analysis_b_json=None, ev_pct=None)
        self.assertEqual(self.groups([empty])[0]['markets']['Over/Under']['conclusion'], 'ยังไม่แนะนำ')
        negative = quote(point=3, odds=1.05)
        entry = self.groups([negative])[0]['markets']['Over/Under']
        self.assertLess(entry['ev'], 0)
        self.assertEqual(entry['conclusion'], 'ยังไม่แนะนำ')

    def test_ou_quarter_and_integer_lines_use_own_saved_probabilities(self):
        probabilities = []
        for line in [2.5, 2.75, 3.0]:
            row = quote(point=line)
            fixture = self.groups([row,quote(point=line,side="under",odds=1.85,pick=2)])[0]
            entry = fixture['markets']['Over/Under']
            self.assertTrue(entry['model_valid'])
            self.assertEqual(entry['b']['ou_line'], line)
            probabilities.append(entry['b']['ou']['over_full'])
            html = fixture_dashboard_html(fixture)
            self.assertIn(f"{entry['b']['ou']['over_full']:.2f}%", html)
            self.assertIn('เส้นตลาดจริง', html)
        self.assertNotEqual(probabilities[0], probabilities[2])

    def test_ah_model_reference_or_wrong_ou_line_is_never_substituted(self):
        row = quote(point=2.75)
        wrong = json.loads(quote(point=2.5)['analysis_b_json'])
        row['analysis_b_json'] = json.dumps(wrong)
        fixture = self.groups([row])[0]
        entry = fixture['markets']['Over/Under']
        self.assertFalse(entry['model_valid'])
        self.assertIsNone(entry['ev'])
        html = fixture_dashboard_html(fixture)
        self.assertIn('ไม่ตรงเส้นตลาด', fixture_details_html(fixture, 'เกณฑ์'))
        self.assertNotIn('class="vbp-tile vbp-ou"', html)
        only_ah = fixture_dashboard_html(self.groups([quote('Asian Handicap', -.25, 'home')])[0])
        self.assertIn('ไม่มีราคาตลาด', only_ah)
        self.assertNotIn('class="vbp-tile vbp-ou"', only_ah)

    def test_missing_price_is_never_synthesized(self):
        row = quote()
        row['odds'] = None
        fixture = self.groups([row])[0]
        self.assertIsNone(fixture['markets']['Over/Under'])
        self.assertEqual(fixture_dashboard_html(fixture).count('ไม่มีราคาตลาด'), 2)

    def test_inconsistent_ev_and_price_evidence_are_not_trusted(self):
        row = quote()
        row['ev_pct'] = 999
        self.assertIsNone(self.groups([row])[0]['markets']['Over/Under']['ev'])
        row = quote()
        row['point'] = 3
        self.assertFalse(self.groups([row])[0]['markets']['Over/Under']['model_valid'])

    def test_snapshot_lookup_never_uses_other_book_market_line_or_later_scan(self):
        with tempfile.TemporaryDirectory() as folder:
            self.f['DB_FILE'] = Path(folder) / 'test.db'
            self.f['init_db']()
            row = quote()
            row.pop("received_at", None)
            row.pop("source_updated_at", None)
            with self.f['db']() as con:
                for market, book, point, at in [('Over/Under', 'OU Book', 2.5, '2026-10-20T11:00:00+07:00'),
                                               ('Asian Handicap', 'OU Book', 2.5, '2026-10-20T11:30:00+07:00'),
                                               ('Over/Under', 'Other', 2.5, '2026-10-20T11:30:00+07:00'),
                                               ('Over/Under', 'OU Book', 2.75, '2026-10-20T11:30:00+07:00'),
                                               ('Over/Under', 'OU Book', 2.5, '2026-10-20T13:00:00+07:00')]:
                    con.execute('INSERT INTO odds_snapshots(event_id,market,side,bookmaker,point,odds,captured_at) VALUES (?,?,?,?,?,?,?)',
                                ('event', market, 'over', book, point, row['odds'], at))
            self.assertEqual(self.f['lookup_quote_snapshot'](row)['id'], 1)
            row['odds'] = 8
            self.assertIsNone(self.f['lookup_quote_snapshot'](row))

    def test_grouped_html_escapes_bookmaker_and_explains_ranking(self):
        row = quote(book='\"><img src=x onerror=alert(1)>')
        fixture = self.groups([row])[0]
        html = fixture_dashboard_html(fixture) + fixture_details_html(fixture, 'หลักเกณฑ์')
        parser = Elements()
        parser.feed(html)
        self.assertNotIn('<img ', html)
        self.assertIn('&lt;img', html)
        self.assertIn('หลักเกณฑ์', html)

    def test_reused_event_identity_does_not_mix_different_teams(self):
        old, current = quote('Asian Handicap', -.25, 'home'), quote(pick=2)
        current['home_team'] = 'Different Team'
        fixture = self.groups([old, current])[0]
        self.assertTrue(fixture['identity_conflict'])
        self.assertIsNone(fixture['markets']['Asian Handicap'])

    def test_missing_form_sample_cannot_be_promoted_by_a_score_alone(self):
        row = quote()
        a = json.loads(row['analysis_a_json'])
        a['score'] = 99
        a['form5']['form_sample'] = a['form10']['form_sample'] = 0
        row['analysis_a_json'] = json.dumps(a)
        entry = self.groups([row])[0]['markets']['Over/Under']
        self.assertEqual(entry['conclusion'], 'ยังไม่แนะนำ')
