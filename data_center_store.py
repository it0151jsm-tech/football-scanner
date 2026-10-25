"""Append-only operational football data, independent of model success.

This stores an allowlisted representation of the actual odds response, not its
original wire bytes or an observed-model dataset. It never writes picks/slips,
creates forecasts, fetches providers, or upgrades First Observed to Opening.
"""
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import hashlib
import json
import math

from credential_redaction import redact
from quote_provenance import quote_update
from readiness import instant, quote_freshness
from scan_diagnostics import filter_scan_fixtures

UTC=timezone.utc
TH_TZ=timezone(timedelta(hours=7))
MARKETS={'spreads':'Asian Handicap','totals':'Over/Under'}


def _json(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def _text(value):
    if not isinstance(value,str) or not value.strip():
        return None
    # Do not silently turn a credential-bearing name into a different club.
    return value if redact(value)==value else None


def _stamp(value):
    if instant(value) is None:
        return None
    return value.isoformat() if isinstance(value,datetime) else value


def _scalar(value):
    if value is None or isinstance(value,bool):
        return value
    if isinstance(value,str):
        return _text(value)
    if isinstance(value,(int,float)):
        try:
            return value if math.isfinite(value) else None
        except OverflowError:
            return None
    return None


def _number(value):
    try:
        return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)
    except OverflowError:
        return False


def _payload(matches,receipt):
    """Keep actual identifiers/quotes and clock representations, no API secrets."""
    if not isinstance(matches,list):
        raise ValueError('data_center_response_must_be_a_list')
    output=[]
    for match in matches:
        if not isinstance(match,dict):
            output.append(None)
            continue
        row={key:_text(match.get(key)) for key in ('id','sport_key','home_team','away_team','commence_time')}
        row['_received_at']=_stamp(match.get('_received_at')) or receipt
        if type(match.get('_observation_raw_id')) is int and match['_observation_raw_id']>0:
            row['_observation_raw_id']=match['_observation_raw_id']
        books=[]
        for book in match.get('bookmakers',[]) if isinstance(match.get('bookmakers'),list) else []:
            if not isinstance(book,dict):
                continue
            clean={key:_text(book.get(key)) for key in ('key','title')}
            if 'last_update' in book:
                clean['last_update']=_scalar(book['last_update'])
            clean['markets']=[]
            for market in book.get('markets',[]) if isinstance(book.get('markets'),list) else []:
                if not isinstance(market,dict) or market.get('key') not in MARKETS:
                    continue
                item={'key':market['key'],'outcomes':[]}
                if 'last_update' in market:
                    item['last_update']=_scalar(market['last_update'])
                for outcome in market.get('outcomes',[]) if isinstance(market.get('outcomes'),list) else []:
                    if isinstance(outcome,dict):
                        item['outcomes'].append({key:_scalar(outcome.get(key)) for key in ('name','point','price')})
                clean['markets'].append(item)
            books.append(clean)
        row['bookmakers']=books
        output.append(row)
    return output


def _safe_report(report):
    report=report if isinstance(report,dict) else {}
    result={key:report.get(key) for key in ('hours_limit','api_returned','passed_filter','excluded_count')
            if type(report.get(key)) is int and report[key]>=0}
    for key in ('sport_key','start_utc','end_utc','api_received_at','start_th','end_th'):
        if _text(report.get(key)) is not None:
            result[key]=report[key]
    reasons=report.get('excluded_by_reason')
    result['excluded_by_reason']={key:value for key,value in reasons.items()
        if _text(key) is not None and type(value) is int and value>=0} if isinstance(reasons,dict) else {}
    return result


def initialize(con):
    con.execute('''CREATE TABLE IF NOT EXISTS dc_batches (
        id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, sport_key TEXT NOT NULL,
        league TEXT NOT NULL, regions TEXT NOT NULL, received_at TEXT,
        stored_at TEXT NOT NULL, payload_json TEXT NOT NULL, report_json TEXT NOT NULL,
        remaining_json TEXT, used_json TEXT, UNIQUE(run_id,sport_key))''')
    con.execute('''CREATE TABLE IF NOT EXISTS dc_enrichments (
        id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, event_id TEXT NOT NULL,
        stored_at TEXT NOT NULL, payload_json TEXT NOT NULL)''')
    con.execute('CREATE INDEX IF NOT EXISTS idx_dc_enrichment_fixture ON dc_enrichments(run_id,event_id,id)')
    for table in ('dc_batches','dc_enrichments'):
        for operation in ('UPDATE','DELETE'):
            con.execute(f'''CREATE TRIGGER IF NOT EXISTS protect_{table}_{operation.lower()}
                BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'data center history is append only'); END''')


