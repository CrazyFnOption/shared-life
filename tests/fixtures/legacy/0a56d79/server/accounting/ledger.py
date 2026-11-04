"""Atomic parent-versioned ledger mutations and durable original receipts."""

from copy import deepcopy
import hashlib
import json
import sqlite3

from fastapi import HTTPException

from server.accounting.categories import ensure_catalog_category
from server.accounting import families


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def complete_expense(expense: dict) -> dict:
    """Fill defaults on historical receipts without consulting the current ledger."""
    result = dict(expense)
    result.setdefault("category", "")
    result.setdefault("family_id", None)
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


def can_read(expense: dict | None, user_id: str, db) -> bool:
    return families.visible(db, expense, user_id)


def owned_expense(db: sqlite3.Connection, user_id: str, expense_id: str) -> dict:
    expense = read_expense(db, expense_id)
    if not can_read(expense, user_id, db) or expense["owner_id"] != user_id:
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
    row = db.execute("SELECT * FROM operations WHERE user_id = ? AND operation_id = ?", (user_id, operation_id)).fetchone()
    if row is None:
        return None
    # Authorize both original receipt provenance and the current object before returning bytes.
    if row['family_id']:
        families.require_shared(db, user_id, row['family_id'])
        version = db.execute('SELECT membership_version FROM users WHERE id=?', (user_id,)).fetchone()[0]
        if row['membership_version'] != version:
            raise HTTPException(409, detail={'code': 'family_changed'})
    current = read_expense(db, row['expense_id'])
    if not can_read(current, user_id, db):
        raise HTTPException(404, detail='Expense not found')
    receipt = json.loads(row["receipt_json"])
    if receipt['expense']['scope'] == 'shared':
        receipt['expense'].setdefault('family_id', row['family_id'])
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
        """INSERT INTO expenses (id, owner_id, scope, amount_cents, purpose, occurred_at, location, category, version, voided, rating, rating_reason, family_id)
           VALUES (:id, :owner_id, :scope, :amount_cents, :purpose, :occurred_at, :location, :category, :version, :voided, :rating, :rating_reason, :family_id)
           ON CONFLICT(id) DO UPDATE SET scope = excluded.scope, amount_cents = excluded.amount_cents,
           purpose = excluded.purpose, occurred_at = excluded.occurred_at, location = excluded.location,
           category = excluded.category, version = excluded.version, voided = excluded.voided,
           rating = excluded.rating, rating_reason = excluded.rating_reason, family_id = excluded.family_id""", expense,
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
        namespace = expense['family_id'] if expense['scope'] == 'shared' else expense['owner_id']
        ensure_catalog_category(db, expense['scope'], namespace, expense['category'])
    db.execute("UPDATE change_requests SET status = 'stale' WHERE expense_id = ? AND status = 'pending' AND id != ?", (expense["id"], exclude_request or ""))
    if expense["scope"] == "private" or expense["voided"]:
        db.execute("UPDATE change_requests SET withdrawn = 1 WHERE expense_id = ?", (expense["id"],))
    from server.accounting.category_changes import invalidate_expense
    invalidate_expense(db,expense['id'])


def store_receipt(db, user_id: str, operation_id: str, request_hash: str, receipt: dict, *, origin_family_id=None):
    fid = receipt['expense'].get('family_id') or origin_family_id
    # Scope-to-private receipts still originate in their former family.
    if fid is None:
        prior = db.execute('SELECT after_json FROM revisions WHERE expense_id=? ORDER BY version DESC LIMIT 1', (receipt['expense']['id'],)).fetchone()
        if prior:
            old = json.loads(prior['after_json'])
            if old['scope'] == 'shared':
                fid = old.get('family_id')
    version = db.execute('SELECT membership_version FROM users WHERE id=?', (user_id,)).fetchone()[0] if fid else None
    db.execute("INSERT INTO operations (user_id,operation_id,request_hash,receipt_json,family_id,expense_id,membership_version) VALUES (?,?,?,?,?,?,?)", (user_id,operation_id,request_hash,canonical_json(receipt),fid,receipt['expense']['id'],version))


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
    before = read_expense(db, expense_id)
    if before is not None and not can_read(before,user_id,db):
        raise HTTPException(404, detail='Expense not found')
    wants_shared = payload['data'].get('scope') == 'shared'
    if before is not None and before['scope'] == 'shared':
        if not can_read(before, user_id, db) or before['owner_id'] != user_id:
            raise HTTPException(404, detail='Expense not found')
        families.require_shared(db, user_id, before['family_id'])
    elif wants_shared:
        families.require_shared(db, user_id)
    previous = previous_receipt(db, user_id, operation_id, request_hash)
    if previous is not None:
        db.commit()
        return previous
    before = read_expense(db, expense_id)
    if operation.kind == "create":
        if before is not None:
            raise HTTPException(409, detail={"code": "expense_exists"})
        expense = complete_expense(dict(id=expense_id, owner_id=user_id, version=1, family_id=families.current_id(db, user_id) if wants_shared else None, **payload["data"]))
        if expense["rating"] is None:
            expense["rating"] = 3
    else:
        if not can_read(before, user_id, db) or before["owner_id"] != user_id:
            raise HTTPException(404, detail="Expense not found")
        if before["version"] != operation.base_version:
            raise HTTPException(409, detail={"code": "version_conflict", "current": before})
        ensure_refund_id(db, expense_id, operation.kind, payload["data"])
        expense = changed_expense(before, operation.kind, payload["data"])
    if expense['scope'] == 'private':
        expense['family_id'] = None
    elif expense.get('family_id') is None:
        expense['family_id'] = families.require_shared(db, user_id)
    persist_expense(db, expense)
    receipt = {"operation_id": operation_id, "expense": expense}
    store_receipt(db, user_id, operation_id, request_hash, receipt, origin_family_id=before.get("family_id") if before else None)
    store_revision(db, user_id, operation_id, before, expense)
    db.commit()
    return receipt


def history(db, user_id: str, expense_id: str) -> list[dict]:
    owned_expense(db, user_id, expense_id)
    result = []
    for row in db.execute("SELECT r.*,o.family_id AS origin_family FROM revisions r JOIN operations o ON o.user_id=r.user_id AND o.operation_id=r.operation_id WHERE r.expense_id=? ORDER BY r.version", (expense_id,)):
        snapshots = []
        allowed = True
        for key in ('before_json','after_json'):
            value = json.loads(row[key]) if row[key] else None
            if value is not None:
                if value['scope']=='shared':
                    value.setdefault('family_id',row['origin_family'])
                    if value['family_id'] != families.current_id(db,user_id):
                        allowed=False
                value=complete_expense(value)
            snapshots.append(value)
        if allowed:
            result.append({'version':row['version'],'operation_id':row['operation_id'],'before':snapshots[0],'after':snapshots[1]})
    return result
