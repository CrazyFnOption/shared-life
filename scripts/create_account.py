"""Explicitly create an independent account with a private ledger."""

import argparse
from getpass import GetPassWarning, getpass
from pathlib import Path
import sqlite3
import warnings

from server.accounting.db import create_user


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--username", required=True)
    parser.add_argument("--display-name", required=True)
    args = parser.parse_args()
    username, display_name = args.username.strip(), args.display_name.strip()
    if not username or not display_name:
        parser.error("Username and display name must not be blank.")
    print(f"Database: {args.database.resolve()}")
    print("The new account has no family. Creating or joining a family is an explicit in-app action.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", GetPassWarning)
            password = getpass("New password (8-128 characters): ")
            confirmation = getpass("Confirm new password: ")
    except GetPassWarning:
        parser.exit(1, "A terminal with hidden password input is required.\n")
    except (EOFError, KeyboardInterrupt):
        parser.exit(1, "\nAccount creation cancelled.\n")
    if not 8 <= len(password) <= 128:
        parser.error("Password must contain 8-128 characters.")
    if password != confirmation:
        parser.error("Passwords do not match.")
    try:
        create_user(args.database, username, password, display_name)
    except sqlite3.IntegrityError:
        parser.exit(1, "Account already exists; no account was overwritten.\n")
    except ValueError as error:
        parser.exit(1, f"{error}\n")
    except sqlite3.Error:
        parser.exit(1, "Database operation failed; no existing account was overwritten.\n")
    print(f"Account created: {username} ({display_name}).")


if __name__ == "__main__":
    main()
