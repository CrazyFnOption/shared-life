"""Create a new, explicitly fictional local ledger. Never overwrite a database."""

import argparse
from pathlib import Path

from server.accounting.db import create_user, initialize_database, connect


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path(".local/dev.sqlite3"))
    args = parser.parse_args()
    if args.database.exists():
        parser.error("Database already exists; use a new path. No data was changed.")
    args.database.parent.mkdir(parents=True, exist_ok=True)
    initialize_database(args.database)
    users = []
    for username, display_name in (("demo-a", "小甲"), ("demo-b", "小乙")):
        users.append(create_user(args.database, username, "demo-only-2026", display_name)["id"])
    from server.accounting.families import create_explicit_family
    with connect(args.database) as db:
        db.execute("BEGIN IMMEDIATE")
        create_explicit_family(db, users)
        db.commit()
    print(f"Fictional ledger created: {args.database.resolve()}")
    print("Local demo accounts: demo-a / demo-b; password: demo-only-2026")
    print("These public demo credentials are for fictional local data only.")


if __name__ == "__main__":
    main()
