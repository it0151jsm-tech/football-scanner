"""One read-only display row per fixture/market, with all raw quotes retained.

The original A/B/C and OU main-line policies decide whether an exact saved
quote can be a recommendation. An anchor shown for an unresolved market is
explicitly an evaluation, never a default Over/Under directional selection.
This module performs no model calculation, database write or provider request.
"""
from copy import deepcopy

from market_recommendations import MARKETS, TREND, group_fixture_recommendations

EVALUATING = 'ตัวเลือกตลาดที่กำลังประเมิน — ไม่ใช่คำแนะนำเดิมพัน'
NO_PRICE = 'ไม่มีราคาตลาด'
NO_BET = 'ยังไม่แนะนำ'


def _missing_market(anchor, market, *, identity_valid):
    # Copy fixture metadata only. A score/EV from another market is not evidence
    # for an absent contract, and a placeholder must not identify a stored pick.
    row = {key:deepcopy(anchor.get(key)) for key in (
        'event_id','home_team','away_team','league','match','kickoff_utc','kickoff_th',
        'scanned_at','received_at','source_updated_at')}
    row.update(id=None,market=market,side=None,point=None,odds=None,selection=NO_PRICE,
        bookmaker=None,bookmaker_key=None,ev_pct=None,p_win=None,p_partial=None,p_loss=None,
        score=None,confidence_score=None,recommendation=NO_BET,
        analysis_reason=NO_PRICE,system_c_reason=NO_PRICE,summary_reason=NO_PRICE,
        summary_recommended=False,summary_status='no_market_price',
        summary_identity_valid=identity_valid,summary_alternative_count=0,
        odds_in_range=0,ou_selected=False)
    if market == 'Over/Under':
        row['ou_main_status']='missing'
    return row


def summary_market_rows(rows, choose_c, *, include_missing=True):
    """Return at most one AH and one OU row for each original fixture group.

    ``include_missing`` adds explicit no-price display rows for absent markets;
    it does not fabricate a quote, side, line, bookmaker, probability or EV.
    Missing event IDs stay isolated under the original grouping contract and
    can never become a verified recommendation. Callers retain ``rows`` or use
    ``group_fixture_recommendations`` to inspect all saved market alternatives.
    """
    result=[]
    for fixture in group_fixture_recommendations(rows,choose_c):
        event_id=fixture.get('event_id')
        identity_valid=(isinstance(event_id,str) and bool(event_id.strip())
                        and not fixture.get('identity_conflict',False))
        for market in MARKETS:
            entry=fixture['markets'].get(market)
            if entry is None:
                if include_missing:
                    result.append(_missing_market(fixture['row'],market,identity_valid=identity_valid))
                continue
            row=deepcopy(entry['row'])
            alternatives=fixture['alternatives'].get(market,[])
            in_range=row.get('odds_in_range',1) in (1,True)
            reason=entry['reason']
            conclusion=entry['conclusion']
            recommended=(identity_valid and in_range and entry.get('tier')==4
                         and entry.get('ev') is not None and entry['ev']>0)
            row['ev_pct']=entry.get('ev')
            status='recommended' if recommended else 'evaluating'
            if market=='Over/Under':
                recommended=(recommended and fixture.get('ou_direction')==row.get('side')
                             and fixture.get('ou_main',{}).get('pair') is not None)
                reason=fixture['ou_reason']
                row.update(ou_main_status=fixture['ou_main']['status'],ou_selected=recommended)
                if recommended:
                    conclusion=reason
                    status='recommended'
                else:
                    status='evaluating'
                    # A model may price this exact saved quote even when Main
                    # cannot be confirmed. That remains a quote being evaluated.
                    conclusion=TREND if entry.get('ev') is None and entry['conclusion']==TREND else EVALUATING
            if not in_range:
                recommended=False
                status='outside_configured_price_range'
                conclusion=NO_BET
                reason+=' · ราคาอยู่นอกกรอบที่เลือกไว้'
            if not identity_valid:
                recommended=False
                status='identity_unverified'
                conclusion=NO_BET
                reason+=' · ตัวตนคู่แข่งขันขาดหรือขัดแย้ง — รอตรวจด้วยมือ'
            row.update(recommendation=conclusion,analysis_reason=reason,system_c_reason=reason,
                summary_reason=reason,summary_status=status,summary_recommended=recommended,
                summary_identity_valid=identity_valid,summary_alternative_count=len(alternatives))
            # Even when an original OU entry happened to be directional, an
            # identity/range gate above must clear the summary directional flag.
            if market=='Over/Under':
                row['ou_selected']=recommended
            result.append(row)
    return result
