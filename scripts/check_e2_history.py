"""Explicit live CSV audit; never imports the app or calls The Odds API."""
import json
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import pandas as pd
import requests


def audit():
    reports = []
    for season in ('2627', '2526', '2425', '2324'):
        url = f'https://www.football-data.co.uk/mmz4281/{season}/E2.csv'
        result = dict(season=season, url=url)
        try:
            response = requests.get(url, timeout=(5, 20))
            result.update(http_status=response.status_code, final_url=response.url, bytes=len(response.content))
            if response.status_code != 200:
                result['status'] = 'http_error'
            elif not response.content.strip():
                result['status'] = 'empty_file'
            else:
                frame = pd.read_csv(BytesIO(response.content))
                required = {'Date', 'HomeTeam', 'AwayTeam', 'FTHG', 'FTAG'}
                result.update(columns=frame.columns.tolist(), raw_rows=len(frame), missing_columns=sorted(required-set(frame.columns)))
                if result['missing_columns']:
                    result['status'] = 'missing_columns'
                else:
                    dates = pd.to_datetime(frame['Date'], dayfirst=True, errors='coerce')
                    hg, ag = pd.to_numeric(frame['FTHG'], errors='coerce'), pd.to_numeric(frame['FTAG'], errors='coerce')
                    valid = dates.notna() & hg.notna() & ag.notna() & frame['HomeTeam'].notna() & frame['AwayTeam'].notna()
                    before = valid & (dates < pd.Timestamp('2026-10-21'))
                    names = sorted(set(frame['HomeTeam'].dropna()) | set(frame['AwayTeam'].dropna()))
                    matches = [n for n in names if 'oxford' in n.lower() or 'wigan' in n.lower()]
                    result.update(status='ok', valid_rows=int(valid.sum()), invalid_dates=int(dates.isna().sum()),
                                  before_fixture_rows=int(before.sum()), matching_csv_names=matches,
                                  source_date_examples=frame['Date'].head(3).tolist(),
                                  team_samples={name: {'all_before': int((before & ((frame['HomeTeam']==name) | (frame['AwayTeam']==name))).sum()),
                                                      'home_before': int((before & (frame['HomeTeam']==name)).sum()),
                                                      'away_before': int((before & (frame['AwayTeam']==name)).sum())} for name in matches})
        except Exception as exc:
            result.update(status='download_or_parse_error', error=type(exc).__name__)
        reports.append(result)
    return dict(checked_at=datetime.now(timezone.utc).isoformat(), fixture_date='2026-10-21',
                api_names_from_user=['Oxford United', 'Wigan Athletic'],
                note='No Odds API request; API names are the supplied fixture, not a newly fetched API response.', seasons=reports)


if __name__ == '__main__':
    report = audit()
    folder = Path('test-artifacts')
    folder.mkdir(exist_ok=True)
    (folder/'e2-live-history-audit.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))
