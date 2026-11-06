CREATE TABLE ai_costs (
 id TEXT PRIMARY KEY, month TEXT NOT NULL, feature TEXT NOT NULL CHECK(feature IN ('planning','monthly')),
 status TEXT NOT NULL CHECK(status IN ('reserved','settled','unknown')),
 reserved_micro_usd INTEGER NOT NULL CHECK(typeof(reserved_micro_usd)='integer' AND reserved_micro_usd>=0),
 actual_micro_usd INTEGER CHECK(actual_micro_usd IS NULL OR (typeof(actual_micro_usd)='integer' AND actual_micro_usd>=0)),
 pricing_json TEXT NOT NULL, usage_json TEXT, created_at TEXT NOT NULL
);
CREATE INDEX ai_costs_month ON ai_costs(month,status);
CREATE TABLE ai_turns (
 id TEXT PRIMARY KEY, record_id TEXT NOT NULL REFERENCES plan_records(id), user_id TEXT NOT NULL REFERENCES users(id),
 op_id TEXT NOT NULL, request_hash TEXT NOT NULL, source_json TEXT NOT NULL, base_version INTEGER NOT NULL,
 content TEXT NOT NULL, chosen_json TEXT NOT NULL, allow_web INTEGER NOT NULL CHECK(allow_web IN (0,1)),
 status TEXT NOT NULL CHECK(status IN ('running','ready','failed','stale')), error TEXT,
 response_json TEXT, cost_id TEXT REFERENCES ai_costs(id), created_at TEXT NOT NULL, UNIQUE(user_id,op_id)
);
CREATE TABLE ai_drafts (
 id TEXT PRIMARY KEY, turn_id TEXT NOT NULL UNIQUE REFERENCES ai_turns(id), record_id TEXT NOT NULL REFERENCES plan_records(id),
 base_version INTEGER NOT NULL, items_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE ai_adoptions (
 user_id TEXT NOT NULL REFERENCES users(id), op_id TEXT NOT NULL, record_id TEXT NOT NULL REFERENCES plan_records(id),
 request_hash TEXT NOT NULL, source_json TEXT NOT NULL, receipt_json TEXT NOT NULL, PRIMARY KEY(user_id,op_id)
);
