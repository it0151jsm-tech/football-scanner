"""Continue independent leagues after local errors; halt only for safety.

No HTTP/Streamlit/SQLite calls occur here. The application supplies the existing
scan callable and the evidence from the current response. Every selected league
gets a record, including those never requested after a global safety stop.
"""
from collections import Counter
from datetime import datetime, timezone

from odds_budget import LeagueScanError, ScanStopped


def _count(value):
    return value if type(value) is int and value >= 0 else None


def _header_count(value):
    if isinstance(value,bool):
        return None
    try:
        result=int(value)
        return result if result>=0 and str(result)==str(value).strip() else None
    except (TypeError,ValueError,OverflowError):
        return None


def _report(report_for,key):
    if report_for is None:
        return {}
    try:
        result=report_for(key)
        return result if isinstance(result,dict) else {}
    except Exception:
        # Reporting failure must not lose saved rows or reveal exception URLs.
        return {}


def _events(rows,market=None):
    return {row['event_id'] for row in rows if (market is None or row.get('market')==market)
            and isinstance(row.get('event_id'),str) and row['event_id'].strip()}


def run_selected_leagues(selected,league_config,scan_one,*,report_for=None,on_progress=None):
    """Run one supplied scanner per selected key, preserving all returned rows.

    ``scan_one(key, league_name, csv_code)`` returns the existing four-tuple
    ``(rows, snapshots, remaining, used)``. ``report_for(key)`` optionally gives
    current-response api_returned/passed_filter/excluded_by_reason evidence.
    No count is inferred from quote count as a substitute for API fixture count.
    Returned rows/snapshots remain raw for the application's append-only save.
    """
    selected=list(selected)
    rows,snapshots,records=[],[],[]
    seen=set()
    stopped=False
    stop_reason=None
    started=datetime.now(timezone.utc).isoformat()
    for index,key in enumerate(selected):
        configured=league_config.get(key) if isinstance(key,str) else None
        entry={'sport_key':key if isinstance(key,str) else None,
            'league':configured[0] if isinstance(configured,(list,tuple)) and configured else None,
            'status':None,'reason':None,'api_returned':None,'passed_filter':None,
            'excluded_count':None,'excluded_by_reason':{},'saved_fixture_count':0,
            'market_rows':0,'snapshot_rows':0,'ah_fixtures':0,'ou_fixtures':0,
            'no_price_fixtures':0,'provider_remaining':None,'provider_used':None,
            'api_received_at':None}
        if stopped:
            entry.update(status='skipped_safety_stop',reason=stop_reason)
        elif not isinstance(configured,(list,tuple)) or len(configured)!=2:
            entry.update(status='invalid_league',reason='sport_key_not_configured')
        elif key in seen:
            entry.update(status='skipped_duplicate',reason='duplicate_selected_sport_key')
        else:
            seen.add(key)
            try:
                output=scan_one(key,*configured)
                if not isinstance(output,(tuple,list)) or len(output)!=4:
                    raise ValueError('invalid_scan_result')
                league_rows,league_snapshots,remaining,used=output
                if (not isinstance(league_rows,list) or not isinstance(league_snapshots,list)
                        or any(not isinstance(row,dict) for row in league_rows+league_snapshots)):
                    raise ValueError('invalid_scan_rows')
                rows.extend(league_rows)
                snapshots.extend(league_snapshots)
                evidence=_report(report_for,key)
                returned,passed,excluded=(_count(evidence.get(name)) for name in
                    ('api_returned','passed_filter','excluded_count'))
                status=('success' if league_rows else 'empty_api' if returned==0 else
                    'filtered_out' if returned is not None and returned>0 and passed==0 else
                    'fixtures_without_rows' if passed is not None and passed>0 else 'success_no_rows')
                entry.update(status=status,api_returned=returned,passed_filter=passed,
                    excluded_count=excluded,
                    excluded_by_reason={str(name):count for name,value in evidence.get('excluded_by_reason',{}).items()
                        if (count:=_count(value)) is not None} if isinstance(evidence.get('excluded_by_reason',{}),dict) else {},
                    saved_fixture_count=len(_events(league_rows)),
                    market_rows=len(league_rows),snapshot_rows=len(league_snapshots),
                    ah_fixtures=len(_events(league_rows,'Asian Handicap')),
                    ou_fixtures=len(_events(league_rows,'Over/Under')),
                    no_price_fixtures=len(_events(league_rows,'ยังไม่มีราคา')),
                    provider_remaining=_header_count(remaining),provider_used=_header_count(used),
                    api_received_at=evidence.get('api_received_at'))
            except LeagueScanError as exc:
                entry.update(status='cooldown' if exc.reason=='cooldown' else 'league_error',
                    reason=exc.reason,http_status=exc.status)
            except ScanStopped as exc:
                stopped=True
                stop_reason=exc.reason
                entry.update(status='safety_stop',reason=stop_reason,http_status=exc.status)
            except Exception:
                # No provider/request exception body or API key in reports.
                entry.update(status='league_error',reason='unexpected_scan_error')
        records.append(entry)
        if on_progress is not None:
            try:
                on_progress(index+1,len(selected),dict(entry))
            except ScanStopped as exc:
                # Persistence callbacks can explicitly request a safety stop
                # rather than spending more credits after a storage failure.
                stopped=True
                stop_reason=exc.reason
                entry['progress_callback_error']=True
                entry['progress_stop_reason']=stop_reason
            except Exception:
                # A presentation callback cannot turn a local problem into a
                # silent loss of the remaining selected leagues.
                entry['progress_callback_error']=True
    successful={'success','empty_api','filtered_out','fixtures_without_rows','success_no_rows'}
    counts=Counter(entry['status'] for entry in records)
    known_api=[entry['api_returned'] for entry in records if entry['api_returned'] is not None]
    known_passed=[entry['passed_filter'] for entry in records if entry['passed_filter'] is not None]
    return {'rows':rows,'snapshots':snapshots,'leagues':records,'started_at':started,
        'finished_at':datetime.now(timezone.utc).isoformat(),'stopped':stopped,'stop_reason':stop_reason,
        'counts':dict(selected_leagues=len(selected),successful_leagues=sum(entry['status'] in successful for entry in records),
            league_errors=counts['league_error'],cooldown_leagues=counts['cooldown'],
            safety_stops=counts['safety_stop'],skipped_safety_stop=counts['skipped_safety_stop'],
            invalid_leagues=counts['invalid_league'],duplicate_selections=counts['skipped_duplicate'],
            api_returned=sum(known_api),api_returned_known_leagues=len(known_api),
            passed_filter=sum(known_passed),passed_filter_known_leagues=len(known_passed),
            response_counts_complete=len(known_api)==len(records) and len(known_passed)==len(records),
            saved_fixture_count=len(_events(rows)),
            market_rows=len(rows),snapshot_rows=len(snapshots))}


def scan_summary_text(report):
    """A compact, explicit caption for the existing scan status area."""
    counts=report['counts']
    known=counts['response_counts_complete']
    qualifier='' if known else ' (เฉพาะลีกที่มีหลักฐานจำนวนตอบกลับ)'
    return (f"เลือก {counts['selected_leagues']} ลีก | ตรวจสำเร็จ {counts['successful_leagues']} ลีก | "
        f"API ส่งมา {counts['api_returned']} คู่{qualifier} | ผ่านตัวกรอง {counts['passed_filter']} คู่ | "
        f"แสดง {counts['saved_fixture_count']} คู่ | ผิดพลาด {counts['league_errors']} ลีก | "
        f"ข้ามช่วงพัก {counts['cooldown_leagues']} ลีก | หยุดเพื่อความปลอดภัย "
        f"{counts['safety_stops']} ลีก; ยังไม่ได้เรียก {counts['skipped_safety_stop']} ลีก")