def _run(con,run_id):
    row=con.execute('SELECT * FROM scan_runs WHERE id=?',(run_id,)).fetchone()
    if row is None:
        raise ValueError('data_center_scan_run_missing')
    return row


def _identity(match,sport):
    kickoff=instant(match.get('commence_time'))
    return (match.get('id'),sport,match.get('home_team'),match.get('away_team'),kickoff.isoformat() if kickoff else None)


def _quotes(match,sport):
    quotes=[]
    for book in match.get('bookmakers',[]):
        for market in book.get('markets',[]):
            for outcome in market.get('outcomes',[]):
                line,odds=outcome.get('point'),outcome.get('price')
                if (not _number(line) or not _number(odds) or odds<=1 or not math.isfinite(line*4)
                        or abs(line*4-round(line*4))>1e-9 or (market['key']=='totals' and line<0)):
                    continue
                name=outcome.get('name')
                if market['key']=='spreads':
                    side='home' if name==match['home_team'] else 'away' if name==match['away_team'] else None
                else:
                    side={'over':'over','under':'under'}.get(name.casefold()) if isinstance(name,str) else None
                if side is None:
                    continue
                quote={'market':MARKETS[market['key']],'side':side,'point':line,'odds':odds,
                    'bookmaker':book.get('title'),'bookmaker_key':book.get('key'),
                    'received_at':match.get('_received_at'),**quote_update(book,market)}
                digest={'fixture':_identity(match,sport),**quote}
                quote['quote_id']=hashlib.sha256(_json(digest).encode()).hexdigest()
                quote.update(first_observed=None,first_observed_status='not_yet_resolved',
                    first_observed_scope='data_center_records_only',
                    opening_odds=None,opening_status='unavailable_no_verified_opening_source')
                quotes.append(quote)
    # Exact duplicates are displayed once, while the persisted payload retains all.
    return list({row['quote_id']:row for row in quotes}.values())


def _cards(matches,sport,label,run_id,now,hours):
    accepted,_=filter_scan_fixtures(matches,sport,now,hours)
    cards=[]
    for match in accepted:
        if match['home_team']==match['away_team']:
            continue
        kickoff=instant(match['commence_time'])
        quotes=_quotes(match,sport)
        for quote in quotes:
            fresh,reason=quote_freshness({'event_id':match['id'],**quote},now)
            quote['freshness_status']='recent_observed_snapshot' if fresh else 'unverified_or_stale'
            quote['freshness_reason']=reason
            receipt=instant(quote['received_at'])
            quote['receipt_age_seconds']=(now-receipt).total_seconds() if receipt is not None else None
        missing_time=instant(match.get('_received_at')) is None or any(instant(q['source_updated_at']) is None for q in quotes)
        reasons=[]
        if not quotes:reasons.append('provider_has_no_usable_ah_ou_prices')
        if missing_time:reasons.append('receipt_or_provider_update_unverified')
        if any(not q.get('bookmaker') or not q.get('bookmaker_key') for q in quotes):
            reasons.append('bookmaker_identity_incomplete')
        cards.append({'run_id':run_id,'event_id':match['id'],'sport_key':sport,'league':label,
            'home_team':match['home_team'],'away_team':match['away_team'],
            'match':match['home_team']+' vs '+match['away_team'],
            'kickoff_utc':kickoff.isoformat(),'kickoff_th':kickoff.astimezone(TH_TZ).strftime('%d/%m/%Y %H:%M'),
            'received_at':match.get('_received_at'),'provider':'The Odds API','quotes':quotes,
            'data_status':'no_prices' if not quotes else 'missing_timestamp' if missing_time else 'available',
            'data_quality_reasons':reasons,'statistics':None})
    return cards


