"""Explicit atomic local migration of a supported old database to schema 10."""
import argparse
import json
from pathlib import Path
from datetime import datetime,UTC
from server.accounting.db import migrate_database,connect,preflight
from server.memos.migration_guard import capture,verify,summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',required=True)
    parser.add_argument('--evidence',type=Path,help='New private evidence file; no row values or credentials')
    args=parser.parse_args()
    changed=None
    try:
        if args.evidence and args.evidence.exists():raise ValueError('Evidence path must be new')
        status=preflight(args.database)
        with connect(args.database,read_only=True) as db:
            input_version=db.execute('PRAGMA user_version').fetchone()[0]
            before=capture(db)
        changed=migrate_database(args.database)
        with connect(args.database,read_only=True) as db:
            verify(db,before)
            version=db.execute('PRAGMA user_version').fetchone()[0]
        if args.evidence:
            from server.automation.backups import write_private
            write_private(args.evidence,{'created_at':datetime.now(UTC).isoformat(),'input_schema':status,'input_schema_version':input_version,'schema':version,'changed':changed,'original_rows_preserved':True,'original_tables':summary(before),'minimum_compatible_program':'full shared-life schema10 reader/writer; never schema9/B/A','production_authorization_inferred':False})
    except Exception as error:
        state='migration committed; evidence verification/write did not complete' if changed else 'no migration committed; source retained or transaction rolled back'
        parser.exit(1,f'Migration failed ({type(error).__name__}); {state}.\n')
    print('Migrated to schema 10.' if changed else 'Schema 10 verified; no migration needed.')


if __name__=='__main__':
    main()
