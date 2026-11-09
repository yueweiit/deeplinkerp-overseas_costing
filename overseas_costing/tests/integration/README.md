# Local settlement integration checks

`run_settlement_frappe_integration.py` exercises the settlement backend against real Frappe and MariaDB. It refuses to connect unless the site is `settlement-test.local` and its configured database host is `oc-settlement-test-db`.

Prerequisites: an isolated Frappe test site with the current production-based Overseas Costing app installed and a complete `bench migrate` applied (including Fee Evidence, Fee SKU Component, material AI and packing DocTypes); invoking only the settlement schema installer is insufficient. Also required: the current app package copied to `/tmp/settlement-code/overseas_costing`; no OA, object storage or ERP connections are required. The script reads the site's existing configuration through Frappe and never prints credentials. It creates synthetic `LOCAL-*` test records with a unique run identifier and leaves them on the disposable site for inspection.

From the costing worktree, using the existing local backend container:

```sh
docker cp overseas_costing overseas-cost-local-backend-1:/tmp/settlement-code/
docker cp overseas_costing/tests/integration/run_settlement_frappe_integration.py overseas-cost-local-backend-1:/tmp/run_settlement_frappe_integration.py
docker exec -e PYTHONPATH=/tmp/settlement-code -w /home/frappe/frappe-bench/sites overseas-cost-local-backend-1 /home/frappe/frappe-bench/env/bin/python /tmp/run_settlement_frappe_integration.py
```

Successful runs print one JSON result per check and finish with `integration_complete`. Coverage includes idempotent schema installation and unique indexes, real document persistence, zero final fees, final shipment quantity overlays while preserving independent purchase values and raw packing, packing review, frozen confirmed versions and idempotent adjustment drafts, application/rebind rollback after injected failures, and concurrent confirmation through independent database connections on both sides of the one-to-one association.

This is an opt-in integration script, not part of the default pytest run. Do not change the site or database-host guard to target production or an existing demo site.

## Frappe document and precision checks

`run_settlement_document_frappe_integration.py` uses the same site/database guard and creates distinct `LOCAL-DOC-*` fixtures plus one local user with the costing role. Welcome email is disabled. After copying the current package as above, run:

```sh
docker exec -e PYTHONPATH=/tmp/settlement-code -w /home/frappe/frappe-bench/sites overseas-cost-local-backend-1 /home/frappe/frappe-bench/env/bin/python /tmp/settlement-code/overseas_costing/tests/integration/run_settlement_document_frappe_integration.py
```

The script tests actual Frappe File and Attachment insertion, private file access for the costing role and denial to Guest, physical packing fill precedence, separate shipped/purchase quantities, preservation of independent purchase values, item-review acknowledgement clearing document blockers, frozen adjustment/history preservation, explicit retirement, queued retry, and exact six-place fee persistence including stable remainder after reordered detail input and conserved RMB/MXN allocation totals across three stored item rows. Fixtures provide explicit ACTUAL logical fee identities and independent purchase price, currency, unit and document evidence, as required by the production trial engine. A previously unattached cached File may be adopted by Frappe's Attach-field hook; already-owned historical File links must remain unchanged when a new version reuses the same URL.

Parsed packing documents and their synthetic cached bytes are fixtures: this script does not validate XLSX extraction or contact object storage. The scheduled `resume_pending` queue inventory, cursor IDs and enable-control record are restricted to the new fixture using a Store wrapper, so concurrent browser QA batches are untouched. Target reads/writes, row locks, `apply_source`, File controllers and calculations all use real Frappe/MariaDB. No production implementation is patched by the test. A successful run finishes with `document_integration_complete`.

## Unified expense XLSX adoption

`run_unified_source_frappe_integration.py` uses the same isolated site/database guard. Unlike the synthetic document fixtures, it creates an actual private XLSX workbook and invokes the real source manifest, signed material preview, edit lease, complete-cargo review, atomic adoption and schema-2 calculation paths. It verifies selected expense quantity/weight, preservation of original purchase and packing facts, then changes the source comment and checks that the old preview cannot write any rows. The isolated site needs its own normal Frappe `encryption_key` for preview signatures. It makes no DeepSeek, OA, object-storage or ERP calls.

```sh
docker exec -e PYTHONPATH=/tmp/settlement-code -w /home/frappe/frappe-bench/sites overseas-cost-local-backend-1 /home/frappe/frappe-bench/env/bin/python /tmp/settlement-code/overseas_costing/tests/integration/run_unified_source_frappe_integration.py
```

The final fixture intentionally remains source-stale so the browser can verify that current fields are pending and the previous calculation is clearly marked as history.

## PostgreSQL source adapter contract

`test_settlement_archive_postgres.py` is opt-in. It applies the actual upstream migration files using the upstream worktree's installed `node-pg-migrate`, then tests the costing adapter through real read-only psycopg connections and the `costing_reader` role. Set `SETTLEMENT_UPSTREAM_WORKTREE` when the upstream checkout is not the sibling `dingtalk-settlement-upstream` directory.

Use a disposable PostgreSQL instance listening only on loopback and a database named `settlement_test*`. The fixture truncates its approval, allowlist and attachment tables between tests. Local trust authentication is expected for the disposable `costing_reader` role; no live credentials are needed.

```sh
SETTLEMENT_ADAPTER_TEST_DSN='postgresql://postgres@127.0.0.1:PORT/settlement_test_adapter' \
  python3 -m pytest -q overseas_costing/tests/integration/test_settlement_archive_postgres.py
```

The eight tests cover migration grants and preflight, actual `bucket`/`object_key`/`content_quality` columns, raw-payload hydration and tenant isolation, keyset paging over 200 equal-timestamp rows, attachment changes/retirement and source tombstones, unchanged weekly metadata inventory, changed-source-only hydration, and empty changed-source requests. Default pytest runs skip these tests without the explicit DSN. The lightweight inventory does not transfer or hydrate full source payloads, but PostgreSQL still reads source content to compute its revision hash.

## AI fill consistency and retry

Run the guarded AI fill probe after migrating the isolated settlement site to the
current app revision. It supplies synthetic, locally archived approval evidence
and uses real server preview receipts, the edit lease, MariaDB transactions and
document reads. It checks equal preview and saved values, unchanged raw purchase
fields, two simultaneous confirmations of one receipt, and rollback after a
real item write. The source authorization checks also run against the actual
Frappe document controller. No external services are contacted.

```sh
docker exec -e PYTHONPATH=/tmp/settlement-code -w /home/frappe/frappe-bench/sites overseas-cost-local-backend-1 /home/frappe/frappe-bench/env/bin/python -m overseas_costing.tests.integration.run_ai_fill_consistency_frappe_integration
```
