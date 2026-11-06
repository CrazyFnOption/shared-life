"""Same-origin HTTP API for the phase-one ledger."""

from datetime import UTC, datetime
import hashlib
from pathlib import Path
import secrets
import sqlite3
import time
from typing import Annotated, Literal
from contextlib import asynccontextmanager
import asyncio
import threading
from uuid import UUID, uuid4

from argon2.exceptions import VerifyMismatchError, InvalidHashError, VerificationError
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler

from server.accounting.db import connect, initialize_database, password_hasher
from server.accounting import ledger, families, category_changes, requests as change_requests
from server.api.models import Login, ChangePassword, Register, CreateFamily, InviteFamily, TransferFamilyOwner, FamilyMutation, DefaultScope, Operation, Category, ChangeRequest, Decision, CategoryChangePreview, CategoryChangeSubmit
from server.accounting.categories import ensure_category, visible_categories
from server.accounting import images
from server.automation import reports, api as automation_api
from server.automation.worker import Worker


SESSION_COOKIE = "shared_expense_session"
SESSION_SECONDS = 30 * 24 * 60 * 60


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_app(
    db_path: str | Path,
    *,
    allowed_origins: list[str] | None = None,
    secure_cookies: bool = False,
    worker: Worker | None = None,
    start_worker: bool = False,
    planning_provider=None,
) -> FastAPI:
    initialize_database(db_path)
    from server.automation.backups import validate_database
    with connect(db_path,read_only=True) as startup_db:
        validate_database(startup_db)
    worker = worker or Worker(db_path)
    @asynccontextmanager
    async def lifespan(app):
        thread = None
        if start_worker:
            thread = threading.Thread(target=worker.run, name="shared-expense-worker", daemon=True)
            thread.start()
        try:
            yield
        finally:
            worker.stop_event.set()
            if thread:
                await asyncio.to_thread(thread.join)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.worker = worker
    from server.automation.providers import configured_provider
    app.state.planning_provider=planning_provider if planning_provider is not None else configured_provider('planning')
    origins = set(allowed_origins if allowed_origins is not None else [
        "http://127.0.0.1:8765", "http://localhost:8765"
    ])

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError):
        if request.url.path in {"/api/auth/password", "/api/auth/register", "/api/auth/login"}:
            # FastAPI's default validation response includes the rejected input.
            return JSONResponse(status_code=422, content={"detail": {"code": "invalid_password_request"}})
        return await request_validation_exception_handler(request, error)

    @app.exception_handler(sqlite3.Error)
    async def database_failure(request: Request, error: sqlite3.Error):
        # Do not expose SQL, receipt contents or private fields in a failed response.
        return JSONResponse(status_code=500, content={"detail": "Database operation failed"})

    @app.middleware("http")
    async def protect_api(request: Request, call_next):
        if not request.url.path.startswith("/api/"):
            return await call_next(request)
        api_path = request.url.path.replace("/api/v2/","/api/",1)
        origin = request.headers.get("origin")
        if origin is not None and origin not in origins:
            response = JSONResponse(status_code=403, content={"detail": "Origin not allowed"})
        elif request.method not in {"GET", "HEAD", "OPTIONS"} and request.headers.get("x-app-request") != "shared-expense":
            response = JSONResponse(status_code=403, content={"detail": "X-App-Request required"})
        elif request.method == "POST" and request.url.path != "/api/auth/logout" and request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            response = JSONResponse(status_code=415, content={"detail": "application/json required"})
        elif request.method in {"POST", "PUT", "DELETE"} and (api_path in {"/api/settings", "/api/push-subscriptions", "/api/account/default-scope"} or api_path.endswith("/retry")) and request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            response = JSONResponse(status_code=415, content={"detail": "application/json required"})
        else:
            response = await call_next(request)
        captured = getattr(request.state, 'source', None)
        if captured:
            response.headers.update({'X-App-Protocol':'2','X-Source-User':captured['user_id'],'X-Restore-Id':captured['restore_id']})
            if 'scope' in captured:
                response.headers.update({'X-Scope':captured['scope'],'X-Family-Id':captured['family_id'] or 'none','X-Relation-Epoch':str(captured['relation_epoch'])})
        response.headers["Cache-Control"] = "no-store"
        return response

    def database():
        with connect(db_path) as db:
            db.create_function("app_now", 0, lambda: worker.clock().timestamp())
            yield db

    Database = Annotated[sqlite3.Connection, Depends(database)]

    def authenticated(request: Request, db: Database) -> dict[str, str]:
        token = request.cookies.get(SESSION_COOKIE)
        row = db.execute(
            """SELECT users.id, users.display_name, users.username FROM sessions
               JOIN users ON users.id = sessions.user_id
               WHERE token_hash = ? AND expires_at > ?""",
            (token_hash(token) if token else "", int(time.time())),
        ).fetchone()
        if row is None:
            raise HTTPException(401, detail="Login required")
        db.auth_session = (token_hash(token),row["id"])
        return dict(row)

    def expected_user(request: Request, db: Database, user: Annotated[dict, Depends(authenticated)]) -> dict:
        if request.headers.get('x-expected-user') != user['id']:
            raise HTTPException(409, detail={'code':'account_changed'})
        path = request.url.path
        v2 = path.startswith('/api/v2/') or path in {'/api/context','/api/account/default-scope'}
        requested = request.headers.get('x-app-protocol')
        if v2 and requested != '2':
            raise HTTPException(426, detail={'code':'client_upgrade_required','min_protocol':2})
        if requested not in (None,'1','2'):
            raise HTTPException(426, detail={'code':'client_upgrade_required','min_protocol':2})
        auth_only = path in {'/api/auth/logout','/api/auth/password'}
        if requested == '2' and not v2 and not auth_only:
            raise HTTPException(426, detail={'code':'protocol_path_required','min_protocol':2})
        db.protocol = 2 if v2 else 1
        current = reports.state(db)
        supplied = request.headers.get('x-restore-id')
        needs_restore = v2 and path != '/api/context'
        if needs_restore or request.method not in {'GET','HEAD','OPTIONS'}:
            if (supplied is not None and supplied != current['restore_id']) or (needs_restore and supplied != current['restore_id']) or (current['restored_at'] and supplied != current['restore_id']):
                raise HTTPException(409, detail={'code':'restore_changed','restore_id':current['restore_id']})
            if supplied is not None:
                db.expected_restore = supplied
        if v2:
            request.state.source = {'user_id':user['id'],'restore_id':current['restore_id']}
            account_domain = path in {'/api/context','/api/account/default-scope','/api/v2/families','/api/v2/push-subscriptions'} or (path == '/api/v2/family-invites' and request.method=='GET') or (path.startswith('/api/v2/family-invites/') and path.rsplit('/',1)[-1] in {'accept','reject'})
            if not account_domain:
                scope = request.headers.get('x-scope')
                fid = request.headers.get('x-family-id')
                raw_epoch = request.headers.get('x-relation-epoch')
                try:
                    epoch = int(raw_epoch) if raw_epoch is not None and raw_epoch.isdecimal() else -1
                    valid = scope in {'private','shared'} and 0 <= epoch <= families.MAX_COUNTER
                    if scope == 'private':
                        valid = valid and fid == 'none' and epoch == 0
                    else:
                        valid = valid and fid is not None and str(UUID(fid)) == fid and epoch > 0
                except (ValueError,TypeError):
                    valid = False
                if not valid:
                    raise HTTPException(409, detail={'code':'source_context_required'})
                db.source = {'protocol_version':2,'user_id':user['id'],'restore_id':current['restore_id'],'scope':scope,'family_id':None if fid=='none' else fid,'relation_epoch':epoch}
                request.state.source = db.source
        elif not auth_only:
            db.family_headers = (request.headers.get('x-family-id'),request.headers.get('x-membership-version'))
            db.legacy_discovery = path == '/api/sync'
        if not auth_only:
            db.context_user = user['id']
            families.check_context(db,user['id'])
        return user

    ExpectedUser = Annotated[dict, Depends(expected_user)]

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.post("/api/auth/login")
    def login(body: Login, request: Request, response: Response, db: Database):
        row = db.execute("SELECT * FROM users WHERE username = ?", (body.username,)).fetchone()
        if row is None:
            raise HTTPException(401, detail="Invalid username or password")
        try:
            password_hasher.verify(row["password_hash"], body.password)
        except (VerifyMismatchError, InvalidHashError, VerificationError):
            raise HTTPException(401, detail="Invalid username or password") from None
        token = secrets.token_urlsafe(32)
        db.execute("BEGIN IMMEDIATE")
        # Verification is expensive, so keep it outside the write lock. Under
        # that lock, reject a password changed since the verified snapshot.
        current = db.execute("SELECT password_hash FROM users WHERE id = ?", (row["id"],)).fetchone()
        if current is None or current["password_hash"] != row["password_hash"]:
            raise HTTPException(401, detail="Invalid username or password")
        old_token = request.cookies.get(SESSION_COOKIE)
        if old_token:
            db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash(old_token),))
        db.execute(
            "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
            (token_hash(token), row["id"], int(time.time()) + SESSION_SECONDS),
        )
        db.commit()
        response.set_cookie(
            SESSION_COOKIE, token, max_age=SESSION_SECONDS, httponly=True,
            secure=secure_cookies, samesite="strict", path="/",
        )
        return {"user": {"id": row["id"], "display_name": row["display_name"], "username": row["username"]}}

    @app.post('/api/auth/register')
    def register(body: Register, request: Request, response: Response, db: Database):
        encoded = password_hasher.hash(body.password.get_secret_value())
        user = {'id':str(uuid4()),'username':body.username,'display_name':body.display_name}
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT 1 FROM users WHERE username=?',(body.username,)).fetchone():
            raise HTTPException(409,detail={'code':'username_taken'})
        db.execute('INSERT INTO users(id,username,display_name,password_hash) VALUES(?,?,?,?)', (user['id'],body.username,body.display_name,encoded))
        old = request.cookies.get(SESSION_COOKIE)
        if old:
            db.execute('DELETE FROM sessions WHERE token_hash=?',(token_hash(old),))
        token = secrets.token_urlsafe(32)
        db.execute('INSERT INTO sessions VALUES(?,?,?)',(token_hash(token),user['id'],int(time.time())+SESSION_SECONDS))
        db.commit()
        response.set_cookie(SESSION_COOKIE,token,max_age=SESSION_SECONDS,httponly=True,secure=secure_cookies,samesite='strict',path='/')
        return {'user':user}

    @app.get('/api/context')
    def account_context(db: Database, user: ExpectedUser):
        db.execute('BEGIN')
        result = families.metadata(db,user['id'])
        db.commit()
        return result

    @app.put('/api/account/default-scope')
    def account_default(body: DefaultScope, db: Database, user: ExpectedUser):
        return families.set_default(db,user['id'],body)

    @app.get('/api/family')
    def family_context(db: Database,user: ExpectedUser):
        db.execute('BEGIN')
        result=families.context(db,user['id']);db.commit();return result

    @app.post('/api/families')
    def create_family(body: CreateFamily,db: Database,user: ExpectedUser):
        return families.create(db,user['id'],body)

    @app.get('/api/family-invites')
    def family_invites(db: Database,user: ExpectedUser):
        db.execute('BEGIN')
        result=families.list_invites(db,user['id']);db.commit();return result

    @app.post('/api/family-invites')
    def invite_family(body: InviteFamily,db: Database,user: ExpectedUser):
        return families.invite(db,user['id'],body)

    @app.post('/api/family-invites/{invite_id}/{action}')
    def decide_invite(invite_id: UUID,action: Literal['accept','reject','revoke'],body: automation_api.Empty,db: Database,user: ExpectedUser):
        return families.decide(db,user['id'],str(invite_id),action)

    @app.post('/api/families/{family_id}/leave')
    def leave_family(family_id: UUID,body: FamilyMutation,db: Database,user: ExpectedUser):
        return families.leave(db,user['id'],str(family_id),body)

    @app.post('/api/families/{family_id}/members/{target_id}/remove')
    def remove_family_member(family_id: UUID,target_id: UUID,body: FamilyMutation,db: Database,user: ExpectedUser):
        return families.remove(db,user['id'],str(family_id),str(target_id),body)

    @app.post('/api/families/{family_id}/owner')
    def transfer_family_owner(family_id: UUID,body: TransferFamilyOwner,db: Database,user: ExpectedUser):
        return families.transfer_owner(db,user['id'],str(family_id),str(body.user_id),body=body)

    @app.post('/api/families/{family_id}/transfer-and-leave')
    def transfer_and_leave_family(family_id: UUID,body: TransferFamilyOwner,db: Database,user: ExpectedUser):
        return families.transfer_owner(db,user['id'],str(family_id),str(body.user_id),leave_after=True,body=body)

    @app.post('/api/families/{family_id}/dissolve')
    def dissolve_family(family_id: UUID,body: FamilyMutation,db: Database,user: ExpectedUser):
        return families.dissolve(db,user['id'],str(family_id),body)

    @app.get("/api/auth/me")
    def me(user: Annotated[dict, Depends(authenticated)]):
        return {"user": user}

    @app.post("/api/auth/password", status_code=204)
    def change_password(body: ChangePassword, request: Request, db: Database, user: ExpectedUser):
        new_password = body.new_password.get_secret_value()
        if not 8 <= len(new_password) <= 128:
            raise HTTPException(400, detail={"code": "invalid_password_length"})
        db.execute("BEGIN IMMEDIATE")
        current_token = token_hash(request.cookies[SESSION_COOKIE])
        # Recheck under the write lock: a concurrent password change may have revoked us.
        if db.execute("SELECT 1 FROM sessions WHERE token_hash = ? AND user_id = ? AND expires_at > ?",
                      (current_token, user["id"], int(time.time()))).fetchone() is None:
            raise HTTPException(401, detail="Login required")
        row = db.execute("SELECT password_hash FROM users WHERE id = ?", (user["id"],)).fetchone()
        try:
            password_hasher.verify(row["password_hash"], body.old_password.get_secret_value())
        except (VerifyMismatchError, InvalidHashError, VerificationError):
            raise HTTPException(400, detail={"code": "incorrect_password"}) from None
        db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hasher.hash(new_password), user["id"]))
        db.execute("DELETE FROM sessions WHERE user_id = ? AND token_hash != ?", (user["id"], current_token))
        db.commit()
        return Response(status_code=204)

    @app.post("/api/auth/logout", status_code=204)
    def logout(request: Request, db: Database, user: ExpectedUser):
        db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash(request.cookies[SESSION_COOKIE]),))
        response = Response(status_code=204)
        response.delete_cookie(SESSION_COOKIE, path="/", secure=secure_cookies, httponly=True, samesite="strict")
        return response

    @app.get("/api/sync")
    def sync(db: Database, user: ExpectedUser):
        db.execute("BEGIN IMMEDIATE")
        synced_at = worker.clock().isoformat()
        db.execute("INSERT INTO user_sync(user_id,last_synced_at) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET last_synced_at=excluded.last_synced_at", (user['id'],synced_at))
        family = families.context(db,user['id']) if db.protocol!=2 else {}
        fid = families.current_id(db,user['id'])
        selected = db.source['scope'] if db.protocol==2 else None
        users = [dict(row) for row in db.execute("""SELECT u.id,u.display_name,last_synced_at FROM users u LEFT JOIN user_sync ON user_sync.user_id=u.id WHERE u.id=? OR u.id IN (SELECT user_id FROM family_members WHERE family_id=? AND active=1) OR u.id IN (SELECT owner_id FROM expenses WHERE scope='shared' AND family_id=? AND voided=0) ORDER BY u.id""", (user['id'],fid,fid))]
        expenses = []
        for row in db.execute("SELECT id FROM expenses WHERE (scope='private' AND owner_id=?) OR (scope='shared' AND family_id=? AND (owner_id=? OR voided=0)) ORDER BY occurred_at,id", (user['id'],fid,user['id'])):
            expense = ledger.read_expense(db, row["id"])
            expenses.append(expense if expense["owner_id"] == user["id"] else ledger.shared_expense(expense))
        if selected is not None:
            expenses = [e for e in expenses if e['scope']==selected]
        if selected == 'private':
            users = [r for r in users if r['id']==user['id']]
        requests = change_requests.visible_requests(db, user["id"])
        withdrawn_request_ids = [row["id"] for row in db.execute(
            "SELECT id FROM change_requests WHERE requester_id = ? AND withdrawn = 1 AND family_id=? ORDER BY id",
            (user["id"],fid),
        )]
        categories = visible_categories(db, user["id"])
        category_change_list = category_changes.visible_changes(db,user['id'])
        image_list = images.visible_images(db, user["id"])
        restore = reports.state(db)
        db.commit()
        return {**family, **({"source":db.source,"context_version":families.metadata(db,user["id"])["context_version"]} if db.protocol==2 else {}), "restore_id": restore["restore_id"], "restored_at": restore["restored_at"], "user": user, "users": users, "expenses": expenses, "synced_at": synced_at, "categories": categories, "category_changes":category_change_list, "images": image_list, "requests": requests, "withdrawn_request_ids": withdrawn_request_ids}

    @app.get("/api/expenses/{expense_id}")
    def get_expense(expense_id: UUID, db: Database, user: ExpectedUser):
        db.execute("BEGIN")
        expense = ledger.read_expense(db, str(expense_id))
        if not ledger.can_read(expense, user["id"], db):
            raise HTTPException(404, detail="Expense not found")
        db.commit()
        return expense if expense["owner_id"] == user["id"] else ledger.shared_expense(expense)

    @app.get("/api/expenses/{expense_id}/history")
    def get_history(expense_id: UUID, db: Database, user: ExpectedUser):
        db.execute("BEGIN")
        revisions = ledger.history(db, user["id"], str(expense_id))
        result = {"revisions":revisions}
        if db.protocol==2:
            dependencies = {e['family_id'] for r in revisions for e in (r['before'],r['after']) if e and e['scope']=='shared'}
            result['source_dependencies'] = [{'family_id':fid,'relation_epoch':families.relation(db,user['id'],fid)['relation_epoch']} for fid in sorted(dependencies)]
        db.commit()
        return result

    @app.get("/api/change-requests")
    def get_requests(db: Database, user: ExpectedUser):
        db.execute("BEGIN")
        result={"requests": change_requests.visible_requests(db, user["id"])}
        db.commit();return result

    @app.post("/api/change-requests")
    def add_request(body: ChangeRequest, db: Database, user: ExpectedUser):
        return change_requests.submit_request(db, user["id"], body)

    @app.post("/api/change-requests/{request_id}/decision")
    def decide_request(request_id: UUID, body: Decision, db: Database, user: ExpectedUser):
        return change_requests.decide_request(db, user["id"], str(request_id), body)

    @app.post("/api/operations")
    def operations(body: Operation, db: Database, user: ExpectedUser):
        return ledger.submit_operation(db, user["id"], body)

    @app.post("/api/categories")
    def add_category(body: Category, db: Database, user: ExpectedUser):
        db.execute("BEGIN IMMEDIATE")
        families.require_scope(db,user["id"],body.scope)
        result=ensure_category(db, user["id"], body.scope, body.name, restore_deleted=body.restore_deleted)
        db.commit();return result

    @app.post('/api/category-changes/preview')
    def preview_category_change(body: CategoryChangePreview,db: Database,user: ExpectedUser):
        return category_changes.preview(db,user['id'],body)

    @app.post('/api/category-changes')
    def submit_category_change(body: CategoryChangeSubmit,db: Database,user: ExpectedUser):
        return category_changes.submit(db,user['id'],body)

    @app.get('/api/category-changes')
    def list_category_changes(db: Database,user: ExpectedUser):
        db.execute('BEGIN IMMEDIATE')
        result = {'changes':category_changes.visible_changes(db,user['id'])}
        db.commit()
        return result

    @app.post('/api/category-changes/{change_id}/decision')
    def decide_category_change(change_id: UUID,body: Decision,db: Database,user: ExpectedUser):
        return category_changes.decide(db,user['id'],str(change_id),body)

    @app.put("/api/expenses/{expense_id}/images/{image_id}")
    async def put_image(expense_id: UUID, image_id: UUID, request: Request, db: Database, user: ExpectedUser):
        images.require_expense(db, user["id"], str(expense_id), write=True)
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > images.MAX_IMAGE_BYTES:
                raise HTTPException(413, detail={"code": "image_too_large"})
            data.extend(chunk)
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        return images.put_image(db, user["id"], str(expense_id), str(image_id), bytes(data), content_type)

    @app.get("/api/expenses/{expense_id}/images/{image_id}")
    def get_image(expense_id: UUID, image_id: UUID, db: Database, user: ExpectedUser):
        row = images.get_image(db, user["id"], str(expense_id), str(image_id))
        return Response(content=row["data"], media_type=row["mime_type"],
                        headers={"X-Content-Type-Options": "nosniff"})

    @app.delete("/api/expenses/{expense_id}/images/{image_id}", status_code=204)
    def delete_image(expense_id: UUID, image_id: UUID, db: Database, user: ExpectedUser):
        images.delete_image(db, user["id"], str(expense_id), str(image_id))
        return Response(status_code=204)

    @app.get("/api/settings")
    def settings(db: Database, user: ExpectedUser, scope: Literal['private','shared']='shared'):
        db.execute('BEGIN')
        result=automation_api.get_settings(db, user['id'], worker, scope)
        db.commit();return result

    @app.put("/api/settings")
    def update_settings(body: automation_api.Settings, db: Database, user: ExpectedUser, scope: Literal['private','shared']='shared'):
        return automation_api.set_settings(db, user['id'], body, worker, scope)

    @app.get("/api/reports")
    def report_list(db: Database, user: ExpectedUser, scope: Literal['shared','private']='shared'):
        db.execute('BEGIN')
        if db.protocol==2:
            families.require_scope(db,user['id'],scope)
        rows = db.execute('SELECT * FROM reports WHERE scope=? AND owner_id=? ORDER BY month DESC,id', (scope, families.current_id(db,user['id']) if scope=='shared' else user['id']))
        result = {'reports': [reports.read(db,dict(row)) for row in rows]}
        db.commit()
        return result

    @app.get("/api/reports/{report_id}")
    def report_detail(report_id: UUID, db: Database, user: ExpectedUser):
        db.execute('BEGIN')
        result = reports.read(db,reports.authorize(db,str(report_id),user['id']),detail=True)
        db.commit()
        return result

    @app.post("/api/reports/{report_id}/retry")
    def retry_report(report_id: UUID, body: automation_api.Empty, db: Database, user: ExpectedUser):
        db.execute('BEGIN IMMEDIATE')
        row=reports.authorize(db,str(report_id),user['id'])
        if row['scope']=='shared':
            families.require_shared(db,user['id'],row['owner_id'])
        current=reports.version(reports.snapshot(db,row['scope'],row['owner_id'],row['month']))
        reports.queue(db,row['id'],current,when=worker.clock().timestamp())
        db.execute("UPDATE reports SET status='pending',content_json=NULL,error=NULL,data_version=? WHERE id=?",(current,row['id']))
        result=reports.read(db,dict(db.execute('SELECT * FROM reports WHERE id=?',(row['id'],)).fetchone()),detail=True)
        db.commit();return result

    @app.post("/api/push-subscriptions")
    def subscribe(body: automation_api.Subscription, db: Database, user: ExpectedUser):
        import json
        db.execute('BEGIN IMMEDIATE')
        db.execute('INSERT INTO push_subscriptions(owner_id,endpoint,keys_json) VALUES(?,?,?) ON CONFLICT(owner_id,endpoint) DO UPDATE SET keys_json=excluded.keys_json,active=1',(user['id'],body.endpoint,json.dumps(body.keys.model_dump())))
        db.commit()
        return {'status':'subscribed'}

    @app.delete("/api/push-subscriptions", status_code=204)
    def unsubscribe(body: automation_api.Endpoint, db: Database, user: ExpectedUser):
        db.execute('BEGIN IMMEDIATE')
        db.execute('DELETE FROM push_subscriptions WHERE owner_id=? AND endpoint=?',(user['id'],body.endpoint))
        db.commit()
        return Response(status_code=204)

    @app.get("/api/export")
    def export(db: Database, user: ExpectedUser, format: Literal['json','csv']='json', scope: Literal['shared','private','all']='all'):
        content=automation_api.export(db,user['id'],scope,format)
        return Response(content,media_type='application/json' if format=='json' else 'text/csv; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="shared-expense.{format}"'})

    # Versioned paths reuse the same handlers; the dependency enforces protocol 2.
    from fastapi.routing import APIRoute
    for route in list(app.routes):
        if isinstance(route,APIRoute) and route.path.startswith('/api/') and not route.path.startswith('/api/auth/') and route.path not in {'/api/health','/api/context','/api/account/default-scope'}:
            app.add_api_route('/api/v2/'+route.path[len('/api/'):],route.endpoint,methods=sorted(route.methods),status_code=route.status_code)
    from server.memos.api import register_routes as register_memos
    register_memos(app,Database,ExpectedUser)
    from server.plans.routes import register_routes as register_plans
    register_plans(app,Database,ExpectedUser)
    from server.ai.routes import register_routes as register_ai
    register_ai(app,Database,ExpectedUser,db_path)
    return app
