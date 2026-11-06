"""Integer USD-micro accounting. Caller must hold BEGIN IMMEDIATE to reserve."""
from datetime import datetime, UTC, timedelta
from zoneinfo import ZoneInfo
import json
import os
from uuid import uuid4

ZONE=ZoneInfo('Asia/Shanghai')
LIMIT=50_000_000
MAX_INPUT_TOKENS=1_050_000  # Hard model context ceiling, also bounds PDF/image reservation.
MAX_OUTPUT_TOKENS=12_000
MAX_WEB_CALLS=2
REQUEST_TIMEOUT_SECONDS=300
PENDING_SECONDS=360  # Provider deadline plus time to settle/persist its result.

class BudgetError(Exception):
    def __init__(self,code):self.code=code;super().__init__(code)

def pricing():
    # USD micros per million tokens. Standard published rates, including long context.
    # Explicit deployment configuration can update rates without exposing a key.
    defaults={'input':2_000_000,'cached':100_000,'cache_write':2_500_000,'output':10_000_000,'web':10_000}
    result={k:int(os.getenv('SHARED_EXPENSE_AI_PRICE_'+k.upper(),v)) for k,v in defaults.items()}
    if any(type(v)!=int or v<=0 for v in result.values()):raise BudgetError('ai_pricing_unconfigured')
    return result

def ceildiv(value,denominator):return (value+denominator-1)//denominator

def reservation_amount(prices,web=False):
    # Reserve the entire context at the highest long-context input/cache-write rate.
    # This conservative ceiling avoids pretending compressed PDF bytes bound tokens.
    return ceildiv(MAX_INPUT_TOKENS*max(prices['input'],prices['cache_write'])*2+MAX_OUTPUT_TOKENS*prices['output']*3//2,1_000_000)+(MAX_WEB_CALLS*prices['web'] if web else 0)

def snapshot(db,now=None):
    month=(now or datetime.now(UTC)).astimezone(ZONE).strftime('%Y-%m')
    row=db.execute("SELECT COALESCE(sum(CASE WHEN status='settled' THEN actual_micro_usd ELSE 0 END),0),COALESCE(sum(CASE WHEN status='reserved' THEN reserved_micro_usd ELSE 0 END),0),COALESCE(sum(CASE WHEN status='unknown' THEN reserved_micro_usd ELSE 0 END),0) FROM ai_costs WHERE month=?",(month,)).fetchone()
    confirmed,reserved,unknown=row
    committed=confirmed+reserved+unknown;remaining=max(0,LIMIT-committed)
    return {'month':month,'limit_micro_usd':LIMIT,'confirmed_micro_usd':confirmed,'reserved_micro_usd':reserved,
            'unknown_micro_usd':unknown,'has_unknown':unknown>0,'committed_micro_usd':committed,
            'remaining_micro_usd':remaining,'blocked':remaining==0}

def reserve(db,feature,*,web=False,now=None):
    if not db.in_transaction:raise RuntimeError('AI reserve requires a write transaction')
    moment=now or datetime.now(UTC)
    db.execute("UPDATE ai_costs SET status='unknown' WHERE status='reserved' AND created_at<?",((moment-timedelta(seconds=PENDING_SECONDS)).astimezone(UTC).isoformat(),))
    prices=pricing();state=snapshot(db,now)
    amount=reservation_amount(prices,web)
    if amount>state['remaining_micro_usd']:raise BudgetError('ai_budget_exceeded')
    identity=str(uuid4())
    db.execute("INSERT INTO ai_costs(id,month,feature,status,reserved_micro_usd,pricing_json,created_at) VALUES(?,?,?,'reserved',?,?,?)",(identity,state['month'],feature,amount,json.dumps(prices),(now or datetime.now(UTC)).isoformat()))
    return identity

def usage_cost(usage,prices,*,web_allowed):
    if not isinstance(usage,dict):raise ValueError('missing_usage')
    inp,out=usage.get('input_tokens'),usage.get('output_tokens')
    details=usage.get('input_tokens_details')
    if details is None:details={}
    if not isinstance(details,dict):raise ValueError('invalid_usage')
    # Responses uses cache_write_tokens; retain the earlier injected-fixture alias.
    # Equal aliases describe the same tokens, never add them; disagreement is unknown.
    if 'cache_write_tokens' in details and 'cache_creation_tokens' in details:
        if type(details['cache_write_tokens'])!=int or type(details['cache_creation_tokens'])!=int or details['cache_write_tokens']!=details['cache_creation_tokens']:raise ValueError('conflicting_cache_usage')
    cached=details.get('cached_tokens',0)
    writes=details.get('cache_write_tokens',details.get('cache_creation_tokens',0))
    web=usage.get('web_search_calls',0)
    if any(type(v)!=int or v<0 for v in (inp,out,cached,writes,web)):raise ValueError('invalid_usage')
    if inp>MAX_INPUT_TOKENS or out>MAX_OUTPUT_TOKENS or cached+writes>inp or web>(MAX_WEB_CALLS if web_allowed else 0):raise ValueError('unbounded_usage')
    multiplier=2 if inp>272_000 else 1
    numerator=((inp-cached-writes)*prices['input']+cached*prices['cached']+writes*prices['cache_write'])*multiplier
    output=out*prices['output']
    if multiplier==2:output=ceildiv(output*3,2)
    return ceildiv(numerator+output,1_000_000)+web*prices['web']

def settle(db,identity,usage,*,web_allowed=False):
    row=db.execute('SELECT * FROM ai_costs WHERE id=?',(identity,)).fetchone()
    if not row or row['status']!='reserved':return False
    try:
        amount=usage_cost(usage,json.loads(row['pricing_json']),web_allowed=web_allowed)
        if amount>row['reserved_micro_usd']:raise ValueError('reservation_exceeded')
    except (ValueError,TypeError,KeyError):
        db.execute("UPDATE ai_costs SET status='unknown' WHERE id=?",(identity,));return False
    db.execute("UPDATE ai_costs SET status='settled',actual_micro_usd=?,usage_json=? WHERE id=?",(amount,json.dumps(usage),identity));return True

def unknown(db,identity):db.execute("UPDATE ai_costs SET status='unknown' WHERE id=? AND status='reserved'",(identity,))
