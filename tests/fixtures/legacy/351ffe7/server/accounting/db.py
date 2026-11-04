"""SQLite connections and explicit local account initialization."""

from contextlib import contextmanager
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from argon2 import PasswordHasher


password_hasher = PasswordHasher()


@contextmanager
def connect(db_path: str | Path):
    connection = sqlite3.connect(db_path, timeout=10, isolation_level=None, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.create_function("app_now", 0, time.time)
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
    finally:
        connection.close()


def initialize_database(db_path: str | Path) -> None:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    schema = Path(__file__).parent.parent / "migrations" / "001_initial.sql"
    with connect(path) as db:
        db.executescript("BEGIN;\n" + schema.read_text() + "\nCOMMIT;")
        db.execute("BEGIN IMMEDIATE")
        if "category" not in {row["name"] for row in db.execute("PRAGMA table_info(expenses)")}:
            db.execute("ALTER TABLE expenses ADD COLUMN category TEXT NOT NULL DEFAULT '' CHECK (length(category) <= 24)")
        additions = schema.with_name("002_categories_images.sql").read_text()
        for statement in additions.split(";"):
            if statement.strip():
                db.execute(statement)
        columns = {row["name"] for row in db.execute("PRAGMA table_info(expenses)")}
        for name, definition in {
            "voided": "INTEGER NOT NULL DEFAULT 0 CHECK (voided IN (0, 1))",
            "rating": "INTEGER CHECK (rating IS NULL OR (typeof(rating) = 'integer' AND rating BETWEEN 1 AND 5))",
            "rating_reason": "TEXT NOT NULL DEFAULT '' CHECK (length(rating_reason) <= 500)",
        }.items():
            if name not in columns:
                db.execute(f"ALTER TABLE expenses ADD COLUMN {name} {definition}")
        for statement in schema.with_name("003_maintenance.sql").read_text().split(";"):
            if statement.strip():
                db.execute(statement)
        for statement in schema.with_name("004_automation.sql").read_text().split(";"):
            if statement.strip():
                db.execute(statement)
        db.execute("INSERT OR IGNORE INTO app_state(singleton,restore_id) VALUES(1,?)", (str(uuid4()),))
        db.commit()


def create_user(
    db_path: str | Path, username: str, password: str, display_name: str
) -> dict[str, str]:
    """Create one explicitly requested account; never overwrite an existing user."""
    if not username.strip() or not password or not display_name.strip():
        raise ValueError("username, password and display_name are required")
    initialize_database(db_path)
    user = {"id": str(uuid4()), "display_name": display_name}
    encoded_password = password_hasher.hash(password)
    with connect(db_path) as db:
        db.execute(
            "INSERT INTO users (id, username, password_hash, display_name) VALUES (?, ?, ?, ?)",
            (user["id"], username, encoded_password, display_name),
        )
    return user
