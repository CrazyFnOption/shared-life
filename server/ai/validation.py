"""Semantic checks supplement schema preflight for backup and restore."""
import json
from uuid import UUID
from .models import DraftItem
from .budget import usage_cost

def validate_database(db):
    try:
        records={r['id']:dict(r) for r in db.execute('SELECT * FROM plan_records')}
        for row in db.execute('SELECT * FROM ai_costs'):
            prices=json.loads(row['pricing_json'])
            if set(prices)!={'input','cached','cache_write','output','web'} or any(type(v)!=int or v<=0 for v in prices.values()):raise ValueError()
            if row['status']=='settled':
                if usage_cost(json.loads(row['usage_json']),prices,web_allowed=True)!=row['actual_micro_usd']:raise ValueError()
            elif row['actual_micro_usd'] is not None:raise ValueError()
        for row in db.execute('SELECT * FROM ai_turns'):
            UUID(row['id']);origin=json.loads(row['source_json']);record=records[row['record_id']]
            if origin['user_id']!=row['user_id'] or origin['scope']!=record['scope'] or (origin['family_id'] if record['scope']=='shared' else origin['user_id'])!=record['namespace']:raise ValueError()
            if not isinstance(json.loads(row['chosen_json']),list):raise ValueError()
            if row['response_json']:json.loads(row['response_json'])
        for row in db.execute('SELECT * FROM ai_drafts'):
            turn=db.execute('SELECT record_id FROM ai_turns WHERE id=?',(row['turn_id'],)).fetchone()
            if not turn or turn['record_id']!=row['record_id']:raise ValueError()
            for item in json.loads(row['items_json']):
                data=dict(item);data.pop('adopted');data.pop('entity_id',None);DraftItem.model_validate(data)
        for row in db.execute('SELECT * FROM ai_adoptions'):
            origin=json.loads(row['source_json']);receipt=json.loads(row['receipt_json'])
            if origin['user_id']!=row['user_id'] or receipt['plan_id'] not in records:raise ValueError()
    except (ValueError,KeyError,TypeError) as error:raise ValueError('invalid_ai_database') from error
