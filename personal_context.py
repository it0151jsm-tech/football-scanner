"""Point-in-time personal evidence for A; never a new probability/EV model.

Only explicit exact club labels in the five supported domestic competitions are
eligible. Reviewed results may fill an absent form source; personal xG remains
context until a model for this source has been validated independently.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json

import pandas as pd

from domestic_data import season_for_kickoff
from personal_stats import eligible_history
from readiness import instant
from history_data import validate_rows

SUPPORTED_LEAGUES = frozenset(('soccer_epl', 'soccer_spain_la_liga',
    'soccer_italy_serie_a', 'soccer_germany_bundesliga', 'soccer_france_ligue_one'))


def fixture_context(connection, *, home, away, kickoff, sport_key, event_id=None,
                    analysis_at=None, team_sources=None):
    target, observed = instant(kickoff), instant(analysis_at or datetime.now(timezone.utc))
    output = dict(scope='personal_reviewed_evidence', available=False,
        analysis_at=observed.isoformat() if observed else None,
        target_kickoff=target.isoformat() if target else None,
        model_enabled=False, calibrated=False,
        model_reason='สถิติส่วนตัวเป็นหลักฐานประกอบ ยังไม่มีโมเดลสำหรับแหล่งนี้ที่ผ่าน Validation/Calibration — ไม่สร้าง EV ทดแทน',
        form_fallback_sides=[], excluded_conflicts=0, excluded_duplicates=0,
        home={}, away={})
    if target is None or observed is None or not isinstance(home,str) or not isinstance(away,str) or home == away:
        output['status']='invalid_fixture';return output, pd.DataFrame()
    cutoff=min(target,observed)
    output['as_of']=cutoff.isoformat()
    sources=team_sources or {'home':sport_key,'away':sport_key}
    frames=[]
    for side, name in (('home',home),('away',away)):
        league=sources.get(side)
        info=dict(team=name,league=league,season=season_for_kickoff(target),
                  status='unsupported_league',records=[],sample=0)
        output[side]=info
        if league not in SUPPORTED_LEAGUES:
            continue
        records=eligible_history(connection,team=name,before=target,as_of=cutoff,
            league=league,season=info['season'],exclude_match_key=event_id)
        accepted=[]
        for record in records:
            day=instant(record.get('kickoff_utc'))
            received=instant(record.get('received_at'))
            reviewed=instant(record.get('reviewed_at'))
            if record.get('league')!=league or record.get('season')!=info['season']:
                continue
            if record.get('evidence_mode')!='observed' or record.get('reviewed')!=1 or record.get('completed')!=1 or record.get('verified')!=1:
                continue
            if received is None or reviewed is None or received>=cutoff or reviewed>=cutoff or day is None or day>=cutoff:
                continue
            if received>reviewed:
                continue
            if day is None or day.date()>=target.date() or event_id in (record.get('match_key'),record.get('source_match_id')) and event_id:
                continue
            # Storage/query does exact identity filtering; validate again at boundary.
            if name not in (record.get('home_team'),record.get('away_team')):
                continue
            if any(isinstance(record.get(k),bool) or not isinstance(record.get(k),int) or record[k]<0
                   for k in ('home_goals','away_goals')):
                continue
            accepted.append(record)
        info.update(status='eligible' if accepted else 'no_point_in_time_evidence',sample=len(accepted),
            records=[{k:r.get(k) for k in ('revision_id','match_key','kickoff_utc','home_team','away_team',
                'home_goals','away_goals','source','source_url','recorded_at','received_at','reviewed_at',
                'league','season','stats_json')} for r in accepted])
        for r in accepted:
            frames.append(dict(Date_dt=pd.Timestamp(instant(r['kickoff_utc']).date()),HomeTeam=r['home_team'],
                AwayTeam=r['away_team'],FTHG=r['home_goals'],FTAG=r['away_goals'],
                _domestic_sport_key=league,_history_season=info['season'],
                _history_source_url=r.get('source_url'),_history_received_at=r.get('received_at') or r.get('recorded_at'),
                _personal_revision_id=r.get('revision_id'),_personal_source=r['source']))
    frame=pd.DataFrame(frames)
    if not frame.empty:
        keys=['_domestic_sport_key','Date_dt','HomeTeam','AwayTeam']
        conflict=frame.groupby(keys)[['FTHG','FTAG']].transform('nunique').gt(1).any(axis=1)
        output['excluded_conflicts']=int(conflict.sum())
        frame=frame[~conflict].copy()
        duplicate=frame.duplicated(keys)
        output['excluded_duplicates']=int(duplicate.sum())
        frame=frame[~duplicate].sort_values('Date_dt').reset_index(drop=True)
    output.update(available=not frame.empty,status='eligible' if not frame.empty else 'no_eligible_history')
    return output,frame


def supplement_form_history(base, personal, context):
    """Use one source per club; never silently pool an existing source with uploads."""
    result=base.copy() if isinstance(base,pd.DataFrame) else pd.DataFrame()
    attrs=deepcopy(result.attrs)
    if personal.empty:
        return result
    if not result.empty:
        original_count=len(result)
        result,_=validate_rows(result)
        context['base_invalid_rows']=original_count-len(result)
        if '_domestic_sport_key' in result:
            expected={context[s].get('team'):context[s].get('league') for s in ('home','away')}
            for column in ('HomeTeam','AwayTeam'):
                result[column]=result.apply(lambda r:
                    f'__foreign__[{r["_domestic_sport_key"]}]::{r[column]}'
                    if r[column] in expected and r['_domestic_sport_key']!=expected[r[column]]
                    else r[column],axis=1)
    additions=[]
    fallback=set()
    for side in ('home','away'):
        name=context[side].get('team');league=context[side].get('league')
        existing=(result[(result.HomeTeam==name)|(result.AwayTeam==name)]
            if {'HomeTeam','AwayTeam'}.issubset(result.columns) else pd.DataFrame())
        if not existing.empty and 'Date_dt' in existing:
            dates=pd.to_datetime(existing.Date_dt,errors='coerce',utc=True)
            existing=existing[dates.dt.date < instant(context['target_kickoff']).date()]
        if not existing.empty and '_domestic_sport_key' in existing:
            existing=existing[existing._domestic_sport_key.eq(league)]
        if existing.empty:
            fallback.add((name,league))
    for side in ('home','away'):
        name=context[side].get('team'); league=context[side].get('league')
        existing=(result[(result.HomeTeam==name)|(result.AwayTeam==name)]
                  if {'HomeTeam','AwayTeam'}.issubset(result.columns) else pd.DataFrame())
        if not existing.empty and 'Date_dt' in existing:
            dates=pd.to_datetime(existing.Date_dt,errors='coerce',utc=True)
            existing=existing[dates.dt.date < instant(context['target_kickoff']).date()]
        if not existing.empty and '_domestic_sport_key' in existing:
            existing=existing[existing._domestic_sport_key.eq(league)]
        if (name,league) not in fallback:
            context[side]['form_status']='existing_source_retained';continue
        selected=personal[(personal._domestic_sport_key==league)&
            ((personal.HomeTeam==name)|(personal.AwayTeam==name))].copy()
        if selected.empty:
            context[side]['form_status']='no_eligible_scores';continue
        # Namespacing unselected opponents prevents pooled cross-league identities.
        for column in ('HomeTeam','AwayTeam'):
            selected[column]=selected.apply(lambda r: r[column] if (r[column],league) in fallback
                else f'__personal__[{league}]::{r[column]}',axis=1)
        additions.append(selected);context['form_fallback_sides'].append(side)
        context[side]['form_status']='reviewed_personal_scores_only'
    if not additions:
        return result
    result=pd.concat([result,*additions],ignore_index=True).drop_duplicates(
        ['Date_dt','HomeTeam','AwayTeam']).sort_values('Date_dt').reset_index(drop=True)
    result.attrs=attrs
    result.attrs['personal_evidence']=deepcopy(context)
    if not attrs.get('history_diagnostics') or not isinstance(base,pd.DataFrame) or base.empty:
        result.attrs['history_diagnostics']={'status':'ok','scope':'human-reviewed personal results; no model xG',
            'sources':[]}
    return result
