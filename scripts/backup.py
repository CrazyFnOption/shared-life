"""Create a new local backup directory; never overwrite."""
import argparse
from server.automation.backups import backup

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',required=True);parser.add_argument('--destination',required=True)
    args=parser.parse_args()
    try:
        result=backup(args.database,args.destination)
    except Exception as error:
        parser.exit(1, f'Backup failed ({type(error).__name__}); any partial directory is retained for inspection.\n')
    print(f'Backup created: {result}; credentials.json is separate and private.')
if __name__=='__main__': main()
