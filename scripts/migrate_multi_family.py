"""Compatibility entry point for explicit migration to the current schema."""
import argparse
from server.accounting.db import migrate_database


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True)
    args = parser.parse_args()
    try:
        changed = migrate_database(args.database)
    except Exception as error:
        parser.exit(1, f'Migration failed ({type(error).__name__}); transaction rolled back.\n')
    print('Migrated to schema 9.' if changed else 'Schema 9 verified; no migration needed.')


if __name__ == '__main__':
    main()
