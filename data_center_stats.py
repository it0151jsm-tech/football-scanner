"""Descriptive, independently loaded football observations for Data Center.

No model, probability, EV, standing estimate or HTTP client is used here. Source
callbacks are supplied by the existing application. One failed callback cannot
discard another source or the fixture that the Data Center already persisted.
"""
from copy import deepcopy
from datetime import datetime, timezone
import math
import re
from urllib.parse import parse_qsl, urlsplit

import pandas as pd

from history_data import match_team, team_history, validate_rows, _team_identity
from readiness import instant
from understat_history import SUPPORTED_SPORTS, eligible_xg_history

UTC = timezone.utc
CUPS = frozenset(('soccer_uefa_champs_league','soccer_uefa_europa_league',
                  'soccer_uefa_europa_conference_league'))
SOURCE_STATES = frozenset(('ok','success','partial','empty','empty_history','empty_file',
    'download_error','http_error','forbidden','rate_limited','unsupported','unsupported_source',
    'unsupported_xg','invalid_dates','invalid_scores','invalid_rows','invalid_json','invalid_schema',
    'missing_columns','invalid_payload','empty_payload','conflicting_scores','conflicting_fixtures',
    'invalid_season','not_called','not_configured','plan_unsupported','provider_error',
    'invalid_response','cache','source_unavailable','no_eligible_history'))


def _finite(value):
    try:
        return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)
    except OverflowError:
        return False


def _url(value):
    if not isinstance(value,str):
        return None
    try:
        parsed=urlsplit(value)
        if parsed.scheme not in ('http','https') or not parsed.netloc or parsed.username or parsed.password:
            return None
        if any(re.search(r'api.?key|token|secret|authorization',key,re.I) or key.casefold()=='key'
               for key,_ in parse_qsl(parsed.query)):
            return None
    except ValueError:
        return None
    return value


def _clock(value):
    stamp=instant(value)
    return stamp.isoformat() if stamp is not None else None


def _state(value):
    return value if isinstance(value,str) and value in SOURCE_STATES else 'unknown'


def _source(provider,frame=None,*,status=None,reason=None,now=None):
    attrs=getattr(frame,'attrs',{})
    diagnostic=attrs.get('xg_diagnostics') if provider=='Understat' else attrs.get('history_diagnostics')
    diagnostic=diagnostic if isinstance(diagnostic,dict) else {}
    reports=diagnostic.get('sources')
    reports=reports if isinstance(reports,list) else [diagnostic] if diagnostic else []
    sources=[]
    receipts=[]
    for report in reports:
        if not isinstance(report,dict):
            continue
        item={}
        for key in ('url','source_url','final_url'):
            safe=_url(report.get(key))
            if safe:
                item[key]=safe
        receipt=_clock(report.get('received_at'))
        if receipt:
            receipts.append(receipt)
            item['received_at']=receipt
        updated=_clock(report.get('updated_at') or report.get('source_updated_at'))
        if updated:
            item['source_updated_at']=updated
        season=report.get('season')
        if isinstance(season,(str,int)) and not isinstance(season,bool) and re.fullmatch(r'\d{4}',str(season)):
            item['season']=str(season)
        item['status']=_state(report.get('status',diagnostic.get('status')))
        http=report.get('http_status')
        if type(http) is int and 100 <= http <= 599:
            item['http_status']=http
        digest=report.get('payload_sha256')
        if isinstance(digest,str) and re.fullmatch('[a-fA-F0-9]{64}',digest):
            item['payload_sha256']=digest
        for key in ('raw_rows','valid_rows','usable_rows','rejected_rows'):
            if type(report.get(key)) is int and report[key]>=0:
                item[key]=report[key]
        sources.append(item)
    if frame is not None and not frame.empty and '_history_received_at' in frame:
        receipts.extend(value for value in map(_clock,frame['_history_received_at']) if value)
    receipts=sorted(set(receipts))
    source_status=status or _state(diagnostic.get('status'))
    if source_status=='unknown':
        source_status='success' if frame is not None and not frame.empty else 'no_data'
    cache=attrs.get('cache_diagnostics')
    cache=cache if isinstance(cache,dict) else {}
    return {'provider':provider,'status':source_status,'reason':reason,
        'source_status':_state(diagnostic.get('status')),'received_at':receipts,
        'cache_status':cache.get('status') if cache.get('status') in ('cache','fetched','unknown_receipt') else
            'cache' if diagnostic.get('cache_hit') is True or diagnostic.get('state')=='cache' else 'unknown',
        'sources':sources,'receipt_status':'known' if receipts else 'unknown',
        'scope':'descriptive source observations; not a model or past-availability claim'}


