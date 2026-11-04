"""Explicit management-only resume after isolated restore."""
import argparse
from pathlib import Path
from server.accounting.db import connect

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',required=True);parser.add_argument('--resume',action='store_true',required=True)
    args=parser.parse_args()
    if not Path(args.database).is_file():
        parser.error('Database does not exist')
    with connect(args.database) as db:
        db.execute('UPDATE app_state SET automation_paused=0 WHERE singleton=1')
    print('Automation resumed. Model and push environment gates still apply.')
if __name__=='__main__': main()
