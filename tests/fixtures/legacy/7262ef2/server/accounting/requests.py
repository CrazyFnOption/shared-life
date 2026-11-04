"""Online change requests; approval and the ledger mutation share one transaction."""

from datetime import UTC, datetime
import json

from fastapi import HTTPException

from server.accounting import ledger, families


def serialize(row) -> dict:
    return {key: row[key] for key in ("id", "expense_id", "requester_id", "owner_id", "base_version", "kind", "reason", "status", "created_at", "decided_at")} | {"data": json.loads(row["data_json"]), "before": json.loads(row["before_json"])}


def visible_requests(db, user_id: str) -> list[dict]:
    return [serialize(row) for row in db.execute(
        """SELECT r.* FROM change_requests r JOIN expenses e ON e.id=r.expense_id
        WHERE r.family_id=? AND ((r.owner_id=? AND (e.scope='shared' OR e.owner_id=?))
        OR (r.requester_id=? AND r.withdrawn=0 AND e.scope='shared' AND e.voided=0))
        ORDER BY r.created_at,r.id""", (families.current_id(db,user_id),user_id,user_id,user_id))]



def submit_request(db, user_id: str, body) -> dict:
    payload = body.model_dump(mode="json")
    request_hash = ledger.digest(payload)
    db.execute("BEGIN IMMEDIATE")
    expense = ledger.read_expense(db, payload["expense_id"])
    if not ledger.can_read(expense, user_id, db) or expense["owner_id"] == user_id:
        raise HTTPException(404, detail="Expense not found")
    families.require_shared(db, user_id, expense["family_id"])
    previous = db.execute("SELECT * FROM change_requests WHERE id = ?", (payload["id"],)).fetchone()
    if previous is not None:
        if previous["withdrawn"] or previous["requester_id"] != user_id:
            raise HTTPException(404, detail="Request not found")
        if previous["request_hash"] != request_hash:
            raise HTTPException(409, detail={"code": "request_id_reused"})
        db.commit()
        return serialize(previous)
    if expense["version"] != payload["base_version"]:
        raise HTTPException(409, detail={"code": "version_conflict", "current": ledger.shared_expense(expense)})
    ledger.ensure_refund_id(db, expense["id"], payload["kind"], payload["data"])
    ledger.changed_expense(expense, payload["kind"], payload["data"])
    db.execute(
        """INSERT INTO change_requests (id, expense_id, requester_id, owner_id, base_version, kind, data_json, before_json, reason, status, created_at, request_hash, family_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)""",
        (payload["id"], expense["id"], user_id, expense["owner_id"], payload["base_version"], payload["kind"], ledger.canonical_json(payload["data"]), ledger.canonical_json(ledger.shared_expense(expense)), payload["reason"], datetime.now(UTC).isoformat(), request_hash, expense["family_id"]),
    )
    result = serialize(db.execute("SELECT * FROM change_requests WHERE id = ?", (payload["id"],)).fetchone())
    db.commit()
    return result


def decide_request(db, user_id: str, request_id: str, body) -> dict:
    decision_id = str(body.decision_id)
    request_hash = ledger.digest({"type": "decision", "request_id": request_id, **body.model_dump(mode="json")})
    db.execute("BEGIN IMMEDIATE")
    row = db.execute("SELECT * FROM change_requests WHERE id = ? AND owner_id = ?", (request_id, user_id)).fetchone()
    if row is None:
        raise HTTPException(404, detail="Request not found")
    families.require_shared(db, user_id, row['family_id'])
    if row['withdrawn']:
        raise HTTPException(404, detail='Request not found')
    previous = ledger.previous_receipt(db, user_id, decision_id, request_hash, request_id=request_id)
    if previous is not None:
        db.commit()
        return previous
    before = ledger.owned_expense(db, user_id, row["expense_id"])
    if row["status"] == "stale":
        raise HTTPException(409, detail={"code": "request_stale"})
    if row["status"] != "pending":
        raise HTTPException(409, detail={"code": "request_decided"})
    if before["version"] != row["base_version"] or before["voided"] or before["scope"] != "shared":
        # Normally persisted by the mutation which advanced this parent version.
        db.execute("UPDATE change_requests SET status = 'stale' WHERE id = ?", (request_id,))
        db.commit()
        raise HTTPException(409, detail={"code": "request_stale"})
    expense = before
    if body.decision == "accept":
        data = json.loads(row["data_json"])
        ledger.ensure_refund_id(db, before["id"], row["kind"], data)
        expense = ledger.changed_expense(before, row["kind"], data)
        ledger.persist_expense(db, expense, exclude_request=request_id)
    db.execute("UPDATE change_requests SET status = ?, decided_at = ? WHERE id = ?", ("accepted" if body.decision == "accept" else "rejected", datetime.now(UTC).isoformat(), request_id))
    result = {"request": serialize(db.execute("SELECT * FROM change_requests WHERE id = ?", (request_id,)).fetchone()), "expense": expense}
    ledger.store_receipt(db, user_id, decision_id, request_hash, result)
    if body.decision == "accept":
        ledger.store_revision(db, user_id, decision_id, before, expense)
    db.commit()
    return result