def _empty_frame():
    return pd.DataFrame()


def _clean(frame,cutoff,*,xg=False,available_at=None):
    """Validate scores and quarantine conflicts before selecting any window."""
    if not isinstance(frame,pd.DataFrame):
        return _empty_frame(),{'status':'invalid_response','input_rows':0,'accepted_rows':0}
    attributes=deepcopy(frame.attrs)
    cleaned,validation=validate_rows(frame)
    audit={'input_rows':len(frame),'validation':validation,'late_rows':0,'conflicting_rows':0,
           'duplicate_rows':0,'invalid_xg_rows':0,'accepted_rows':0}
    if cleaned.empty:
        audit['status']=validation.get('status','no_data')
        cleaned.attrs=attributes
        return cleaned,audit
    # A date-only result cannot prove availability earlier on the same UTC date.
    day=pd.Timestamp(cutoff).tz_convert('UTC').tz_localize(None).normalize()
    before=cleaned.Date_dt.lt(day)
    audit['late_rows']=int((~before).sum())
    cleaned=cleaned[before].copy()
    if '_history_received_at' in cleaned:
        # Missing receipts stay explicitly unknown. A supplied malformed/future
        # receipt cannot certify an observation that the host has not received.
        analysis=available_at or cutoff
        def receipt_ready(value):
            if value is None or (isinstance(value,float) and math.isnan(value)) or value is pd.NA or value is pd.NaT:
                return True
            if not isinstance(value,(str,datetime)):
                return False
            stamp=instant(value)
            return stamp is not None and stamp<=analysis
        receipts=cleaned['_history_received_at'].map(receipt_ready)
        audit['late_or_invalid_receipt_rows']=int((~receipts).sum())
        cleaned=cleaned[receipts].copy()
    self_play=cleaned.apply(lambda row:match_team(row.HomeTeam,[row.AwayTeam])[0] is not None,axis=1)
    cleaned=cleaned[~self_play].copy()
    audit['self_match_rows']=int(self_play.sum())
    if xg and not {'xG_home','xG_away'}.issubset(cleaned.columns):
        audit.update(status='missing_xg_columns',accepted_rows=0)
        return cleaned.iloc[0:0].copy(),audit
    keys=['Date_dt','HomeTeam','AwayTeam']
    values=['FTHG','FTAG']+(['xG_home','xG_away'] if xg else [])
    if not cleaned.empty:
        conflict=cleaned.groupby(keys,dropna=False)[values].transform('nunique',dropna=False).gt(1).any(axis=1)
        if xg and '_understat_id' in cleaned:
            ids=cleaned['_understat_id'].notna()
            conflicting_ids=cleaned.loc[ids].groupby('_understat_id')[keys+values].transform('nunique',dropna=False).gt(1).any(axis=1)
            conflict.loc[conflicting_ids.index]|=conflicting_ids
        audit['conflicting_rows']=int(conflict.sum())
        cleaned=cleaned[~conflict].copy()
        duplicate=cleaned.duplicated(keys,keep='first')
        audit['duplicate_rows']=int(duplicate.sum())
        cleaned=cleaned[~duplicate].copy()
    if xg and not cleaned.empty:
        good=cleaned[['xG_home','xG_away']].apply(lambda series:series.map(
            lambda value:_finite(value) and value>=0)).all(axis=1)
        audit['invalid_xg_rows']=int((~good).sum())
        cleaned=cleaned[good].copy()
    cleaned=cleaned.sort_values('Date_dt').reset_index(drop=True)
    cleaned.attrs=attributes
    audit.update(status='success' if not cleaned.empty else 'no_data',accepted_rows=len(cleaned))
    return cleaned,audit


def _safe_clean(frame,cutoff,*,xg=False,available_at=None):
    try:
        return _clean(frame,cutoff,xg=xg,available_at=available_at)
    except Exception:
        # Malformed content from one source remains that source's quality
        # failure, never a reason to hide another source or the saved fixture.
        return _empty_frame(),{'status':'invalid_response','reason':'source_rows_failed_validation',
            'input_rows':len(frame) if isinstance(frame,pd.DataFrame) else 0,'accepted_rows':0}


def _provenance(row):
    season=row.get('_history_season')
    if not isinstance(season,(str,int)) or isinstance(season,bool) or not re.fullmatch(r'\d{4}',str(season)):
        season=None
    return {'date':str(pd.Timestamp(row['Date_dt']).date()),'season':str(season) if season is not None else None,
        'source_url':_url(row.get('_history_source_url')),
        'source_received_at':_clock(row.get('_history_received_at'))}


