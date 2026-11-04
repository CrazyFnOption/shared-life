"""Authenticated image attachments stored transactionally beside the ledger."""

from hashlib import sha256
from io import BytesIO
import sqlite3

from fastapi import HTTPException
from PIL import Image, UnidentifiedImageError
from server.accounting import families

MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
FORMATS = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


def require_expense(db: sqlite3.Connection, user_id: str, expense_id: str, *, write=False):
    expense = db.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)).fetchone()
    if not families.visible(db, expense, user_id) or (write and expense['owner_id'] != user_id):
        raise HTTPException(404, detail="Expense not found")
    if write and expense['scope'] == 'shared':
        families.require_shared(db, user_id, expense['family_id'])
    if write and expense["voided"]:
        raise HTTPException(409, detail={"code": "expense_voided"})


def validate_image(data: bytes, content_type: str) -> str:
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(413, detail={"code": "image_too_large"})
    if content_type not in FORMATS.values():
        raise HTTPException(415, detail={"code": "unsupported_image"})
    try:
        with Image.open(BytesIO(data)) as image:
            mime = FORMATS.get(image.format)
            if mime != content_type or image.width * image.height > MAX_IMAGE_PIXELS:
                raise ValueError("Image format or dimensions not supported")
            image.verify()
        # verify() checks container integrity; load() also detects truncated JPEG data.
        with Image.open(BytesIO(data)) as image:
            image.load()
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError) as error:
        raise HTTPException(422, detail={"code": "invalid_image"}) from error
    return mime


def metadata(row) -> dict:
    return {key: row[key] for key in ("id", "expense_id", "mime_type", "size_bytes")}


def visible_images(db: sqlite3.Connection, user_id: str) -> list[dict]:
    return [metadata(row) for row in db.execute(
        """SELECT i.id, i.expense_id, i.mime_type, i.size_bytes FROM expense_images i
           JOIN expenses e ON e.id = i.expense_id
           WHERE i.deleted = 0 AND ((e.scope='private' AND e.owner_id=?) OR (e.scope='shared' AND e.family_id=? AND (e.owner_id=? OR e.voided=0))) ORDER BY i.id""",
        (user_id, families.current_id(db,user_id), user_id),
    )]


def put_image(db: sqlite3.Connection, user_id: str, expense_id: str, image_id: str,
              data: bytes, content_type: str) -> dict:
    mime = validate_image(data, content_type)
    digest = sha256(data).hexdigest()
    db.execute("BEGIN IMMEDIATE")
    require_expense(db, user_id, expense_id, write=True)
    old = db.execute("SELECT * FROM expense_images WHERE id = ?", (image_id,)).fetchone()
    if old is not None:
        if old["expense_id"] != expense_id:
            raise HTTPException(404, detail="Image not found")
        if old["deleted"]:
            raise HTTPException(409, detail={"code": "image_deleted"})
        if old["content_hash"] != digest:
            raise HTTPException(409, detail={"code": "image_id_reused"})
        db.commit()
        return metadata(old)
    count = db.execute(
        "SELECT count(*) FROM expense_images WHERE expense_id = ? AND deleted = 0", (expense_id,)
    ).fetchone()[0]
    if count >= 3:
        raise HTTPException(409, detail={"code": "image_limit"})
    db.execute(
        """INSERT INTO expense_images (id, expense_id, mime_type, size_bytes, content_hash, data)
           VALUES (?, ?, ?, ?, ?, ?)""", (image_id, expense_id, mime, len(data), digest, data),
    )
    db.commit()
    return {"id": image_id, "expense_id": expense_id, "mime_type": mime, "size_bytes": len(data)}


def get_image(db: sqlite3.Connection, user_id: str, expense_id: str, image_id: str):
    db.execute("BEGIN")
    require_expense(db, user_id, expense_id)
    row = db.execute(
        "SELECT mime_type, data FROM expense_images WHERE id = ? AND expense_id = ? AND deleted = 0",
        (image_id, expense_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, detail="Image not found")
    db.commit()
    return row


def delete_image(db: sqlite3.Connection, user_id: str, expense_id: str, image_id: str):
    db.execute("BEGIN IMMEDIATE")
    require_expense(db, user_id, expense_id, write=True)
    old = db.execute("SELECT expense_id FROM expense_images WHERE id = ?", (image_id,)).fetchone()
    if old is not None and old["expense_id"] != expense_id:
        raise HTTPException(404, detail="Image not found")
    # Also remember a deletion which reaches us before its delayed upload.
    db.execute(
        """INSERT INTO expense_images (id, expense_id, deleted) VALUES (?, ?, 1)
           ON CONFLICT(id) DO UPDATE SET deleted = 1, data = NULL""", (image_id, expense_id),
    )
    db.commit()
