ALTER TABLE change_requests ADD COLUMN rejection_reason TEXT NOT NULL DEFAULT '';
ALTER TABLE category_changes ADD COLUMN rejection_reason TEXT NOT NULL DEFAULT '';
