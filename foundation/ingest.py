"""Explicit local-file importer. No network client or automatic downloads."""
import hashlib
import math
import re
from pathlib import Path

from .store import LEAGUES, connect, digest, encoded, timestamp, utc_now


class QualityError(ValueError):
    pass


def text(value,field):
    if not isinstance(value,str) or not value.strip() or len(value)>300:
        raise QualityError('invalid '+field)
    return value.strip()


def number(value,field,integer=False):
    if type(value) not in (int,float) or not math.isfinite(value) or value<0 or (integer and int(value)!=value):
        raise QualityError('invalid '+field)
    return int(value) if integer else float(value)


def require_keys(row,allowed):
    if not isinstance(row,dict) or set(row)-set(allowed):
        raise QualityError('unknown fields or forbidden availability/observation override')


def season_valid(season,kickoff):
    if not isinstance(season,str) or not re.fullmatch(r'20\d{2}-\d{2}',season):
        return False
    year=int(season[:4])
    return int(season[-2:])==(year+1)%100 and (year,7,1)<=(kickoff.year,kickoff.month,kickoff.day)<(year+1,7,1)


def import_file(path,db_path):
    import json
    raw=Path(path).read_bytes()
    if len(raw)>10*1024*1024:
        raise ValueError('local import limit is 10 MiB; split the dataset into reviewed batches')
    return import_dataset(json.loads(raw),db_path)


