"""Atomic parent-versioned ledger mutations and durable original receipts."""

from copy import deepcopy
import hashlib
import json
import sqlite3

from fastapi import HTTPException

from server.accounting.categories import ensure_category


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def complete_expense(expense: dict) -> dict:
    """Fill defaults on historical receipts without consulting the current ledger."""
    result = dict(expense)
    result.setdefault("category", "")
    result["voided"] = bool(result.get("voided", False))
    result.setdefault("rating", None)
    result.setdefault("rating_reason", "")
    result.setdefault("refunds", [])
    result["net_amount_cents"] = 0 if result["voided"] else result["amount_cents"] - sum(
        r["amount_cents"] for r in result["refunds"] if not r["voided"]
    )
    return result


def read_expense(db: sqlite3.Connection, expense_id: str) -> dict | None:
    row = db.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)).fetchone()
    if row is None:
        return None
    expense = dict(row)
    expense["refunds"] = []
    for row in db.execute(
        "SELECT id, amount_cents, occurred_at, reason, voided, version FROM refunds WHERE expense_id = ? ORDER BY id",
        (expense_id,),
    ):
        expense["refunds"].append(dict(row, voided=bool(row["voided"])))
    return complete_expense(expense)


def shared_expense(expense: dict) -> dict:
    return dict(expense, refunds=[r for r in expense["refunds"] if not r["voided"]])


def can_read(expense: dict | None, user_id: str) -> bool:
    return expense is not None and (expense["owner_id"] == user_id or (expense["scope"] == "shared" and not expense["voided"]))


def owned_expense(db: sqlite3.Connection, user_id: str, expense_id: str) -> dict:
    expense = read_expense(db, expense_id)
    if expense is None or expense["owner_id"] != user_id:
        raise HTTPException(404, detail="Expense not found")
    return expense


def request_payload(operation) -> dict:
    payload = operation.model_dump(mode="json")
    if payload["kind"] in {"create", "update"} and "rating" not in operation.data.model_fields_set:
        payload["data"].pop("rating", None)
        payload["data"].pop("rating_reason", None)
    return payload


def operation_hash(payload: dict) -> str:
    normalized = dict(payload, data=dict(payload["data"]))
    # Compatibility with both v1 and the category extension.
    if not normalized["data"].get("category"):
        normalized["data"].pop("category", None)
    return digest(normalized)


def previous_receipt(db, user_id: str, operation_id: str, request_hash: str, *, request_id: str | None = None) -> dict | None:
    row = db.execute("SELECT request_hash, receipt_json FROM operations WHERE user_id = ? AND operation_id = ?", (user_id, operation_id)).fetchone()
    if row is None:
        return None
    receipt = json.loads(row["receipt_json"])
    if row["request_hash"] != request_hash:
        code = "request_decided" if request_id and receipt.get("request", {}).get("id") == request_id else "operation_id_reused"
        raise HTTPException(409, detail={"code": code})
    receipt["expense"] = complete_expense(receipt["expense"])
    return receipt


def changed_expense(before: dict, kind: str, data: dict) -> dict:
    """Validate a proposed action; no database writes occur here."""
    if before["voided"]:
        raise HTTPException(409, detail={"code": "expense_voided"})
    expense = deepcopy(before)
    if kind in {"update", "review", "scope"}:
        expense.update(data)
    elif kind == "void":
        expense["voided"] = True
    elif kind.startswith("refund_"):
        refund_id = data["refund_id"]
        refund = next((r for r in expense["refunds"] if r["id"] == refund_id), None)
        if kind == "refund_create":
            if refund is not None:
                raise HTTPException(409, detail={"code": "refund_exists"})
            expense["refunds"].append(dict(id=refund_id, amount_cents=data["amount_cents"], occurred_at=data["occurred_at"], reason=data["reason"], voided=False, version=1))
        else:
            if refund is None:
                raise HTTPException(404, detail="Refund not found")
            if refund["voided"]:
                raise HTTPException(409, detail={"code": "refund_voided"})
            if kind == "refund_void":
                refund["voided"] = True
            else:
                refund.update({k: data[k] for k in ("amount_cents", "occurred_at", "reason")})
            refund["version"] += 1
    total = sum(r["amount_cents"] for r in expense["refunds"] if not r["voided"])
    if total > expense["amount_cents"]:
        raise HTTPException(422, detail={"code": "refund_exceeds_amount"})
    expense["version"] += 1
    expense["refunds"].sort(key=lambda r: r["id"])
    return complete_expense(expense)


