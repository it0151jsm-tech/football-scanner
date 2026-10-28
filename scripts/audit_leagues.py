"""Explicit source audit for every configured league. Zero Odds API requests."""
import ast
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from history_data import parse_csv, match_team


def config():
    tree = ast.parse(Path('app.py').read_text())
    return {t.id: ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
            for t in node.targets if isinstance(t, ast.Name) and t.id in ('LEAGUES','UNDERSTAT_LEAGUES')}


def csv_source(code, season):
    if not code:
        return {'status':'unsupported','teams':[]}
    url = f'https://www.football-data.co.uk/mmz4281/{season}/{code}.csv'
    try:
        r = requests.get(url, timeout=(4, 10))
        if r.status_code != 200:
            return dict(url=url,http=r.status_code,status='load_error',teams=[])
        frame, info = parse_csv(r.content)
        teams = sorted(set(frame.HomeTeam)|set(frame.AwayTeam)) if not frame.empty else []
        return dict(url=url,http=r.status_code,status='ok' if teams else info['status'],teams=teams,
                    rows=len(frame),last_date=str(frame.Date_dt.max().date()) if teams else None)
    except Exception as exc:
        return dict(url=url,status='load_error',error=type(exc).__name__,teams=[])


def understat_source(code):
    if not code:
        return {'status':'unsupported','teams':[]}
    attempts = []
    for season in (2026,2025):
        url = f'https://understat.com/getLeagueData/{code}/{season}'
        try:
            r = requests.get(url,timeout=(4,10),headers={'User-Agent':'Mozilla/5.0','X-Requested-With':'XMLHttpRequest','Referer':f'https://understat.com/league/{code}/{season}'})
            attempts.append({'url':url,'http':r.status_code})
            if r.status_code != 200:
                continue
            payload = r.json()
            rows = payload.get('dates',[]) if isinstance(payload,dict) else []
            teams = sorted({item.get(side,{}).get('title') for item in rows for side in ('h','a') if item.get(side,{}).get('title')})
            if teams:
                dates = [item.get('datetime') for item in rows if item.get('isResult') and item.get('datetime')]
                return dict(status='ok',url=url,season=season,teams=teams,attempts=attempts,last_date=max(dates) if dates else None)
        except Exception as exc:
            attempts.append({'url':url,'error':type(exc).__name__})
    return dict(status='load_error',teams=[],attempts=attempts)


def audit_one(item, understat):
    key,(label,code) = item
    current,previous = csv_source(code,'2627'),csv_source(code,'2526')
    xg = understat_source(understat.get(key))
    checked = []
    if current['teams'] and xg['teams']:
        for name in xg['teams']:
            mapped,method = match_team(name,current['teams'])
            checked.append(dict(understat_name=name,csv_name=mapped,status='matched' if mapped else 'unmatched',method=method))
    return dict(key=key,league=label,csv_code=code,csv_current=current,csv_previous=previous,understat=xg,
                current_source_teams=len(current['teams']),cross_source_checked=len(checked),
                cross_source_matched=sum(r['status']=='matched' for r in checked),cross_source_results=checked,
                current_new_names=sorted(set(current['teams'])-set(previous['teams'])) if previous['teams'] else [],
                entrant_note='Names new to this league CSV; promotion/relegation identity is not independently verified.',
                api_teams_checked=0,api_scope='No production SQLite/API payload available to this source audit; no Odds API requests.')


if __name__ == '__main__':
    settings = config()
    with ThreadPoolExecutor(max_workers=6) as pool:
        reports = list(pool.map(lambda item:audit_one(item,settings['UNDERSTAT_LEAGUES']),settings['LEAGUES'].items()))
    context = [dict(key=key,context_only=True,understat=understat_source(code))
               for key,code in settings['UNDERSTAT_LEAGUES'].items() if key not in settings['LEAGUES']]
    output = dict(context_only_sources=context,checked_at=datetime.now(timezone.utc).isoformat(),configured_leagues=len(reports),
                  scope='Actual configured source rosters; exact/verified aliases only. Not a complete Odds API club census.',leagues=reports)
    Path('test-artifacts').mkdir(exist_ok=True)
    Path('test-artifacts/league-source-audit.json').write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in output.items() if k!='leagues'},ensure_ascii=False))
    for extra in context:
        print(json.dumps(extra,ensure_ascii=False))
    for r in reports:
        print(json.dumps(dict(league=r['key'],csv=r['csv_current']['status'],teams=r['current_source_teams'],
                              understat=r['understat']['status'],checked=r['cross_source_checked'],matched=r['cross_source_matched'],
                              new_names=r['current_new_names'],unresolved=[x for x in r['cross_source_results'] if x['status']!='matched']),ensure_ascii=False))