def import_dataset(dataset,db_path):
    require_keys(dataset,('manifest','teams','matches','odds','quarantine'))
    manifest=dataset['manifest']
    require_keys(manifest,('source_id','name','dataset_kind','license','evidence','rights_status','allowed_uses','reviewed_by','reviewed_at','evidence_sha256'))
    if manifest.get('rights_status')!='verified' or not {'storage','research','model_training'}<=set(manifest.get('allowed_uses',[])):
        raise ValueError('rights not approved for this import; no source payload stored')
    for key in ('source_id','name','license','evidence','reviewed_by'):
        text(manifest.get(key),key)
    evidence=Path(manifest['evidence']).read_bytes()
    if len(evidence)>1024*1024 or hashlib.sha256(evidence).hexdigest()!=manifest.get('evidence_sha256'):
        raise ValueError('rights evidence missing or checksum mismatch')
    evidence_text=evidence.decode('utf-8')
    now=utc_now()
    if timestamp(manifest.get('reviewed_at'))>timestamp(now):
        raise ValueError('permission review cannot be in the future')
    if manifest.get('dataset_kind') not in ('test_sample','research'):
        raise ValueError('explicit test_sample or research provenance required')
    for key in ('teams','matches','odds','quarantine'):
        if not isinstance(dataset.get(key,[]),list):
            raise ValueError(key+' must be a list')
    # Adapter rejections retain raw records without inventing mandatory match fields.
    # Validate their envelope before registering a source or storing any payload.
    for row in dataset.get('quarantine',[]):
        require_keys(row,('entity','source_row_id','reason','payload'))
        if row.get('entity')!='match' or not isinstance(row.get('payload'),dict):
            raise ValueError('invalid adapter quarantine envelope')
        for field in ('source_row_id','reason'):
            text(row.get(field),field)
    source=manifest['source_id']; batch_hash=digest(dataset)
    accepted=quarantined=0
    with connect(db_path) as con:
        existing=con.execute('SELECT * FROM sources WHERE id=?',(source,)).fetchone()
        if existing and existing['manifest_sha256']!=digest(manifest):
            raise ValueError('source manifest is immutable; register a new source version')
        con.execute('INSERT OR IGNORE INTO sources VALUES (?,?,?,?,?,?)',
                    (source,encoded(manifest),digest(manifest),manifest['dataset_kind'],'verified',now))
        con.execute('INSERT OR IGNORE INTO source_permissions VALUES (?,?,?,?,?)',
                    (source,manifest['evidence_sha256'],evidence_text,manifest['reviewed_by'],manifest['reviewed_at']))
        duplicate=con.execute('SELECT * FROM ingestion_runs WHERE source_id=? AND sha256=?',(source,batch_hash)).fetchone()
        if duplicate:
            return dict(run_id=duplicate['id'],duplicate_batch=True,accepted=duplicate['accepted'],quarantined=duplicate['quarantined'])
        run=con.execute('INSERT INTO ingestion_runs(source_id,sha256,ingested_at,raw_json) VALUES (?,?,?,?)',
                        (source,batch_hash,now,encoded(dataset))).lastrowid
        blocked_teams=set();blocked_matches=set()
        def rejected(entity,row,exc):
            nonlocal quarantined
            quarantined+=1
            if isinstance(row,dict) and entity=='team':
                blocked_teams.add(str(row.get('source_team_id')))
            if isinstance(row,dict) and entity=='match':
                blocked_matches.add(str(row.get('source_match_id')))
            rowid=row.get('source_'+entity+'_id') if isinstance(row,dict) else None
            con.execute('INSERT INTO quarantine(run_id,entity,source_row_id,reason,payload_json,observed_at) VALUES (?,?,?,?,?,?)',
                        (run,entity,rowid,str(exc),encoded(row),now))
        # Savepoints ensure a bad optional field never leaves partially accepted rows.
        def process(entity,rows,callback):
            nonlocal accepted
            for row in rows:
                con.execute('SAVEPOINT item')
                try:
                    changed=callback(row)
                    con.execute('RELEASE item')
                    accepted+=int(changed)
                except (ValueError,TypeError,KeyError,OverflowError) as exc:
                    con.execute('ROLLBACK TO item');con.execute('RELEASE item')
                    rejected(entity,row,exc)
        def team(row):
            require_keys(row,('source_team_id','name','country','category','memberships'))
            values=[text(row.get(k),k) for k in ('source_team_id','name','country','category')]
            sid,name,country,category=values
            existing=con.execute('SELECT * FROM teams WHERE source_id=? AND source_team_id=?',(source,sid)).fetchone()
            if existing:
                if tuple(existing[k] for k in ('name','country','category'))!=(name,country,category):
                    raise QualityError('team identity conflict; explicit mapping review required')
            ambiguous=con.execute('SELECT name FROM teams WHERE source_id=? AND country=? AND category=?',(source,country,category))
            if not existing and any(r['name'].casefold()==name.casefold() for r in ambiguous):
                raise QualityError('ambiguous team name; no automatic merge')
            if existing:
                team_id=existing['id']
            else:
                team_id=con.execute('INSERT INTO teams(source_id,source_team_id,name,country,category,observed_at,quality) VALUES (?,?,?,?,?,?,?)',
                                    (source,sid,name,country,category,now,'accepted')).lastrowid
            memberships=row.get('memberships')
            if not isinstance(memberships,list) or not memberships:
                raise QualityError('explicit competition/season membership required')
            changed=not bool(existing)
            for member in memberships:
                require_keys(member,('league','season'))
                season=member.get('season')
                if member.get('league') not in LEAGUES or not isinstance(season,str) or not re.fullmatch(r'20\d{2}-\d{2}',season) or int(season[-2:])!=(int(season[:4])+1)%100:
                    raise QualityError('invalid competition/season membership')
                cur=con.execute('INSERT OR IGNORE INTO team_season_memberships VALUES (?,?,?,?,?)',
                                (team_id,member['league'],season,run,now))
                changed=changed or cur.rowcount>0
            return changed
        def match(row):
            require_keys(row,('source_match_id','league','season','kickoff_utc','home_id','away_id','home_name','away_name',
                              'source_updated_at','result','xg','stats'))
            sid=text(row.get('source_match_id'),'match id')
            if row.get('league') not in LEAGUES:
                raise QualityError('league outside five-league experiment')
            kickoff=timestamp(row['kickoff_utc'])
            if not season_valid(row.get('season'),kickoff):
                raise QualityError('season/kickoff mismatch')
            identities=[]
            for side in ('home','away'):
                team_id=text(row.get(side+'_id'),side+' team ID')
                if team_id in blocked_teams:
                    raise QualityError('team identity failed in this batch')
                found=con.execute('SELECT * FROM teams WHERE source_id=? AND source_team_id=?',(source,team_id)).fetchone()
                if not found or (row.get(side+'_name') is not None and row[side+'_name']!=found['name']):
                    raise QualityError('unmatched '+side+' team; name similarity is never sufficient')
                if found['category']!='men_first' or not con.execute(
                    'SELECT 1 FROM team_season_memberships WHERE team_id=? AND league=? AND season=?',
                    (found['id'],row['league'],row['season'])).fetchone():
                    raise QualityError('team category/competition-season membership mismatch')
                identities.append(found['id'])
            if identities[0]==identities[1]:
                raise QualityError('same team on both sides')
            updated=row.get('source_updated_at')
            if updated is not None and timestamp(updated)>timestamp(now):
                raise QualityError('future source timestamp')
            if updated is not None and (row.get('result') is not None or row.get('xg') is not None) and timestamp(updated)<kickoff:
                raise QualityError('post-match data claims a pre-match source update')
            if (row.get('result') is not None or row.get('xg') is not None or row.get('stats')) and kickoff>=timestamp(now):
                raise QualityError('future match has post-match data')
            result=row.get('result'); xg=row.get('xg'); stats=row.get('stats',{})
            if result is not None:
                require_keys(result,('home_goals','away_goals','period','status'))
                hg=number(result.get('home_goals'),'home goals',True);ag=number(result.get('away_goals'),'away goals',True)
                if result.get('period')!='FT90' or result.get('status')!='final':
                    raise QualityError('baseline accepts final FT90 results only')
            if xg is not None:
                require_keys(xg,('home','away','method','period'))
                hxg=number(xg.get('home'),'home xG');axg=number(xg.get('away'),'away xG')
                method=text(xg.get('method'),'xG method')
                if xg.get('period')!='FT90' or result is None:
                    raise QualityError('xG requires the associated final FT90 result')
            require_keys(stats,('home','away'))
            for side,values in stats.items():
                require_keys(values,('shots','shots_on_target','corners','possession'))
                for key,value in values.items():
                    number(value,key,integer=key!='possession')
                    if key=='possession' and value>100:
                        raise QualityError('possession exceeds 100')
                if values.get('shots_on_target',0)>values.get('shots',float('inf')):
                    raise QualityError('shots on target exceed total shots')
            identity=(row['league'],row['season'],kickoff.isoformat(),*identities)
            existing=con.execute('SELECT * FROM matches WHERE source_id=? AND source_match_id=?',(source,sid)).fetchone()
            if existing:
                if tuple(existing[k] for k in ('league','season','kickoff_utc','home_id','away_id'))!=identity:
                    raise QualityError('match identity conflict or reschedule; manual review required')
                match_id=existing['id']
            else:
                # Conflicting provider IDs for the same fixture are quarantined, not counted twice.
                if con.execute('SELECT 1 FROM matches WHERE source_id=? AND league=? AND season=? AND kickoff_utc=? AND home_id=? AND away_id=?',
                               (source,*identity)).fetchone():
                    raise QualityError('duplicate fixture under a different source match ID')
                match_id=con.execute('INSERT INTO matches(source_id,source_match_id,league,season,kickoff_utc,home_id,away_id,observed_at,quality) VALUES (?,?,?,?,?,?,?,?,?)',
                                     (source,sid,*identity,now,'accepted')).lastrowid
            if con.execute('SELECT 1 FROM observations WHERE match_id=? AND payload_sha256=?',(match_id,digest(row))).fetchone():
                return False
            observation=con.execute('INSERT INTO observations(match_id,run_id,source_id,observed_at,source_updated_at,payload_sha256,quality) VALUES (?,?,?,?,?,?,?)',
                                    (match_id,run,source,now,updated,digest(row),'accepted')).lastrowid
            if result is not None:
                con.execute('INSERT INTO match_results VALUES (?,?,?,?,?)',(observation,hg,ag,'FT90','final'))
            if xg is not None:
                con.execute('INSERT INTO xg_stats VALUES (?,?,?,?,?)',(observation,hxg,axg,method,'FT90'))
            for i,side in enumerate(('home','away')):
                if side in stats:
                    con.execute('INSERT INTO team_stats VALUES (?,?,?,?)',(observation,identities[i],'FT90',encoded(stats[side])))
            return True
        def odds(row):
            require_keys(row,('source_quote_id','match_id','bookmaker','market','side','line','odds','period','captured_at'))
            qid=text(row.get('source_quote_id'),'quote id'); book=text(row.get('bookmaker'),'bookmaker')
            match_sid=text(row.get('match_id'),'quote match ID')
            if match_sid in blocked_matches:
                raise QualityError('match failed quality review in this batch')
            match=con.execute('SELECT * FROM matches WHERE source_id=? AND source_match_id=?',(source,match_sid)).fetchone()
            if not match:
                raise QualityError('quote has no accepted match')
            captured=timestamp(row['captured_at'])
            if captured>timestamp(now) or captured>=timestamp(match['kickoff_utc']):
                raise QualityError('future or in-play quote cannot be a pre-match baseline input')
            if row.get('period')!='FT90':
                raise QualityError('unsupported quote period')
            market=row.get('market');side=row.get('side')
            if market not in ('Asian Handicap','Over/Under') or side not in (('home','away') if market=='Asian Handicap' else ('over','under')):
                raise QualityError('market/side mismatch')
            line=row.get('line');price=number(row.get('odds'),'odds')
            if type(line) not in (int,float) or not math.isfinite(line) or abs(line*4-round(line*4))>1e-9 or price<=1 or abs(line)>20 or (market=='Over/Under' and line<0):
                raise QualityError('invalid quarter line or decimal odds')
            values=(match['id'],book,market,side,round(line*4),price,'FT90',captured.isoformat())
            old=con.execute('SELECT * FROM odds_snapshots WHERE source_id=? AND source_quote_id=?',(source,qid)).fetchone()
            if old:
                if tuple(old[k] for k in ('match_id','bookmaker','market','side','line_quarters','odds','period','captured_at'))!=values:
                    raise QualityError('quote identifier reused with different price/line')
                return False
            if con.execute('SELECT 1 FROM odds_snapshots WHERE source_id=? AND match_id=? AND bookmaker=? AND market=? AND side=? AND line_quarters=? AND odds=? AND period=? AND captured_at=?',
                           (source,*values)).fetchone():
                return False
            con.execute('INSERT INTO odds_snapshots(source_id,run_id,source_quote_id,match_id,bookmaker,market,side,line_quarters,odds,period,captured_at,observed_at,quality) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (source,run,qid,*values,now,'accepted'))
            return True
        for row in dataset.get('quarantine',[]):
            rejected('match',dict(source_match_id=row['source_row_id'],adapter_rejection=row['payload']),
                     QualityError(row['reason']))
        process('team',dataset.get('teams',[]),team)
        process('match',dataset.get('matches',[]),match)
        process('quote',dataset.get('odds',[]),odds)
        con.execute('UPDATE ingestion_runs SET accepted=?,quarantined=? WHERE id=?',(accepted,quarantined,run))
    return dict(run_id=run,duplicate_batch=False,accepted=accepted,quarantined=quarantined,ingested_at=now)
