"""Offline quota accounting regressions; no API credits or real database."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from odds_budget import Budget,LeagueScanError,ScanStopped,settings
from scan_control import run_selected_leagues


class BudgetRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.budget=Budget(Path(self.tmp.name)/'ledger.db')
        self.now=datetime(2026,10,24,12,tzinfo=timezone.utc)
        self.limits=dict(requests=100,credits=500,monthly_credits=500,reserve=0,cooldown=300)

    def call(self,sport='sport',**changes):
        return self.budget.reserve('TEST-ONLY',sport,'uk',self.now,{**self.limits,**changes})

    def test_defaults_allow_one_manual_38_league_scan_but_cap_owner_month(self):
        names=('VALUE_BET_API_DAILY_REQUESTS','VALUE_BET_API_DAILY_CREDITS',
               'VALUE_BET_API_MONTHLY_CREDITS','VALUE_BET_API_CREDIT_RESERVE','VALUE_BET_API_COOLDOWN_SECONDS')
        with patch.dict(os.environ,{key:value for key,value in os.environ.items() if key not in names},clear=True):
            limits=settings()
        self.assertEqual((limits['requests'],limits['credits'],limits['monthly_credits'],limits['reserve']),(40,80,500,10))
        for index in range(38):
            self.budget.reserve('TEST-ONLY',f'sport-{index}','uk',self.now,limits)
        snapshot=self.budget.snapshot('TEST-ONLY',self.now,limits)
        self.assertEqual((snapshot['requests_today'],snapshot['credits_today'],snapshot['credits_month']),(38,76,76))
        self.assertIsNone(snapshot['provider_remaining'])

    def test_eleven_leagues_cost_twenty_two_and_no_key_is_persisted(self):
        for index in range(11):self.call(str(index))
        snapshot=self.budget.snapshot('TEST-ONLY',self.now,self.limits)
        self.assertEqual(snapshot['credits_today'],22)
        self.assertNotIn(b'TEST-ONLY',self.budget.path.read_bytes())
        self.assertNotIn('TEST-ONLY',str(snapshot))

    def test_calendar_month_cap_survives_daily_reset(self):
        self.call('first',monthly_credits=4)
        tomorrow=self.now+timedelta(days=1)
        self.budget.reserve('TEST-ONLY','second','uk',tomorrow,{**self.limits,'monthly_credits':4})
        with self.assertRaises(ScanStopped) as caught:
            self.budget.reserve('TEST-ONLY','third','uk',tomorrow+timedelta(days=1),{**self.limits,'monthly_credits':4})
        self.assertEqual(caught.exception.reason,'monthly_budget_reached')

    def test_month_cap_atomic_across_threads_sharing_one_ledger(self):
        def reserve(index):
            try:self.call(str(index),monthly_credits=12);return True
            except ScanStopped:return False
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(reserve,range(12))),6)
        self.assertEqual(self.budget.snapshot('TEST-ONLY',self.now,self.limits)['credits_month'],12)

    def test_unknown_failures_remain_charged_and_known_zero_cost_is_exact(self):
        first=self.call('network')
        self.budget.complete(first,'network_error',{})
        second=self.call('zero-cost')
        self.budget.complete(second,200,{'x-requests-last':'0'})
        self.assertEqual(self.budget.snapshot('TEST-ONLY',self.now,self.limits)['credits_today'],2)

    def test_monthly_reserve_applies_when_provider_headers_are_unknown(self):
        self.call('first',monthly_credits=4,reserve=1)
        with self.assertRaises(ScanStopped) as caught:self.call('second',monthly_credits=4,reserve=1)
        self.assertEqual(caught.exception.reason,'monthly_budget_reached')

    def test_one_region_per_request_is_required_for_two_credit_reservation(self):
        for region in ('uk,eu','eu,us','UK','au','',None):
            with self.subTest(region=region),self.assertRaises(LeagueScanError) as caught:
                self.budget.reserve('TEST-ONLY','sport',region,self.now,self.limits)
            self.assertFalse(caught.exception.halt_all)
        for region in ('uk','eu','us'):
            self.budget.reserve('TEST-ONLY',region,region,self.now,self.limits)

    def test_cooldown_is_local_failure_with_legacy_exception_compatibility(self):
        self.call()
        with self.assertRaises(ScanStopped) as caught:self.call()
        self.assertIsInstance(caught.exception,LeagueScanError)
        self.assertEqual(caught.exception.reason,'cooldown')
        self.assertFalse(caught.exception.halt_all)
        self.call('different-league')

    def test_auth_quota_or_payment_errors_block_future_requests_globally(self):
        for status in (401,402,403,429):
            key=str(status)
            call=self.budget.reserve(key,'first','uk',self.now,self.limits)
            with self.subTest(status=status),self.assertRaises(ScanStopped) as caught:
                self.budget.complete(call,status,{})
            self.assertTrue(caught.exception.halt_all)
            self.assertEqual(caught.exception.status,status)
            with self.assertRaises(ScanStopped):self.budget.reserve(key,'second','uk',self.now,self.limits)

    def test_temporary_http_errors_do_not_block_unrelated_league_reservations(self):
        for status in (404,422,500,502,503,504):
            key=str(status)
            call=self.budget.reserve(key,'first','uk',self.now,self.limits)
            self.budget.complete(call,status,{})
            self.budget.reserve(key,'second','uk',self.now,self.limits)

    def test_provider_headers_case_insensitive_and_older_response_cannot_inflate_balance(self):
        first,second=self.call('first'),self.call('second')
        self.budget.complete(second,200,{'X-Requests-Last':'2','X-Requests-Remaining':'6','X-Requests-Used':'494'})
        self.budget.complete(first,200,{'x-requests-last':'2','x-requests-remaining':'8','x-requests-used':'492'})
        snapshot=self.budget.snapshot('TEST-ONLY',self.now,self.limits)
        self.assertEqual(snapshot['provider_used'],494)
        # The newer header had another request in flight: preserve its charge.
        self.assertEqual(snapshot['provider_remaining'],4)

    def test_completed_request_cannot_be_recharged_refunded_or_refresh_balance(self):
        call=self.call('first')
        self.budget.complete(call,200,{'x-requests-last':'2','x-requests-remaining':'98','x-requests-used':'2'})
        self.call('second')
        self.budget.complete(call,200,{'x-requests-last':'2','x-requests-remaining':'98','x-requests-used':'2'})
        self.assertEqual(self.budget.snapshot('TEST-ONLY',self.now,self.limits)['provider_remaining'],96)
        with self.assertRaises(ScanStopped):self.budget.complete(call,200,{'x-requests-last':'0'})

    def test_month_limit_invalid_or_higher_than_owner_plan_is_rejected(self):
        for value in (0,-1,501,'500',True):
            with self.subTest(value=value),self.assertRaises(ScanStopped):self.call(monthly_credits=value)
        with self.assertRaises(ScanStopped):
            self.budget.reserve('TEST-ONLY','sport','uk',self.now.replace(tzinfo=None),self.limits)

    def test_new_utc_month_has_new_local_cap_without_assuming_provider_credit_reset(self):
        call=self.call('old-month',monthly_credits=2)
        self.budget.complete(call,200,{'x-requests-last':'2','x-requests-remaining':'1','x-requests-used':'499'})
        next_month=datetime(2026,11,1,tzinfo=timezone.utc)
        with self.assertRaises(ScanStopped) as caught:
            self.budget.reserve('TEST-ONLY','new-month','uk',next_month,{**self.limits,'monthly_credits':2})
        self.assertEqual(caught.exception.reason,'provider_balance_reserve')

    def test_authored_prior_usage_reproduces_three_of_eleven_then_new_budget_covers_eleven(self):
        """Mechanism reproduction only: these counts are not the incident's data."""
        selected=[f'selected-{number}' for number in range(11)]
        leagues={key:(key,None) for key in selected}
        authored_counts=[6,6,7]+[2]*8
        def exercise(name,requests,credits):
            # Independent ledgers begin with the SAME seven authored prior
            # requests; using separate copies avoids cooldown from the control.
            budget=Budget(self.folder_for_case(name))
            limits=dict(requests=requests,credits=credits,monthly_credits=500,reserve=10,cooldown=300)
            for index in range(7):
                call=budget.reserve('SYNTHETIC_ONLY',f'prior-{index}','uk',self.now,limits)
                budget.complete(call,200,{})
            reports={}
            def scanner(key,label,code):
                call=budget.reserve('SYNTHETIC_ONLY',key,'uk',self.now,limits)
                budget.complete(call,200,{})
                count=authored_counts[selected.index(key)]
                reports[key]=dict(api_returned=count,passed_filter=count,excluded_count=0,excluded_by_reason={})
                return [dict(event_id=f'{key}-fixture-{number}',market='Asian Handicap')
                        for number in range(count)],[],None,None
            result=run_selected_leagues(selected,leagues,scanner,report_for=reports.get)
            accounting=budget.snapshot('SYNTHETIC_ONLY',self.now,limits)
            return dict(counts=result['counts'],league_statuses=[row['status'] for row in result['leagues']],
                stop_reason=result['stop_reason'],accounting=accounting)
        legacy=exercise('legacy',10,20)
        corrected=exercise('corrected',40,80)
        self.assertEqual(legacy['counts']['successful_leagues'],3)
        self.assertEqual(legacy['counts']['saved_fixture_count'],19)
        self.assertEqual(legacy['counts']['safety_stops'],1)
        self.assertEqual(legacy['counts']['skipped_safety_stop'],7)
        self.assertEqual(legacy['stop_reason'],'daily_budget_reached')
        self.assertEqual(legacy['accounting']['credits_today'],20)
        self.assertEqual(corrected['counts']['successful_leagues'],11)
        self.assertEqual(corrected['counts']['saved_fixture_count'],35)
        self.assertEqual(corrected['counts']['safety_stops'],0)
        self.assertEqual(corrected['counts']['skipped_safety_stop'],0)
        self.assertEqual(corrected['accounting']['credits_today'],36)
        folder=os.environ.get('DASHBOARD_SCREENSHOTS')
        if folder:
            target=Path(folder);target.mkdir(parents=True,exist_ok=True)
            report=dict(data_mode='synthetic_test_fixture',case='prior_daily_budget_exhaustion_11_to_3',
                real_provider_calls=0,incident_confirmed=False,
                limitation='Original incident ledger and API responses were not supplied; authored counts prove a code mechanism, not the actual incident cause.',
                authored_fixture_counts=authored_counts,prior_requests=7,prior_charged_credits=14,
                legacy=legacy,corrected=corrected)
            (target/'operational-prior-budget-three-of-eleven.json').write_text(
                json.dumps(report,ensure_ascii=False,indent=2))

    def folder_for_case(self,name):
        return Path(self.tmp.name)/(name+'-ledger.db')

