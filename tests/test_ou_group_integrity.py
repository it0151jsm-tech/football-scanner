"""OU contradictions must reach the selector intact, using offline quote fixtures."""
from datetime import timedelta
from pathlib import Path
import unittest

from market_recommendations import group_fixture_recommendations, recommended_ou_ids, ou_table_rows
from test_market_recommendations import FIXTURE_TIME, quote
from test_regressions import load_functions


class OUGroupIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.f = load_functions(Path(__file__).resolve().parents[1] / 'app.py')

    def fixture(self, rows):
        return group_fixture_recommendations(rows, self.f['choose_recommendation'])[0]

    def rows(self):
        return [quote(side='over', odds=1.90, pick=1),
                quote(side='over', odds=1.95, pick=2),
                quote(side='under', odds=1.95, pick=3)]

    def test_same_snapshot_conflicting_over_quotes_are_never_last_row_wins(self):
        rows = self.rows()
        fixture = self.fixture(rows)
        main = fixture['ou_main']
        self.assertEqual(main['status'], 'unconfirmed')
        self.assertEqual(main['reason_code'], 'conflicting_duplicates')
        self.assertIsNone(fixture['ou_direction'])
        self.assertEqual({e['row']['id'] for e in fixture['alternatives']['Over/Under']}, {1, 2, 3})
        self.assertEqual(main['diagnostics']['conflicting_duplicate_groups'], 1)
        self.assertEqual({r['odds'] for r in main['unpaired'][0]['sides']['over']}, {1.90, 1.95})
        self.assertEqual(recommended_ou_ids(rows, self.f['choose_recommendation']), set())
        self.assertFalse(any(row['ou_selected'] for row in ou_table_rows(rows, self.f['choose_recommendation'])))

    def test_conflict_remains_rejected_in_reverse_order(self):
        main = self.fixture(list(reversed(self.rows())))['ou_main']
        self.assertEqual(main['reason_code'], 'conflicting_duplicates')
        self.assertEqual(main['pairs'], [])
        self.assertIsNone(main['pair'])

    def test_conflicting_under_quotes_are_preserved_as_well(self):
        rows = [quote(side='under', odds=1.90, pick=1),
                quote(side='under', odds=1.95, pick=2),
                quote(side='over', odds=1.95, pick=3)]
        main = self.fixture(rows)['ou_main']
        self.assertEqual(main['reason_code'], 'conflicting_duplicates')
        self.assertEqual({r['odds'] for r in main['unpaired'][0]['sides']['under']}, {1.90, 1.95})

    def test_old_scan_conflicts_do_not_disqualify_a_clean_new_snapshot(self):
        old = self.rows()
        at = (FIXTURE_TIME - timedelta(minutes=1)).isoformat()
        for row in old:
            row.update(scanned_at=at, received_at=at, source_updated_at=at)
        current = [quote(side='over', odds=1.95, pick=4), quote(side='under', odds=1.95, pick=5)]
        fixture = self.fixture(old + current)
        self.assertEqual(fixture['ou_main']['status'], 'estimated')
        self.assertEqual(fixture['ou_main']['diagnostics']['conflicting_duplicate_groups'], 0)
        self.assertEqual({e['row']['id'] for e in fixture['alternatives']['Over/Under']}, {4, 5})

    def test_equal_price_duplicates_reach_selector_and_count_once(self):
        rows = [quote(side='over', odds=1.95, pick=1),
                quote(side='over', odds=1.95, pick=2),
                quote(side='under', odds=1.95, pick=3)]
        main = self.fixture(rows)['ou_main']
        self.assertEqual(main['status'], 'estimated')
        self.assertEqual(main['diagnostics']['complete_pairs'], 1)
        self.assertEqual(main['diagnostics']['eligible_pairs'], 1)
        self.assertEqual(main['diagnostics']['identical_duplicate_quotes'], 1)
        self.assertEqual(set(main['pair']['quote_ids']['over']), {1, 2})
        self.assertIn('1 เจ้ามือ', main['reason'])

    def test_older_line_from_same_book_is_still_discarded(self):
        old = [quote(side='over', point=2.5, odds=1.95, pick=1),
               quote(side='under', point=2.5, odds=1.95, pick=2)]
        at = (FIXTURE_TIME - timedelta(minutes=1)).isoformat()
        for row in old:
            row.update(scanned_at=at, received_at=at, source_updated_at=at)
        current = [quote(side='over', point=3, odds=1.95, pick=3),
                   quote(side='under', point=3, odds=1.95, pick=4)]
        fixture = self.fixture(old + current)
        self.assertEqual(fixture['ou_main']['line'], 3)
        self.assertEqual({e['row']['id'] for e in fixture['alternatives']['Over/Under']}, {3, 4})

    def test_ah_latest_id_policy_remains_unchanged(self):
        rows = [quote('Asian Handicap', -.25, 'home', pick=1),
                quote('Asian Handicap', -.5, 'home', pick=2)]
        fixture = self.fixture(rows)
        self.assertEqual([e['row']['id'] for e in fixture['alternatives']['Asian Handicap']], [2])
        self.assertEqual(fixture['markets']['Asian Handicap']['row']['point'], -.5)


if __name__ == '__main__':
    unittest.main()
