# keboola.ex-revenuecat — Implementation Plan

> Source spec: `docs/superpowers/specs/2026-06-12-ex-revenuecat-design.md`
> Research: `docs/superpowers/research/2026-06-12-revenuecat-research.md`
> Branch: `initial-implementation`
> Execution: worked task-by-task via `superpowers:subagent-driven-development`. Each task names the
> owning component skill so the subagent stays Keboola-aware. Tasks are ordered by dependency; later tasks
> assume earlier ones are merged. The lifecycle tracker (`docs/superpowers/*-lifecycle.md`) gates the
> Phase 4/5 *milestones* — tick those only on the verifier's ✅, not when the last task here is checked.

## Conventions for every task
- Work on `initial-implementation`. Run `ruff check` + `ruff format` clean before declaring a task done.
- Keep `run()` under 30 lines; logic in private methods. No secret/PII logging. Explicit `timeout=` on
  every HTTP call. `UserException` (exit 1) for user-actionable faults.
- Do not commit the gitignored lifecycle tracker.

---

## Phase 4 — Implementation (owner: `component-develop`; schema/UI tasks → `component-build-ui`)

### Task 1 — Typed configuration model — owner: `component-develop`
**Goal:** `src/configuration.py` holds the single Pydantic v2 `Configuration` model per spec §5.1.
- `EntityGroup`/`LoadType` `StrEnum`s; fields `api_key: str = Field(alias="#api_key")`,
  `project_id: str | None = None`, `entities: list[EntityGroup]` (default both), `load_type` (default
  `full_load`); `model_config = ConfigDict(extra="ignore")`; `incremental` computed field; `field_validator`
  on `api_key` that tolerates None/empty and raises a clean message.
- Built-in generics only (no `typing.List`/`Optional`). No `debug` field.
**Done when:** model imports, validates a sample config, raises on blank `#api_key`; `ruff` clean.
**Verify:** unit-instantiate from `parameters` dict (valid + blank-key cases).

### Task 2 — RevenueCat v2 API client — owner: `component-develop`
**Goal:** `src/client/revenuecat_client.py` — a `requests`/`keboola-http-client`-based client, separate
from `component.py`, per spec §3/§4/§6.1/§6.4.
- Base `https://api.revenuecat.com/v2`, `Authorization: Bearer <key>`, **explicit `timeout=` on every
  request**.
- **Generic cursor pager**: `limit=100`, follow `next_page` until `None`, accumulate `items`; explicit
  stop condition (no `while True`), guarded collection access.
- **Retry/backoff**: 429 honours `Retry-After`/`backoff_ms`; 5xx / `retryable:true` retried with capped
  exponential backoff + jitter, bounded; per-customer requests serialized.
- **Error mapping**: parse `{type,message,retryable,doc_url}`; raise typed `RevenueCatClientError`
  (carrying type/message/retryable). Catch `requests.RequestException`/`RetryError`, re-raise `from e`.
- One typed method per endpoint group: `list_projects`, `list_apps(pid)`, `list_products(pid)`,
  `list_entitlements(pid)`, `list_offerings(pid)`, `list_packages(pid, oid)`, `list_customers(pid)`,
  `list_customer_active_entitlements(pid, cid)`, `list_customer_subscriptions(pid, cid)`,
  `list_customer_purchases(pid, cid)`, `list_customer_invoices(pid, cid)`.
**Done when:** methods callable; pager unit-tested with a 2-page fake; `ruff` clean.
**Verify:** unit test the pager (2 pages → merged) and the 429/error mapping with mocked responses.

### Task 3 — Transform/flatten helpers — owner: `component-develop`
**Goal:** pure `@staticmethod` flatteners per spec §4.4/§6.2.
- `_flatten_product` (lift `subscription.{duration,grace_period_duration,trial_duration}`),
  `_flatten_subscription` (lift `total_revenue_in_usd.{gross,proceeds,tax,commission,currency}`; split the
  nested `entitlements` list-envelope out for the child table).
- Strip envelope keys (`object`/`next_page`/`url`); inject parent ids (`project_id`/`customer_id`/
  `offering_id`) into child rows.
**Done when:** given the real sample shapes (research §3), produce flat dict rows + the child-row list;
`ruff` clean.
**Verify:** unit test against the captured subscription/product shapes from research.

### Task 4 — Output writer (manifests, native types, state) — owner: `component-develop`
**Goal:** table-writing + state helpers per spec §6.2/§6.3.
- `_write_table(name, rows, pk, schema)` → `create_out_table_definition(..., primary_key=pk,
  incremental=True, schema=...)`, **headerless** CSV, explicit columns with **meaningful native types**
  (epoch-ms→INTEGER/TIMESTAMP, revenue→NUMERIC, flags→BOOLEAN, ids→STRING), no envelope columns. Writes
  empty tables (header/manifest only) for empty entity lists (purchases/invoices).
- `_load_state`/`_save_state`: read `last_run` at start (missing→`{}`, no KeyError), capture UTC watermark
  before fetch, write after success.