def _window(games,team,size,*,xg=False):
    sample=games.tail(size)
    matches=[]
    for _,row in sample.iterrows():
        is_home=row.HomeTeam==team
        item={**_provenance(row),'venue':'home' if is_home else 'away',
            'opponent':str(row.AwayTeam if is_home else row.HomeTeam)}
        if xg:
            item.update(xg=float(row.xG_home if is_home else row.xG_away),
                        xga=float(row.xG_away if is_home else row.xG_home))
        else:
            gf,ga=int(row.FTHG if is_home else row.FTAG),int(row.FTAG if is_home else row.FTHG)
            item.update(goals_for=gf,goals_against=ga,result='W' if gf>ga else 'L' if gf<ga else 'D')
        matches.append(item)
    if xg:
        return {'sample':len(matches),'xg_avg':sum(item['xg'] for item in matches)/len(matches) if matches else None,
            'xga_avg':sum(item['xga'] for item in matches)/len(matches) if matches else None,'matches':matches}
    return {'sample':len(matches),'wins':sum(item['result']=='W' for item in matches),
        'draws':sum(item['result']=='D' for item in matches),'losses':sum(item['result']=='L' for item in matches),
        'form':[item['result'] for item in matches],
        'gf_avg':sum(item['goals_for'] for item in matches)/len(matches) if matches else None,
        'ga_avg':sum(item['goals_against'] for item in matches)/len(matches) if matches else None,
        'matches':matches}


def _team(frame,xg_frame,name,venue,cutoff,*,scope='selected_league'):
    overall,diagnostic=team_history(frame,name,cutoff)
    at_venue,venue_diagnostic=team_history(frame,name,cutoff,venue)
    matched=diagnostic.get('matched_name')
    xg_overall,xg_diagnostic=team_history(xg_frame,name,cutoff)
    xg_at_venue,xg_venue_diagnostic=team_history(xg_frame,name,cutoff,venue)
    xg_matched=xg_diagnostic.get('matched_name')
    return {'name':name,'matched_name':matched,'mapping':diagnostic.get('mapping'),
        'status':'available' if not overall.empty else diagnostic.get('status','no_data'),
        'scope':scope,'diagnostics':{'overall_status':diagnostic.get('status'),
            'venue_status':venue_diagnostic.get('status'),'overall_sample':len(overall),'venue_sample':len(at_venue)},
        'overall':{f'last{n}':_window(overall,matched,n) for n in (5,10)},
        'venue':{'venue':venue,**{f'last{n}':_window(at_venue,matched,n) for n in (5,10)}},
        'xg':{'status':'available' if not xg_overall.empty else xg_diagnostic.get('status','no_data'),
            'matched_name':xg_matched,'mapping':xg_diagnostic.get('mapping'),'scope':scope,
            'diagnostics':{'overall_status':xg_diagnostic.get('status'),'venue_status':xg_venue_diagnostic.get('status')},
            'overall':{f'last{n}':_window(xg_overall,xg_matched,n,xg=True) for n in (5,10)},
            'venue':{'venue':venue,**{f'last{n}':_window(xg_at_venue,xg_matched,n,xg=True) for n in (5,10)}}}}


def _h2h(frame,home,away):
    result={'status':'no_data','sample':0,'matches':[],'scope':'completed selected-league matches only; display data, no model weight'}
    if frame.empty:
        return result
    names=set(frame.HomeTeam)|set(frame.AwayTeam)
    mapped_home,home_method=match_team(home,names)
    mapped_away,away_method=match_team(away,names)
    if mapped_home is None or mapped_away is None or mapped_home==mapped_away:
        return {**result,'status':'name_unmatched','mapping':{'home':home_method,'away':away_method}}
    rows=frame[(frame.HomeTeam.eq(mapped_home)&frame.AwayTeam.eq(mapped_away))|
               (frame.HomeTeam.eq(mapped_away)&frame.AwayTeam.eq(mapped_home))].tail(10)
    matches=[{**_provenance(row),'home_team':str(row.HomeTeam),'away_team':str(row.AwayTeam),
        'home_goals':int(row.FTHG),'away_goals':int(row.FTAG)} for _,row in rows.iterrows()]
    return {**result,'status':'available' if matches else 'no_data','sample':len(matches),'matches':matches}


