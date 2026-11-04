"""Compare every original table/column/row without exporting any row values."""
import hashlib,json


def _hash_values(values):
    encoded=[]
    for value in values:
        if isinstance(value,bytes):encoded.append(['blob',len(value),hashlib.sha256(value).hexdigest()])
        else:encoded.append([type(value).__name__,value])
    return hashlib.sha256(json.dumps(encoded,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def rows(db,table,columns):
    names=','.join('"'+c.replace('"','""')+'"' for c in columns)
    return sorted(_hash_values(row) for row in db.execute('SELECT '+names+' FROM "'+table.replace('"','""')+'"'))


# These are the only original fields that the pre-family report migration
# translates. All remaining original columns stay inside the fixed row hash.
_REPORT_FIELDS={
    'reports':('id',('owner_id','data_version')),
    'report_tasks':('report_id',('target_version',)),
    'notification_outbox':('id',('data_version','dedup_key')),
}


def _report_proof(db,table,columns):
    key,fields=_REPORT_FIELDS[table]
    fixed=[column for column in columns if column not in fields]
    return {
        _hash_values([row[key]]):{
            'fixed':_hash_values(row[column] for column in fixed),
            'values':{field:_hash_values([row[field]]) for field in fields},
        }
        for row in db.execute('SELECT * FROM '+table)
    }


def capture(db):
    result={}
    for table in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall():
        name=table['name'];columns=[r['name'] for r in db.execute('PRAGMA table_info("'+name.replace('"','""')+'")')]
        result[name]={'columns':columns,'rows':rows(db,name,columns)}
    pre_family='schema_migrations' not in result or not db.execute("SELECT 1 FROM schema_migrations WHERE name='families-v1'").fetchone()
    if pre_family:
        for table in _REPORT_FIELDS.keys() & result.keys():
            result[table]['pre_family_report_proof']=_report_proof(db,table,result[table]['columns'])
    return result


def _verify_family_report_mapping(db,before):
    """Prove exact old/current snapshot translations; never exempt a table."""
    from server.automation import reports

    def checked_rows(table):
        baseline=before[table]
        proof=baseline['pre_family_report_proof']
        key,fields=_REPORT_FIELDS[table]
        fixed=[column for column in baseline['columns'] if column not in fields]
        actual=db.execute('SELECT * FROM '+table).fetchall()
        if len(actual)!=len(proof):raise ValueError('Migration changed original rows')
        for row in actual:
            original=proof.get(_hash_values([row[key]]))
            if not original or _hash_values(row[column] for column in fixed)!=original['fixed']:
                raise ValueError('Migration changed original rows')
            yield row,original['values']

    def same(value,expected):
        if _hash_values([value])!=expected:raise ValueError('Migration changed original rows')

    def family_id():
        users=db.execute("SELECT id,username,legacy_family_id FROM users WHERE username IN ('demo-a','demo-b')").fetchall()
        ids={row['id'] for row in users}
        families={row['legacy_family_id'] for row in users}
        if len(users)!=2 or len(families)!=1 or None in families:
            raise ValueError('Migration changed original rows')
        fid=next(iter(families))
        owner=next(row['id'] for row in users if row['username']=='demo-a')
        actual=db.execute('SELECT owner_id FROM families WHERE id=?',(fid,)).fetchone()
        members={row['user_id'] for row in db.execute('SELECT user_id FROM family_members WHERE family_id=? AND active=1',(fid,))}
        if not actual or actual['owner_id']!=owner or members!=ids:
            raise ValueError('Migration changed original rows')
        return fid

    # Snapshot membership is determined by the newly added family_id column.
    # Prove that complete mapping before treating the resulting snapshot as
    # evidence for either an old or a current report version.
    has_shared=db.execute("SELECT 1 FROM expenses WHERE scope='shared' LIMIT 1").fetchone() or db.execute("SELECT 1 FROM reports WHERE scope='shared' LIMIT 1").fetchone()
    expected_family=family_id() if has_shared else None
    for expense in db.execute('SELECT scope,family_id FROM expenses'):
        expected=expected_family if expense['scope']=='shared' else None
        if expense['family_id']!=expected:raise ValueError('Migration changed original rows')

    versions={}
    if 'reports' in before:
        for row,original in checked_rows('reports'):
            if row['scope']=='shared':same(row['owner_id'],_hash_values([expected_family]))
            else:same(row['owner_id'],original['owner_id'])
            snapshot=reports.snapshot(db,row['scope'],row['owner_id'],row['month'])
            legacy=[{key:value for key,value in expense.items() if key!='family_id'} for expense in snapshot]
            old_version=reports.version(legacy)
            current=reports.version(snapshot)
            translated=original['data_version']==_hash_values([old_version])
            same(row['data_version'],_hash_values([current]) if translated else original['data_version'])
            versions[row['id']]=(old_version,current,translated)
    if 'report_tasks' in before:
        for row,original in checked_rows('report_tasks'):
            old,current,_=versions[row['report_id']]
            expected=_hash_values([current]) if original['target_version']==_hash_values([old]) else original['target_version']
            same(row['target_version'],expected)
    if 'notification_outbox' in before:
        for row,original in checked_rows('notification_outbox'):
            target=versions.get(row['report_id'])
            translated=bool(target and target[2] and original['data_version']==_hash_values([target[0]]))
            same(row['data_version'],_hash_values([target[1]]) if translated else original['data_version'])
            expected=original['dedup_key']
            if translated and expected==_hash_values([f"report:{row['report_id']}:{target[0]}"]):
                expected=_hash_values([f"report:{row['report_id']}:{target[1]}"])
            same(row['dedup_key'],expected)


def verify(db,before):
    report_changes=False
    for table,baseline in before.items():
        after=rows(db,table,baseline['columns'])
        if table=='schema_migrations':
            if not set(baseline['rows'])<=set(after):raise ValueError('Migration changed original schema markers')
        elif after!=baseline['rows']:
            if 'pre_family_report_proof' not in baseline:raise ValueError('Migration changed original rows')
            report_changes=True
    if report_changes:
        _verify_family_report_mapping(db,before)


def summary(before):
    return {table:{'columns':len(values['columns']),'rows':len(values['rows']),'sha256':hashlib.sha256('\n'.join(values['rows']).encode()).hexdigest()} for table,values in before.items()}
