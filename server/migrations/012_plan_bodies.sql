ALTER TABLE plan_stages ADD COLUMN body_markdown TEXT NOT NULL DEFAULT '';
ALTER TABLE plan_records ADD COLUMN open_questions_markdown TEXT NOT NULL DEFAULT '';