def collect_statistics(event,*,fetch_history,fetch_xg,optional_fallback=None,fetch_cup=None,now=None):
    """Load independent evidence sources without invoking A/B/C or The Odds API."""
    now=instant(now) if now is not None else datetime.now(UTC)
    kickoff=instant(event.get('kickoff_utc') or event.get('commence_time'))
    result={'schema_version':1,'event_id':event.get('event_id') or event.get('id'),
        'as_of':now.isoformat() if now else None,'cutoff_utc':None,'status':'no_data','sources':{},'teams':{},
        'standings':{'status':'unsupported','home':None,'away':None,'source':None,
            'reason':'ยังไม่มีแหล่งตารางคะแนนที่รองรับและตรวจสอบแล้ว ไม่ใช้คะแนน A แทนอันดับ'},
        'limitations':['สถิติเชิงพรรณนาจากเกมที่จบแล้ว ไม่ใช่ความน่าจะเป็นหรือ EV',
            'เกมที่มีเพียงวันที่จะไม่ใช้ในวัน UTC เดียวกับ cutoff และไม่สมมติเวลาแข่งขัน',
            'เวลาแสดงข้อมูล as_of ไม่ใช่เวลาที่ผู้ให้บริการอัปเดต; source receipt ที่ไม่มีหลักฐานคงเป็นไม่ทราบ']}
    if now is None or kickoff is None or any(not isinstance(event.get(key),str) or not event[key].strip()
            for key in ('home_team','away_team')) or _team_identity(event['home_team'])==_team_identity(event['away_team']):
        result['reason']='invalid_fixture_identity_or_time'
        return result
    cutoff=min(now,kickoff)
    result['cutoff_utc']=cutoff.isoformat()
    sport=event.get('sport_key') or event.get('league_key')
    home,away=event['home_team'],event['away_team']
    csv_frame,xg_frame=_empty_frame(),_empty_frame()
    csv_source=_source('Football-Data',status='unsupported',reason='csv_source_not_configured')
    xg_source=_source('Understat',status='unsupported',reason='xg_source_not_supported')
    api_source={'provider':'API-Football','status':'not_called','reason':'not_requested',
        'received_at':[],'sources':[],'receipt_status':'unknown','cache_status':'unknown'}
    if sport in CUPS:
        packs={}
        if fetch_cup is not None:
            try:
                loaded=fetch_cup(deepcopy(event))
                packs=loaded if isinstance(loaded,dict) else {}
            except Exception:
                result['sources']['domestic_context']={'provider':'domestic_context','status':'error',
                    'reason':'domestic_source_error'}
        valid_teams=0
        for side,name,venue in (('home',home,'home'),('away',away,'away')):
            pack=packs.get(side,{})
            pack=pack if isinstance(pack,dict) else {}
            context=pack.get('context',{})
            verified=(isinstance(context,dict) and context.get('status')=='matched'
                      and isinstance(context.get('sport_key'),str) and context['sport_key'] not in CUPS)
            scores=pack.get('score_history') if verified else _empty_frame()
            xgs=pack.get('xg_history') if verified else _empty_frame()
            scores,audit=_safe_clean(scores,cutoff,available_at=now)
            if isinstance(xgs,pd.DataFrame):
                xgs=eligible_xg_history(xgs,kickoff,now)
            xgs,xg_audit=_safe_clean(xgs,cutoff,xg=True,available_at=now)
            result['teams'][side]=_team(scores,xgs,name,venue,cutoff,scope='domestic_context')
            result['teams'][side]['context_status']='matched' if verified else 'unverified_or_unsupported'
            result['sources'][side+'_domestic_results']={**_source('Football-Data',scores),
                'scope':'domestic_context','sport_key':context.get('sport_key') if verified else None,'quality':audit}
            result['sources'][side+'_domestic_xg']={**_source('Understat',xgs),
                'scope':'domestic_context','sport_key':context.get('sport_key') if verified else None,'quality':xg_audit}
            valid_teams+=int(not scores.empty or not xgs.empty)
        result['h2h']={'status':'unsupported','sample':0,'matches':[],
            'reason':'ไม่มีแหล่งผลถ้วยโดยตรง ไม่ผสม H2H จากคนละลีก'}
        result['status']='partial' if valid_teams else 'no_data'
        result['sources'].update(football_data=csv_source,understat=xg_source,api_football=api_source)
        return result
    code=event.get('csv_code')
    if code:
        try:
            frame=fetch_history(code)
            csv_frame=frame if isinstance(frame,pd.DataFrame) else _empty_frame()
            csv_source=_source('Football-Data',csv_frame,status='invalid_response' if not isinstance(frame,pd.DataFrame) else None)
        except Exception:
            csv_source=_source('Football-Data',status='error',reason='results_source_error')
    if optional_fallback is not None:
        try:
            fixture={'id':result['event_id'],'sport_key':sport,'home_team':home,'away_team':away,
                     'commence_time':kickoff.isoformat()}
            candidate=optional_fallback(sport,csv_frame,[fixture],now)
            diagnostic=getattr(candidate,'attrs',{}).get('api_football_diagnostics',{})
            diagnostic_frame=pd.DataFrame()
            diagnostic_frame.attrs['history_diagnostics']=diagnostic if isinstance(diagnostic,dict) else {}
            api_source=_source('API-Football',diagnostic_frame)
            api_source['status']=_state(diagnostic.get('state') or diagnostic.get('status')) if isinstance(diagnostic,dict) else 'unknown'
            api_source['reason']=diagnostic.get('reason') if isinstance(diagnostic,dict) and diagnostic.get('reason') in {
                'csv_sufficient','csv_ready','csv_names_matched','unsupported_league','missing_key','not_configured',
                'disabled_by_owner','provider_names_unmatched','provider_metadata_unverified','no_fixtures',
                'invalid_key','invalid_season','names_unmatched','incomplete_pagination','conflicting_results',
                'plan_backoff','auth_backoff','quota_backoff','failure_backoff','request_in_progress',
                'backoff_store_unavailable','invalid_clock','invalid_backoff_policy','plan_unsupported',
                'http_error','provider_error','invalid_response','empty','download_error'} else None
            if isinstance(diagnostic,dict):
                api_source['provider_state']=_state(diagnostic.get('provider_state'))
                for key in ('request_attempted','used_for_form','backoff_active'):
                    if type(diagnostic.get(key)) is bool:
                        api_source[key]=diagnostic[key]
                http=diagnostic.get('http_status')
                if type(http) is int and 100<=http<=599:
                    api_source['http_status']=http
                retry=_clock(diagnostic.get('next_retry_at'))
                if retry is not None:
                    api_source['next_retry_at']=retry
            if isinstance(candidate,pd.DataFrame) and isinstance(diagnostic,dict) and diagnostic.get('used_for_form') is True:
                flags=('_provider_league_verified','_provider_identity_verified')
                if all(key in candidate for key in flags):
                    verified=candidate[candidate[flags[0]].eq(True)&candidate[flags[1]].eq(True)].copy()
                    if not verified.empty:
                        csv_frame=verified
                        csv_source=_source('API-Football',csv_frame)
                        csv_source['reason']='verified_optional_result_source'
                    else:
                        api_source.update(status='unverified',reason='provider_metadata_unverified')
                else:
                    api_source.update(status='unverified',reason='provider_metadata_unverified')
        except Exception:
            api_source=_source('API-Football',status='error',reason='optional_results_source_error')
    if sport in SUPPORTED_SPORTS:
        try:
            frame=fetch_xg(sport)
            xg_frame=frame if isinstance(frame,pd.DataFrame) else _empty_frame()
            xg_source=_source('Understat',xg_frame,status='invalid_response' if not isinstance(frame,pd.DataFrame) else None)
            xg_frame=eligible_xg_history(xg_frame,kickoff,now)
        except Exception:
            xg_source=_source('Understat',status='error',reason='xg_source_error')
    csv_frame,audit=_safe_clean(csv_frame,cutoff,available_at=now)
    xg_frame,xg_audit=_safe_clean(xg_frame,cutoff,xg=True,available_at=now)
    csv_source['quality']=audit
    xg_source['quality']=xg_audit
    result['sources'].update(football_data=csv_source,understat=xg_source,api_football=api_source)
    result['teams']={side:_team(csv_frame,xg_frame,name,venue,cutoff)
        for side,name,venue in (('home',home,'home'),('away',away,'away'))}
    result['h2h']=_h2h(csv_frame,home,away)
    form_available=all(result['teams'][side]['overall']['last5']['sample']>0 for side in ('home','away'))
    xg_available=all(result['teams'][side]['xg']['overall']['last5']['sample']>0 for side in ('home','away'))
    result['status']='complete' if form_available and xg_available else 'partial' if form_available or xg_available else 'no_data'
    result['completeness']={'form_both_teams':form_available,'xg_both_teams':xg_available,'standings':False,
        'meaning':'complete means supported form+xG present, not every requested field or a calibrated model'}
    return result
