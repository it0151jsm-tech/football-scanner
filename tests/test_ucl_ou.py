"""Synthetic contract tests: no real Bayern/Arsenal prices, statistics or API calls."""
import unittest

from ou_market import compare_sides, select_main_line


def entry(side, line=3.0, odds=1.95, bookmaker='Synthetic Book', key='synthetic-book',
          event='synthetic-ucl-event', at='2026-10-21T12:00:00+00:00', **changes):
    # Minimal saved-quote contract. There is deliberately no xG or model B result.
    row = dict(event_id=event, bookmaker=bookmaker, bookmaker_key=key,
               side=side, point=line, odds=odds, scanned_at=at,
               received_at=at,source_updated_at=at)
    row.update(changes)
    return dict(row=row,model_valid=False,ev=None,b={},tier=0)


def pair(line=3.0,**changes):
    return [entry(side,line,**changes) for side in ('over','under')]


class UCLMainLineDiagnosticsTests(unittest.TestCase):
    def test_complete_saved_pair_estimates_line_without_faking_ucl_ev(self):
        main=select_main_line(pair())
        self.assertEqual(main['status'],'estimated')
        self.assertEqual(main['line'],3.0)
        self.assertEqual(main['diagnostics']['complete_pairs'],1)
        chosen,reason=compare_sides(main)
        self.assertIsNone(chosen)
        self.assertIn('EV สองฝั่งยังไม่ครบ',reason)
        self.assertIsNone(main['pair']['over']['ev'])

    def test_ah_only_fixture_has_no_ou_contract_to_estimate(self):
        # AH -0.25 at 2.00 cannot supply either OU side or a goal-total line.
        ah=entry('home',line=-.25,odds=2.00)
        totals=[e for e in [ah] if e['row']['side'] in ('over','under')]
        main=select_main_line(totals)
        self.assertEqual(main['reason_code'],'missing_market')
        self.assertIn('ไม่พบราคา OU',main['reason'])
        self.assertIsNone(main['line'])
        self.assertEqual(main['diagnostics']['input_quotes'],0)

    def test_one_side_reports_exact_missing_side_without_inventing_other_price(self):
        for side,missing in [('over','Under'),('under','Over')]:
            main=select_main_line([entry(side)])
            self.assertEqual(main['reason_code'],'missing_side')
            self.assertIn(missing,main['reason'])
            self.assertEqual(main['diagnostics']['unpaired_groups'],1)
            self.assertEqual(main['pairs'],[])

    def test_different_lines_never_pair(self):
        main=select_main_line([entry('over',line=2.75),entry('under',line=3.0)])
        self.assertEqual(main['reason_code'],'contract_mismatch')
        self.assertEqual(main['diagnostics']['unpaired_groups'],2)
        self.assertIsNone(main['line'])

    def test_same_display_title_with_different_provider_keys_never_pairs(self):
        main=select_main_line([entry('over',key='book-a'),entry('under',key='book-b')])
        self.assertEqual(main['reason_code'],'provider_key_conflict')
        self.assertIn('bookmaker_key',main['reason'])
        self.assertEqual(main['diagnostics']['provider_key_conflicts'],1)
        self.assertIsNone(main['pair'])

    def test_other_event_book_or_snapshot_never_supplies_missing_side(self):
        for changes in [dict(event_id='different-event'),dict(bookmaker='Different Book',bookmaker_key='other-key'),
                        dict(scanned_at='2026-10-21T12:01:00+00:00'),
                        dict(received_at='2026-10-21T12:01:00+00:00'),
                        dict(source_updated_at='2026-10-21T11:59:00+00:00')]:
            with self.subTest(changes=changes):
                main=select_main_line([entry('over'),entry('under',**changes)])
                self.assertEqual(main['reason_code'],'contract_mismatch')
                self.assertIsNone(main['pair'])

    def test_invalid_quotes_have_counts_and_never_make_a_synthetic_pair(self):
        for change in [dict(odds=None),dict(odds=float('nan')),dict(point=2.6),dict(bookmaker='Unknown')]:
            with self.subTest(change=change):
                main=select_main_line([entry('over',**change)])
                self.assertEqual(main['reason_code'],'invalid_quotes')
                self.assertEqual(main['diagnostics']['invalid_quotes'],1)
                self.assertEqual(main['diagnostics']['valid_quotes'],0)

    def test_complete_unbalanced_pair_reports_market_evidence_limit(self):
        main=select_main_line([entry('over',odds=1.45),entry('under',odds=2.80)])
        self.assertEqual(main['reason_code'],'unbalanced_pairs')
        self.assertEqual(main['diagnostics']['complete_pairs'],1)
        self.assertEqual(main['diagnostics']['eligible_pairs'],0)
        self.assertIn('margin 0.98–1.20',main['reason'])

    def test_equal_support_and_balance_does_not_invent_a_main_line(self):
        main=select_main_line(pair(2.5)+pair(3.0))
        self.assertEqual(main['reason_code'],'ambiguous_lines')
        self.assertEqual(main['diagnostics']['candidate_lines'],2)
        self.assertIsNone(main['line'])

    def test_legacy_quotes_without_provider_keys_can_describe_line_but_not_ev(self):
        rows=pair()
        for e in rows:e['row'].pop('bookmaker_key')
        main=select_main_line(rows)
        self.assertEqual(main['status'],'estimated')
        self.assertIsNone(compare_sides(main)[0])
