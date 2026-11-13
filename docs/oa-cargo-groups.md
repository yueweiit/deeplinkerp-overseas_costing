# OA cargo groups

The logistics review groups rows within one approval table by code, specification, and unit.
Different suppliers, recipients, projects, or currencies remain separate.
Rows without a specification remain separate because their identities are not verified.
Each group keeps its source table, source row numbers, native row IDs, values, and source cells.

The review sums quantities and complete physical measurements from the source rows.
It does not sum unit prices.
It preserves purchase facts from other approvals.
Only verified initial logistics rows can supply combined purchase quantities and amounts.
Manual changes stop automatic grouping and keep the current rows for review.

The selected quantity and duplicate-row exclusion form one operation.
A partial field selection cannot remove the other source rows without adopting their combined quantity.
Duplicate ledger rows remain soft-excluded for traceability.
Repeated reviews read source totals instead of adding to previous totals.

Explicit OA fees use the existing fee parser and guarded fee writer.
Zero means a known amount, not a missing field.
The repair creates missing fee rows only.
It preserves existing amounts, manual decisions, disabled fees, and final settlement ownership.

The registered patch repairs approval `202611031848000325271` only.
It skips absent batches, confirmed versions, locked drafts, manual conflicts, and final settlement bindings.
It records the source fingerprint and repair totals on the version.
Repeated calls do not add rows or amounts.
The patch uses the migration transaction and creates no backup.

The incremental Containerfile accepts an exact `BASE_IMAGE` and the new `APP_REVISION`.
It keeps other app revisions, copies this app, and builds production assets.
Deploy the image to all app services, then run the standard site `migrate` command.
Do not use a deployment wrapper that creates backups without user approval.