def save_batch(con,run_id,sport_key,league_name,matches,received_at,report,remaining=None,used=None,region='uk'):
    """Commit the operational response before any optional enrichment/model work.

    Caller owns the transaction. Cached observations retain their source receipt;
    stored_at is only this database write time and never substitutes for receipt.
    """
    run=_run(con,run_id)
    if (not _text(sport_key) or not _text(league_name) or region!=run['regions']
            or sport_key not in json.loads(run['selected_json'])):
        raise ValueError('data_center_batch_scope_mismatch')
    receipt=_stamp(received_at)
    payload=_payload(matches,receipt)
    values=(_json(payload),_json(_safe_report(report)),_json(_scalar(remaining)),_json(_scalar(used)))
    previous=con.execute('SELECT * FROM dc_batches WHERE run_id=? AND sport_key=?',(run_id,sport_key)).fetchone()
    if previous is None:
        con.execute('''INSERT INTO dc_batches(run_id,sport_key,league,regions,received_at,stored_at,
            payload_json,report_json,remaining_json,used_json) VALUES(?,?,?,?,?,?,?,?,?,?)''',
            (run_id,sport_key,league_name,region,receipt,datetime.now(UTC).isoformat(),*values))
    elif (previous['league']!=league_name or previous['received_at']!=receipt or
            tuple(previous[k] for k in ('payload_json','report_json','remaining_json','used_json'))!=values):
        raise ValueError('data_center_batch_already_recorded')
    now=instant(report.get('start_utc')) if isinstance(report,dict) else None
    now=now or instant(run['started_at'])
    cards=_cards(payload,sport_key,league_name,run_id,now,run['hours_limit'])
    rows,snapshots=[],[]
    for card in cards:
        header={k:card[k] for k in ('event_id','kickoff_utc','kickoff_th','league','home_team','away_team','match','received_at')}
        if not card['quotes']:
            rows.append({**header,'market':'ยังไม่มีราคา','side':'none','point':None,'odds':None,
                'bookmaker':None,'bookmaker_key':None,'selection':'ไม่มีราคาตลาด','ev_pct':None})
        for quote in card['quotes']:
            row={**header,**{k:v for k,v in quote.items() if k not in ('first_observed','first_observed_status')},
                'ev_pct':None,'analysis_a_json':None,'analysis_b_json':None}
            rows.append(row)
            snapshots.append({**row,'match_name':card['match']})
    return {'fixtures':len(cards),'quotes':len(snapshots),'cards':cards,'rows':rows,'snapshots':snapshots}


def load_batches(con,run_id):
    _run(con,run_id)
    output={}
    for row in con.execute('SELECT * FROM dc_batches WHERE run_id=? ORDER BY id',(run_id,)):
        output[row['sport_key']]={'matches':json.loads(row['payload_json']),'received_at':row['received_at'],
            'remaining':json.loads(row['remaining_json']),'used':json.loads(row['used_json']),
            'league':row['league'],'report':json.loads(row['report_json']),'stored_at':row['stored_at'],
            'storage_kind':'allowlisted_operational_response_not_original_wire_bytes'}
    return output


def save_enrichment(con,run_id,event_id,payload):
    """Append source statistics/status; never requires an A/B/C or EV result."""
    batches=load_batches(con,run_id)
    identities={_identity(match,key) for key,batch in batches.items() for match in batch['matches']
        if isinstance(match,dict) and match.get('id')==event_id}
    if len(identities)!=1 or not isinstance(payload,dict):
        raise ValueError('data_center_enrichment_fixture_unverified')
    identity=next(iter(identities))
    expected=dict(zip(('event_id','sport_key','home_team','away_team','kickoff_utc'),identity))
    for key,value in expected.items():
        if key in payload and (instant(payload[key])!=instant(value) if key=='kickoff_utc' else payload[key]!=value):
            raise ValueError('data_center_enrichment_fixture_mismatch')
    # A recursive JSON walk excludes accidental API credentials in enrichment.
    def secrets(value):
        if isinstance(value,dict):
            return any(any(term in str(key).casefold().replace('-','').replace('_','')
                for term in ('apikey','authorization','password','accesstoken','secret')) or secrets(item)
                for key,item in value.items())
        if isinstance(value,list):return any(secrets(item) for item in value)
        return isinstance(value,str) and redact(value)!=value
    if secrets(payload):
        raise ValueError('data_center_enrichment_credentials_rejected')
    con.execute('INSERT INTO dc_enrichments(run_id,event_id,stored_at,payload_json) VALUES(?,?,?,?)',
        (run_id,event_id,datetime.now(UTC).isoformat(),_json(payload)))


