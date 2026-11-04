# Historical test source fixtures

These are readable snapshots of this project's historical implementation, used
only to create fictional databases and exercise real migration and restore
behavior. They are not supported deployment versions. Never run them against
production data or expose their services beyond loopback.

`manifest.json` records the original commit, Git blob identifier, SHA-256 and
mode of every included file. The helper in `server/tests/legacy_fixture.py`
verifies each file's SHA-256 before reading or copying it into a test workspace.
Tests do not need Git, hidden local candidates, or the original private history
to use these snapshots.

| Snapshot | Test purpose | Included source |
| --- | --- | --- |
| `351ffe7` | Original family migration, receipt replay and backup restore | Runtime `server/` and `scripts/` |
| `7262ef2` | Creator/admin ownership migration and restore | Runtime `server/` and `scripts/` |
| `298b15f` | Multi-family migration and old backup behavior | Runtime `server/` and `scripts/` |
| `0a56d79` | Schema 007 to full schema 9, preservation and atomic rollback | Runtime `server/` and `scripts/` |
| `8b88854` | Actual old database/restorer rejects unsupported schema 9 | Only `server/accounting/db.py` and `server/automation/backups.py` |

Original tests, project documents, deployment files, Git metadata, credentials,
databases, logs and backups are excluded. Snapshots intentionally retain their
original source bytes; changes to current behavior belong in current source,
not in these fixtures. A new historical baseline needs a separate snapshot and
manifest, with scope and hash review.

Copyright (c) 2026 CrazyFnOption. These project-owned historical sources are
licensed under the MIT License, as is the public project. Third-party imports
remain under their respective licenses; dependencies are not vendored here.
