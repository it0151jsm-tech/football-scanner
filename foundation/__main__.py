"""python -m foundation --help; local files only, no download command."""
import argparse
import json
import sys
import sqlite3
from pathlib import Path

from .ingest import import_file
from .store import DEFAULT_DB, backup, initialize
from .validate import quality_report, validate


def main(argv=None):
    parser=argparse.ArgumentParser(description='Offline foundation lab; never connects to the app database or an API')
    parser.add_argument('--db',type=Path,default=DEFAULT_DB)
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('init')
    imp=sub.add_parser('import');imp.add_argument('file',type=Path)
    sub.add_parser('quality')
    val=sub.add_parser('validate');val.add_argument('--mode',choices=['observed','research','sample'],default='observed');val.add_argument('--source')
    b=sub.add_parser('backup');b.add_argument('destination',type=Path)
    r=sub.add_parser('restore');r.add_argument('backup_file',type=Path)
    args=parser.parse_args(argv)
    try:
        if args.command=='init': result={'database':str(initialize(args.db)),'status':'initialized'}
        elif args.command=='import': result=import_file(args.file,args.db)
        elif args.command=='quality': result=quality_report(args.db)
        elif args.command=='validate': result=validate(args.db,args.mode,args.source)
        elif args.command=='backup': result=backup(args.db,args.destination)
        else: result=backup(args.backup_file,args.db,restore=True)
        print(json.dumps(result,ensure_ascii=False,allow_nan=False,indent=2))
        return 0
    except (ValueError,KeyError,TypeError,OSError,sqlite3.Error) as exc:
        print(json.dumps({'error':str(exc)},ensure_ascii=False),file=sys.stderr)
        return 2


if __name__=='__main__':
    sys.exit(main())