def _first_prices(con,card,history_cache=None):
    history_cache={} if history_cache is None else history_cache
    target=(card['event_id'],card['sport_key'],card['home_team'],card['away_team'],card['kickoff_utc'])
    if card['sport_key'] not in history_cache:
        histories=defaultdict(list)
        for batch in con.execute('SELECT * FROM dc_batches WHERE sport_key=? ORDER BY id',(card['sport_key'],)):
            for match in json.loads(batch['payload_json']):
                if isinstance(match,dict):
                    for quote in _quotes(match,card['sport_key']):
                        histories[_identity(match,card['sport_key'])].append((batch['id'],quote))
        history_cache[card['sport_key']]=histories
    history=history_cache[card['sport_key']].get(target,[])
    for current in card['quotes']:
        keys=('market','side','bookmaker','bookmaker_key')
        matching=[(index,q) for index,q in history if all(q[k]==current[k] for k in keys)]
        if not matching:
            current['first_observed_status']='no_matching_observation'
            continue
        first_id=min(index for index,_ in matching)
        first=[q for index,q in matching if index==first_id]
        same=[q for q in first if q['point']==current['point']]
        choices=same or first
        values={(q['point'],q['odds'],q['received_at'],q['source_updated_at']) for q in choices}
        if len(values)!=1:
            current['first_observed_status']='ambiguous_first_snapshot'
            continue
        selected=choices[0]
        if instant(current.get('received_at')) is None:
            current['first_observed_status']='current_receipt_unverified'
            continue
        if instant(selected.get('received_at')) is None:
            current['first_observed_status']='first_receipt_unverified'
            continue
        if instant(selected['received_at'])>instant(current['received_at']):
            current['first_observed_status']='first_observation_after_current_receipt'
            continue
        current['first_observed']={k:selected[k] for k in ('odds','point','received_at','source_updated_at','source_update_scope')}
        current['first_observed'].update(label='First Observed Price',line_changed=selected['point']!=current['point'])
        current['first_observed_status']='observed_not_opening'


def load_latest(con,selected_keys,regions,hours_limit,now=None):
    """Latest run only, even while enrichment fails or remains pending."""
    run=con.execute('SELECT * FROM scan_runs ORDER BY rowid DESC LIMIT 1').fetchone()
    if run is None or run['regions']!=regions or run['hours_limit']!=hours_limit:
        return []
    now=now or datetime.now(UTC)
    cards=[]
    for key,batch in load_batches(con,run['id']).items():
        if key in selected_keys:
            cards.extend(_cards(batch['matches'],key,batch['league'],run['id'],now,hours_limit))
    # A reused ID that names different fixtures must never merge their statistics.
    by_id=defaultdict(list)
    for card in cards:by_id[card['event_id']].append(card)
    result=[]
    history_cache={}
    for group in by_id.values():
        identities={(c['sport_key'],c['home_team'],c['away_team'],c['kickoff_utc']) for c in group}
        if len(identities)!=1:
            continue
        card=group[0]
        _first_prices(con,card,history_cache)
        enrichment=con.execute('''SELECT stored_at,payload_json FROM dc_enrichments
            WHERE run_id=? AND event_id=? ORDER BY id DESC LIMIT 1''',(run['id'],card['event_id'])).fetchone()
        if enrichment is not None:
            card['statistics']=json.loads(enrichment['payload_json'])
            card['statistics_stored_at']=enrichment['stored_at']
        result.append(card)
    return sorted(result,key=lambda c:(c['kickoff_utc'],c['sport_key'],c['event_id']))
