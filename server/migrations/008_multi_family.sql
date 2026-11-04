ALTER TABLE family_members RENAME TO family_members_v1;
CREATE TABLE family_members (
 user_id TEXT NOT NULL REFERENCES users(id), family_id TEXT NOT NULL REFERENCES families(id),
 relation_epoch INTEGER NOT NULL CHECK(typeof(relation_epoch)='integer' AND relation_epoch>0),
 active INTEGER NOT NULL CHECK(active IN (0,1)), joined_at TEXT, ended_at TEXT,
 PRIMARY KEY(user_id,family_id)
);
INSERT INTO family_members(user_id,family_id,relation_epoch,active)
 SELECT m.user_id,m.family_id,max(1,u.membership_version),1 FROM family_members_v1 m JOIN users u ON u.id=m.user_id;
DROP TABLE family_members_v1;
CREATE INDEX family_members_active_family ON family_members(family_id,active,user_id);
CREATE TABLE legacy_relation_epochs (
 user_id TEXT NOT NULL REFERENCES users(id), family_id TEXT NOT NULL REFERENCES families(id),
 membership_version INTEGER NOT NULL, relation_epoch INTEGER NOT NULL CHECK(typeof(relation_epoch)='integer' AND relation_epoch>0),
 PRIMARY KEY(user_id,family_id,membership_version)
);
INSERT INTO legacy_relation_epochs SELECT m.user_id,m.family_id,u.membership_version,m.relation_epoch FROM family_members m JOIN users u ON u.id=m.user_id;
ALTER TABLE users ADD COLUMN context_version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE users ADD COLUMN default_family_id TEXT REFERENCES families(id);
ALTER TABLE users ADD COLUMN default_version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE users ADD COLUMN default_invalidated_family_id TEXT;
UPDATE users SET default_family_id=(SELECT family_id FROM family_members WHERE user_id=users.id AND active=1);
ALTER TABLE families ADD COLUMN revision INTEGER NOT NULL DEFAULT 1;
ALTER TABLE operations ADD COLUMN protocol_version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE operations ADD COLUMN source_restore_id TEXT;
ALTER TABLE operations ADD COLUMN source_scope TEXT CHECK(source_scope IN ('private','shared'));
ALTER TABLE operations ADD COLUMN relation_epoch INTEGER CHECK(relation_epoch IS NULL OR (typeof(relation_epoch)='integer' AND relation_epoch>0));
ALTER TABLE operations ADD COLUMN source_status TEXT NOT NULL DEFAULT 'held' CHECK(source_status IN ('verified','held'));
ALTER TABLE family_invites ADD COLUMN inviter_epoch INTEGER;
ALTER TABLE family_actions ADD COLUMN protocol_version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE family_actions ADD COLUMN source_restore_id TEXT;
ALTER TABLE family_actions ADD COLUMN relation_epoch INTEGER;
ALTER TABLE change_requests ADD COLUMN source_restore_id TEXT;
ALTER TABLE change_requests ADD COLUMN requester_epoch INTEGER;
ALTER TABLE change_requests ADD COLUMN owner_epoch INTEGER;
ALTER TABLE change_requests ADD COLUMN source_status TEXT NOT NULL DEFAULT 'held' CHECK(source_status IN ('verified','held'));
ALTER TABLE category_changes ADD COLUMN relation_context_json TEXT;
ALTER TABLE category_changes ADD COLUMN source_status TEXT NOT NULL DEFAULT 'held' CHECK(source_status IN ('verified','held'));
ALTER TABLE notification_outbox ADD COLUMN source_scope TEXT NOT NULL DEFAULT 'unknown' CHECK(source_scope IN ('account','private','shared','unknown'));
ALTER TABLE notification_outbox ADD COLUMN family_id TEXT REFERENCES families(id);
ALTER TABLE notification_outbox ADD COLUMN relation_epoch INTEGER;
ALTER TABLE notification_outbox ADD COLUMN source_restore_id TEXT;
ALTER TABLE notification_outbox ADD COLUMN protocol_version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE notification_outbox ADD COLUMN source_status TEXT NOT NULL DEFAULT 'held' CHECK(source_status IN ('verified','held'));
