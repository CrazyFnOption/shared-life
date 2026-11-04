"""Validate a backup and restore to a new isolated database."""
import argparse
from server.automation.backups import restore

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backup',required=True);parser.add_argument('--destination',required=True);parser.add_argument('--credentials')
    args=parser.parse_args()
    try:
        result=restore(args.backup,args.destination,args.credentials)
    except Exception as error:
        parser.exit(1,f'Restore failed ({type(error).__name__}); source and existing databases were not replaced.\n')
    print('Restored to new database. Sessions invalidated; push empty; automation paused.')
    print('Login enabled from explicit credentials.' if result['login_enabled'] else 'Login disabled: no credentials were supplied.')
if __name__=='__main__': main()
