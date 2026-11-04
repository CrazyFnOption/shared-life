"""Category catalogs are owned by one person or one explicit family."""
import sqlite3
from fastapi import HTTPException
from server.accounting import families

DEFAULT_CATEGORIES = ('餐饮','买菜日用','购物','房租','水电网','交通','娱乐旅行','医疗','其他')


def catalog_names(db, scope, namespace):
    stored = {r['name'] for r in db.execute('SELECT name FROM categories WHERE scope=? AND namespace=?', (scope,namespace))}
    removed = {r['name'] for r in db.execute('SELECT name FROM category_tombstones WHERE scope=? AND namespace=?', (scope,namespace))}
    return (set(DEFAULT_CATEGORIES) | stored) - removed


def category_state(db, scope, namespace, name):
    if db.execute('SELECT 1 FROM category_tombstones WHERE scope=? AND namespace=? AND name=?', (scope,namespace,name)).fetchone():
        return 'deleted'
    return 'active' if name in catalog_names(db,scope,namespace) else 'missing'


def ensure_category(db: sqlite3.Connection, user_id: str, scope: str, name: str, *, restore_deleted=False) -> dict:
    namespace = families.require_scope(db,user_id,scope)
    return ensure_catalog_category(db,scope,namespace,name,restore_deleted=restore_deleted)


def ensure_catalog_category(db, scope, namespace, name, *, restore_deleted=False):
    """Internal persistence helper; its caller has authorized this exact namespace."""
    if category_state(db,scope,namespace,name) == 'deleted':
        if not restore_deleted:
            raise HTTPException(409,detail={'code':'category_deleted'})
        # Explicit recreation restores this string label. Historical drafts may
        # use it afterwards; until this action no ordinary ledger write can revive it.
        db.execute('DELETE FROM category_tombstones WHERE scope=? AND namespace=? AND name=?', (scope,namespace,name))
    db.execute("INSERT OR IGNORE INTO categories (scope,namespace,name) VALUES(?,?,?)", (scope,namespace,name))
    return {"scope":scope,"name":name}


def visible_categories(db: sqlite3.Connection, user_id: str) -> list[dict]:
    namespaces = [('private',user_id)] if getattr(db,'protocol',0)!=2 or db.source['scope']=='private' else []
    fid = families.current_id(db,user_id)
    if fid:
        namespaces.append(('shared',fid))
    return [{'scope':scope,'name':name} for scope,namespace in namespaces for name in sorted(catalog_names(db,scope,namespace))]