- No scratch under `/data/out/tables/` (use `/tmp` if ever needed).
**Done when:** writing a sample row set produces correct manifest + headerless CSV; empty list → empty
table; `ruff` clean.
**Verify:** inspect a produced manifest (schema, PK, has_header) and the CSV; confirm native types not all-STRING.

### Task 5 — `run()` orchestrator + entity traversal — owner: `component-develop`
**Goal:** `src/component.py` wires it together per spec §6.1.
- Client built in `__init__`. `run()` <30 lines: build `Configuration` (UserException on invalid) →
  resolve project scope (`project_id` or all from `list_projects`) → for selected `entities` groups call
  `_extract_config_entities` / `_extract_customer_domain` → `_save_state`.
- `_extract_config_entities`: projects, apps, products, entitlements, offerings, then packages per
  offering. `_extract_customer_domain`: list customers, then per-customer fan-out (active_entitlements,
  subscriptions+child, purchases, invoices), serialized.
- Error handling/logging per spec §6.4/§6.5 (counts, page progress, backoff; no secrets/PII).
**Done when:** `python -m src.component` against a datadir runs end-to-end on mocked/cassette data,
produces the expected tables; `run()` <30 lines; `ruff` clean.
**Verify:** local datadir run; confirm table set + exit 0; bad key → exit 1.

### Task 6 — configSchema.json + sync actions — owner: `component-build-ui`
**Goal:** author `component_config/configSchema.json` (rowSchema stays empty) + the two sync actions per
spec §5.2.
- Properties `#api_key` (password), `project_id` (async select, `enum: []`,
  `options.async.action: listProjects`), `entities` (multipick enum), `load_type` (enum dropdown);
  `propertyOrder` of exactly these; titles/descriptions on required fields; `format: "test-connection"`
  widget (no `options.async`); no test/sandbox values in enums/defaults.
- `@sync_action` `testConnection` (GET `/v2/projects?limit=1`; success/clear failure) and `listProjects`
  (returns project options). Each `options.async.action` matched by a `@sync_action`.
**Done when:** schema validates; sync actions importable; `#api_key` name matches the model `alias`.
**Verify:** schema-tester / Playwright per `component-build-ui`; run `testConnection` against a cassette.

### Task 7 — Wire deps, docs, sample config — owner: `component-develop`
**Goal:** confirm `pyproject.toml` deps; refresh `component_config/*.md` short/long descriptions; replace
the cookiecutter `sample-config/config.json` (HubSpot leftovers) with a RevenueCat sample (`#api_key`
placeholder, `entities`, `load_type`).
**Done when:** sample config matches the schema; descriptions describe RevenueCat; `ruff` clean.
**Verify:** sample config validates against the schema.

> **Phase 4 milestone gate (tracker):** scoped `component-checklist-review` on architecture, typing,
> configuration, error-handling, logging, output-state, infra — run cold by an independent subagent.

---

## Phase 5 — Tests + VCR cassettes (owner: `component-test` / `generate-vcr-tests`)

### Task 8 — datadir test scaffolding — owner: `component-test`
**Goal:** `tests/functional/` datadir cases per spec §7.1. Single merged `config.json` per case (root
`parameters` only; no row split), row-scoped state files.
- Cases: happy-config, happy-customers, entity-selection (customers excluded), bad-auth (→exit 1),
  bad-project (→exit 1), pagination (multi-page).
**Done when:** test harness discovers cases; expected dirs scaffolded.

### Task 9 — record VCR cassettes — owner: `generate-vcr-tests`
**Goal:** record genuine cassettes against the seeded project (research §8a). **Re-poll `/subscriptions`
until non-empty before recording** (async propagation). purchases/invoices recorded as genuine
empty-list. Sanitizers redact `Authorization`/`sk_` and customer PII.
**Done when:** cassettes recorded; replay is deterministic offline.

### Task 10 — assertions + full suite green — owner: `component-test`
**Goal:** assert produced tables/columns/PKs against a real produced row (not just the manifest); empty
purchases/invoices tables present. Sync-action tests (testConnection 200/401, listProjects). Full
`pytest` green.
**Done when:** `pytest` prints `N passed`; cassette-validation gate passes (sanitized + recordings match
intent).

> **Phase 5 milestone gate (tracker):** scoped `component-checklist-review` on testing, credentials,
> output-state — run cold by an independent subagent.

---

## Out of plan (later phases, owned elsewhere)
- **Phase 6** (`component-dev-portal`): configSchema/sync-actions/`default_bucket: true`/
  `dataTypeSupport=authoritative` live in the portal, after the bootstrap release.
- **Phase 7** (`component-test` tier 4): cf-dev deploy + smoke test with image-tag override.
- **Phase 8** (`component-checklist-review`): full CF-standards audit.

## Risks carried from the spec (§9)
Empty purchases/invoices fixtures (schema from docs); only `store: promotional` subscriptions in test
data; customer fan-out cost on large accounts (multipick + backoff mitigate); `dataTypeSupport`-flip vs
manifest agreement (verified at Phase 7 smoke).