def ensure_refund_id(db, expense_id: str, kind: str, data: dict):
    if kind == "refund_create" and db.execute("SELECT 1 FROM refunds WHERE id = ?", (data["refund_id"],)).fetchone():
        raise HTTPException(409, detail={"code": "refund_exists"})


def persist_expense(db, expense: dict, *, exclude_request: str | None = None):
    db.execute(
        """INSERT INTO expenses (id, owner_id, scope, amount_cents, purpose, occurred_at, location, category, version, voided, rating, rating_reason)
           VALUES (:id, :owner_id, :scope, :amount_cents, :purpose, :occurred_at, :location, :category, :version, :voided, :rating, :rating_reason)
           ON CONFLICT(id) DO UPDATE SET scope = excluded.scope, amount_cents = excluded.amount_cents,
           purpose = excluded.purpose, occurred_at = excluded.occurred_at, location = excluded.location,
           category = excluded.category, version = excluded.version, voided = excluded.voided,
           rating = excluded.rating, rating_reason = excluded.rating_reason""", expense,
    )
    for refund in expense["refunds"]:
        db.execute(
            """INSERT INTO refunds (id, expense_id, amount_cents, occurred_at, reason, voided, version)
               VALUES (:id, :expense_id, :amount_cents, :occurred_at, :reason, :voided, :version)
               ON CONFLICT(id) DO UPDATE SET amount_cents = excluded.amount_cents,
               occurred_at = excluded.occurred_at, reason = excluded.reason, voided = excluded.voided, version = excluded.version""",
            dict(refund, expense_id=expense["id"]),
        )
    if expense["category"]:
        ensure_category(db, expense["owner_id"], expense["scope"], expense["category"])
    db.execute("UPDATE change_requests SET status = 'stale' WHERE expense_id = ? AND status = 'pending' AND id != ?", (expense["id"], exclude_request or ""))
    if expense["scope"] == "private" or expense["voided"]:
        db.execute("UPDATE change_requests SET withdrawn = 1 WHERE expense_id = ?", (expense["id"],))


def store_receipt(db, user_id: str, operation_id: str, request_hash: str, receipt: dict):
    db.execute("INSERT INTO operations (user_id, operation_id, request_hash, receipt_json) VALUES (?, ?, ?, ?)", (user_id, operation_id, request_hash, canonical_json(receipt)))


def store_revision(db, user_id: str, operation_id: str, before: dict | None, expense: dict):
    db.execute(
        "INSERT INTO revisions (expense_id, version, user_id, operation_id, before_json, after_json) VALUES (?, ?, ?, ?, ?, ?)",
        (expense["id"], expense["version"], user_id, operation_id, canonical_json(before) if before else None, canonical_json(expense)),
    )

    # Invalidation shares the ledger transaction, including approved requests.
    from server.automation.reports import invalidate
    invalidate(db, before, expense)


def submit_operation(db: sqlite3.Connection, user_id: str, operation) -> dict:
    payload = request_payload(operation)
    request_hash = operation_hash(payload)
    operation_id, expense_id = payload["operation_id"], payload["expense_id"]
    db.execute("BEGIN IMMEDIATE")
    previous = previous_receipt(db, user_id, operation_id, request_hash)
    if previous is not None:
        db.commit()
        return previous
    before = read_expense(db, expense_id)
    if operation.kind == "create":
        if before is not None:
            raise HTTPException(409, detail={"code": "expense_exists"})
        expense = complete_expense(dict(id=expense_id, owner_id=user_id, version=1, **payload["data"]))
    else:
        if before is None or before["owner_id"] != user_id:
            raise HTTPException(404, detail="Expense not found")
        if before["version"] != operation.base_version:
            raise HTTPException(409, detail={"code": "version_conflict", "current": before})
        ensure_refund_id(db, expense_id, operation.kind, payload["data"])
        expense = changed_expense(before, operation.kind, payload["data"])
    persist_expense(db, expense)
    receipt = {"operation_id": operation_id, "expense": expense}
    store_receipt(db, user_id, operation_id, request_hash, receipt)
    store_revision(db, user_id, operation_id, before, expense)
    db.commit()
    return receipt


def history(db, user_id: str, expense_id: str) -> list[dict]:
    owned_expense(db, user_id, expense_id)
    return [{"version": row["version"], "operation_id": row["operation_id"],
             "before": complete_expense(json.loads(row["before_json"])) if row["before_json"] else None,
             "after": complete_expense(json.loads(row["after_json"]))}
            for row in db.execute("SELECT * FROM revisions WHERE expense_id = ? ORDER BY version", (expense_id,))]
