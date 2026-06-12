# keboola.ex-revenuecat — Design Spec

> Type: extractor
> Component ID: keboola.ex-revenuecat
> Status: draft
> Date: 2026-06-12

Built on the Phase 2 research: `docs/superpowers/research/2026-06-12-revenuecat-research.md`
(all API facts, shapes, rate limits, and the seeded test data are settled there and not re-derived here).

## 1. Overview & source system

- **What it does:** extracts subscription/in-app-purchase analytics data from **RevenueCat** into Keboola
  Storage — projects, apps, products, entitlements, offerings, packages (configuration) and
  customers + their active entitlements, subscriptions, purchases, invoices (customer analytics).
- **Source system:** RevenueCat **REST API v2** (`https://api.revenuecat.com/v2`). Official docs:
  https://www.revenuecat.com/docs/api-v2 ; reference: https://www.revenuecat.com/reference/revenuecat-rest-api
- **Primary use case:** land RevenueCat subscription data in a warehouse for revenue/retention analytics
  alongside other Keboola-extracted sources.

## 2. Keboola mapping

This is the section that makes the spec Keboola-shaped rather than a generic API client.

### 2.1 Source endpoints → output tables

All entities are v2 reads. Each output table is named `<entity>` and lands in the default bucket
(§2.6). Primary keys are the RevenueCat `id` of each object.

| Output table | Source endpoint | PK | Notes |
|---|---|---|---|
| `projects` | `GET /v2/projects` | `id` | The account's projects (the key is scoped to these). |
| `apps` | `GET /v2/projects/{pid}/apps` | `id` | Per project. |
| `products` | `GET /v2/projects/{pid}/products` | `id` | `subscription` sub-object flattened (§4.4). |
| `entitlements` | `GET /v2/projects/{pid}/entitlements` | `id` | Project-level entitlement catalog. |
| `offerings` | `GET /v2/projects/{pid}/offerings` | `id` | |
| `packages` | `GET /v2/projects/{pid}/offerings/{oid}/packages` | `id` | **Nested under offerings** (project-level `/packages` is 404). Carries `offering_id` (injected from the parent). |
| `customers` | `GET /v2/projects/{pid}/customers` | `id` | The enumerable spine of the customer domain. |
| `customer_active_entitlements` | `GET /v2/projects/{pid}/customers/{cid}/active_entitlements` | `id` + `customer_id` | Path is `/active_entitlements`, NOT `/entitlements` (404). Carries `customer_id`. |
| `subscriptions` | `GET /v2/projects/{pid}/customers/{cid}/subscriptions` | `id` | `total_revenue_in_usd` flattened; nested `entitlements` list → child table (§4.4). Carries `customer_id`. |
| `purchases` | `GET /v2/projects/{pid}/customers/{cid}/purchases` | `id` | Possibly empty (no seed data; not API-creatable). |
| `invoices` | `GET /v2/projects/{pid}/customers/{cid}/invoices` | `id` | Possibly empty (same). |
| `subscription_entitlements` | (from `subscriptions[].entitlements.items`) | `subscription_id` + `id` | Child table of the nested entitlements list on each subscription (§4.4). |

Project id and customer id from parent objects are **injected as columns** into child tables so the
output is joinable without the caller having to thread ids back together.

### 2.2 Single config vs config rows — **single config** (stated override of the convention)

**Decision: single configuration, NOT config rows.** A multipick selects which entity groups to extract.

The Keboola convention (`architecture-conventions.md`) says "multiple objects → config rows, one per
object." **This is the documented override case** ("the objects must be fetched together in a single
logical transaction"), for two concrete platform reasons:

1. **Cross-entity dependencies.** The customer-domain tables are a fan-out: you must list `customers`
   to get the `customer_id`s before you can fetch that customer's `active_entitlements`/`subscriptions`/
   `purchases`/`invoices`; `packages` need `offering_id`s from `offerings`; everything needs the
   `project_id` from `projects`. Config rows run **sequentially with no cross-row state sharing**
   (`config-rows.md`) — a "subscriptions" row could not see the "customers" row's ids. The traversal is
   one logical unit and must live in one run.
2. **One credential, one project scope.** The `sk_` key is project-scoped; there is no per-row
   credential or per-row object that rows would buy us.

So: **config-level** params = auth + global options; a **multipick** lets the user disable entity groups
(e.g. skip the expensive customer fan-out). This is the convention's explicitly-allowed "multipick inside
a single config" shape, not the discouraged "multipick instead of rows for independent objects" shape.
`configRowSchema.json` stays empty.

### 2.3 Incremental strategy → full load + persisted watermark (stated override)

**Decision: full load every run, with `incremental=True` + primary key on every output table, and a
`last_run` watermark persisted in `state.json`.**

The convention prefers incremental loading *when the source exposes an updated-since cursor*. **RevenueCat
v2 has no such cursor** (research §6: no `updated_since`/date filter on any list endpoint; list order is
not even guaranteed, so the pagination cursor can't serve as a watermark). That is the documented
"no incremental signal" override → full load.

But we still:
- write each table with **`incremental=True` + the primary key** so re-runs **upsert on PK** rather than
  duplicate-append (`output-mapping.md`), and the table isn't fully rewritten mid-failure;
- persist `state.json = {"last_run": <UTC ISO8601 captured before fetch>}` after a successful run, so a
  future RevenueCat incremental capability (or a webhook-backfill mode) drops in without a state migration.

Rationale is stated in code per the convention. (Config entities are tiny; the customer fan-out is the
volume but has no delta signal, so full + upsert is the correct and simplest correct choice.)

### 2.4 Secrets → `#`-prefixed

`parameters.#api_key` (already the shape in `secrets.json`). The `#` prefix → platform encrypts at rest
(`KBC::ProjectSecure`); the component receives the decrypted value at runtime (`encryption.md`). No other
secrets.

### 2.5 Sync actions

- **`testConnection`** — calls `GET /v2/projects?limit=1` with the key; 200 → success, 401
  `authentication_error` → failure with a clear message. Validates the credential in the UI before runtime.
- **`listProjects`** (dropdown, optional) — populates an optional `project_id` filter from
  `GET /v2/projects` so a multi-project account can scope the run to one project via a valid pick rather
  than free text. (Sync action implementation is owned by `component-build-ui`.)

### 2.6 Output bucket / table naming

Follow **default-bucket** behaviour — do not hardcode a `destination`. With `default_bucket: true` set in
the Dev Portal (Phase 6), tables route to `in.c-keboola.ex-revenuecat-{configId}` and any manifest
destination is overridden anyway (`default-bucket.md`). Tables are named by entity (`customers`,
`subscriptions`, …).

## 3. Authentication & connection

- **Auth:** API key in `Authorization: Bearer sk_...` (v2 requires the `Bearer` prefix). Chosen over v1
  token auth and OAuth `atk_` because (a) v2 is the recommended server-to-server surface, (b) our key is
  a **v2-only project-scoped secret** (research §2/§8a: v1 returns `403 code 7723`), (c) no OAuth dance is
  needed for a single-tenant extractor.
- **Connection:** REST over HTTPS, JSON. Single surface: v2. No SDK (no maintained official Python SDK for
  the server API; a thin `requests`/`keboola-http-client` client is simpler and pinnable).
- **Provisioning:** minting an `sk_` v2 key needs a **project Admin** in the RevenueCat dashboard
  (Settings → API keys → new V2 key, grant read scopes). **Not headless**, but not a blocker — the user
  supplies the key once into the config, exactly like any API-key extractor. Our build already holds a
  working, sufficiently-scoped key.
- **Blockers / access:** none. Auth confirmed live across all entity endpoints. Test data **seeded**
  (research §8a) so customers/active_entitlements/subscriptions are exercisable; purchases/invoices stay
  empty (not API-creatable) — handled as possibly-empty tables, not a blocker.

## 4. Data model & endpoints

### 4.1 Scope for v1 of the component

In scope (all v2): projects, apps, products, entitlements, offerings, packages, customers,
customer_active_entitlements, subscriptions, purchases, invoices, plus the `subscription_entitlements`
child table. **Deferred:** charts/metrics endpoints (different rate-limit domain, aggregate not raw —
revisit if requested); webhooks ingestion (push model, not a pull extractor); RevenueCat Scheduled Data
Exports (dashboard-only, no API).

### 4.2 Pagination

Cursor-based. Every list endpoint returns `{object:"list", items:[...], next_page:<full URL|null>, url}`.
The client requests `limit=100` (the max) and **follows `next_page` until null**, accumulating `items`.
Generic pager in the client — works identically for every entity.

### 4.3 Rate limits

Per-domain (research §5): customer-info 480/min, project-configuration 60/min, charts 25/min. The client:
- reads `RevenueCat-Rate-Limit-*` headers for observability;
- on **429** honours `Retry-After`/`backoff_ms` with capped exponential backoff + jitter, bounded retries;
- on **5xx / `retryable:true`** retries with the same backoff;
- **serializes** per-customer requests (no concurrent hits on the same customer resource — RevenueCat
  returns 429 for concurrent same-resource requests). The customer fan-out is sequential per customer.

### 4.4 Nested response shapes → flatten vs child-table

- **`products[].subscription`** = object `{duration, grace_period_duration, trial_duration}` →
  **flatten** into the `products` row as `subscription_duration`, `subscription_grace_period_duration`,
  `subscription_trial_duration`.
- **`subscriptions[].total_revenue_in_usd`** = object `{commission, currency, gross, proceeds, tax}` →
  **flatten** into the `subscriptions` row as `total_revenue_in_usd_gross`, `_proceeds`, `_tax`,
  `_commission`, `_currency`.
- **`subscriptions[].entitlements`** = a **nested list-envelope** `{object:"list", items:[entitlement…]}`
  → **child table `subscription_entitlements`** (PK `subscription_id` + entitlement `id`), one row per
  entitlement, with `subscription_id` injected. (Flattening a variable-length list into columns is wrong;
  a child table keeps it joinable.)
- Parent ids (`project_id`, `customer_id`, `offering_id`) are **injected as columns** into the relevant
  child tables.

### 4.5 Propagation-delay note (testing-relevant)

A promotional `grant_entitlement` materializes its subscription **asynchronously** (research §8a) — an
immediate read can show an empty `/subscriptions`. Relevant only to cassette recording (§7), not runtime.

## 5. Configuration & schema

Described here; the actual `configSchema.json` is built by **`component-build-ui`** (do not hand-write the
JSON in this spec).

**Config-level parameters (single config — no rows):**

| Field | Type | Req? | Secret? | Default | Notes |
|---|---|---|---|---|---|
| `#api_key` | string | yes | yes (`#`) | — | RevenueCat v2 secret key (`sk_…`). |
| `project_id` | string (dropdown via `listProjects`) | no | no | all | Optional: restrict to one project; empty → all projects the key can see. |
| `entities` | multipick | no | no | all | Which entity groups to extract: `config` (projects/apps/products/entitlements/offerings/packages) and `customers` (customers + active_entitlements/subscriptions/purchases/invoices). Lets the user skip the expensive customer fan-out. |
| `load_type` | enum dropdown | no | no | `full_load` | `full_load` only for v1 (no incremental signal). Exposed as a dropdown (not a bare bool) per convention, leaving room for a future `incremental_load` mode; `full_load` still writes incremental+PK upserts. |

### 5.1 Typed Pydantic configuration model (`src/configuration.py`)

The config is a single typed Pydantic v2 model — no raw `dict`/`Any` for structured data, no scattered
`parameters.get()` outside the model (checklist: `typing`, `configuration`).

```python
class EntityGroup(StrEnum):
    config = "config"
    customers = "customers"

class LoadType(StrEnum):
    full_load = "full_load"
    incremental_load = "incremental_load"

class Configuration(BaseModel):
    model_config = ConfigDict(extra="ignore")          # explicit extra policy (configuration dim)

    api_key: str = Field(alias="#api_key")             # alias matches the schema property EXACTLY
    project_id: str | None = None                      # optional → explicit default
    entities: list[EntityGroup] = [EntityGroup.config, EntityGroup.customers]
    load_type: LoadType = LoadType.full_load

    @computed_field
    @property
    def incremental(self) -> bool:
        return self.load_type == LoadType.incremental_load

    @field_validator("api_key")
    @classmethod
    def _non_blank(cls, v: str) -> str:                 # validator tolerates None/empty, raises clean
        if not v or not v.strip():
            raise ValueError("RevenueCat API key (#api_key) is required.")
        return v
```

- **Built-in generics only** (`list[...]`, `str | None`) — no `typing.List`/`Optional` (typing dim; Ruff `UP`).
- **No `debug` field** — the platform `debug` parameter is consumed by the component base, which
  auto-switches the root logger to DEBUG; a model `debug` field or any manual `setLevel` would be a
  finding (configuration + logging dims).
- The model is built once from `self.configuration.parameters` at the top of `run()`; a blank/missing
  `#api_key` surfaces as a `UserException` (exit 1) **before any HTTP call**. A sync action that needs
  fewer fields may partially instantiate, but `testConnection` only needs `#api_key`.

### 5.2 configSchema.json design (built by `component-build-ui`, Phase 6 — designed here)

Single-config schema (`configRowSchema.json` stays empty). Field/UI design (checklist: `schema-ui`):

| Property | Widget | Order | Notes |
|---|---|---|---|
| `#api_key` | string, `format: "password"` | 1 | Encrypted; name `#api_key` matches the model `alias` exactly. Has a user-facing `title`/`description`. |
| `project_id` | string, async select | 2 | `"enum": []` present (async-select requirement); `options.async.action: "listProjects"` — matched by a `@sync_action` in code. Empty = all projects. |
| `entities` | array / multipick | 3 | enum `["config","customers"]`, default both; `title`/`description`. |
| `load_type` | string, enum dropdown | 4 | enum `["full_load","incremental_load"]`, default `full_load`. |

- **Test-connection:** a hardcoded `format: "test-connection"` widget auto-invokes `testConnection` —
  **no `options.async` needed** for it.
- **`propertyOrder`** lists only the four properties above (all exist in the schema).
- **No test/sandbox values in the customer-facing enum** and the defaults are not test values — the
  seeded project id stays in test fixtures/code, never in the schema enum.
- Any future conditional fields would use `options.dependencies`, not root-level `dependencies`.

**Sync actions** (owned by `component-build-ui`): `testConnection` (hardcoded widget; calls
`GET /v2/projects?limit=1`) and `listProjects` (`@sync_action` returning project ids/names as dropdown
options). Every `options.async.action` value has a matching `@sync_action` in code.

### 6.1 Structure (architecture dim)

- **API client module** (`src/client/revenuecat_client.py`, separate from `component.py`): owns base URL,
  `Bearer` auth header, the generic cursor pager, rate-limit/429/5xx retry+backoff, per-customer
  serialization, and one typed method per endpoint group (`list_projects`, `list_apps`, `list_products`,
  …, `list_customers`, `list_customer_subscriptions`, …). Raises a typed `RevenueCatClientError` carrying
  the API error `type`/`message`/`retryable`.
- **Client is built in `Component.__init__`**, not in `run()` or per-call (architecture dim).
- **`run()` is a thin orchestrator, under 30 lines**: build config model → (already-constructed client) →
  for each selected entity group call a private method → persist state. Logic lives in well-named private
  methods: `_extract_config_entities`, `_extract_customer_domain`, `_flatten_subscription`,
  `_flatten_product`, `_write_table`, `_load_state`, `_save_state`. No business logic inline in `run()`.
- Methods that don't use `self` (pure flatteners) are `@staticmethod` (typing dim).
- **Pagination has an explicit stop condition** — the pager loops `while next_page is not None`, never an
  unbounded `while True`; **collection access is guarded** (no bare `items[0]`/`.pop()` without a length
  check), since `/customers` can be empty (architecture dim).
- **Config model** (`src/configuration.py`): the typed Pydantic model from §5.1; `load_type`→`incremental`
  computed field per `incremental-state.md`.

### 6.2 Output tables, manifests, native types (output-state dim)

- Emit the **authoritative `schema` manifest** (CF default for new components) via
  `create_out_table_definition(..., primary_key=[...], incremental=True, schema=...)`.
- **Columns are declared explicitly in the manifest, never inferred.** Each column carries a **meaningful
  native type** from the source — epoch-ms timestamps (`created_at`, `starts_at`, `ends_at`, period
  bounds) as INTEGER/TIMESTAMP, revenue fields (`total_revenue_in_usd_*`) as NUMERIC, booleans
  (`gives_access`, `is_current`, `one_time`) as BOOLEAN, ids/keys as STRING. An all-STRING authoritative
  schema would be a finding (`native-data-types.md`).
- **Envelope/internal keys are stripped** from output columns — the list envelope `object`/`next_page`/
  `url` and any `_meta` never become columns; only the entity's own fields plus injected parent ids.
- **Nested objects flattened to scalar columns** (§4.4); the one variable-length list
  (`subscriptions[].entitlements`) becomes the `subscription_entitlements` child table, not a JSON blob.
- **PK set intentionally** on every table (§2.1); **destination table names are static** (entity names),
  never generated at runtime; the `incremental=True` flag matches the actual strategy (full pull +
  PK upsert, §2.3).
- CSVs written **headerless** (the schema names columns) so `has_header` stays default — avoids the
  header-as-data Storage failure (`native-data-types.md`). Destination resolved via `default_bucket: true`
  (set Phase 6), no hardcoded destination.

### 6.3 State handling (output-state dim)

- **Read at start:** `state = self.get_state_file() or {}` — first run with no state must not `KeyError`;
  `since = state.get("last_run")` (currently unused at runtime since the source has no cursor, kept for the
  future incremental mode).
- **Capture the watermark BEFORE fetching:** `run_started_at = datetime.now(timezone.utc)`.
- **Write at end, only after a successful write:** `self.write_state_file({"last_run": run_started_at.isoformat()})`
  on every successful run (UTC ISO-8601). A failed run keeps the old watermark and is safely retried.
- **State keys are consistent across runs** (`last_run`); a structure change would carry a backward-compatible fallback.

### 6.4 Error handling (error-handling + exit-codes dims)

- **All user-actionable errors → `UserException` (exit 1)** with plain-language messages — no stack
  traces, no internal URLs, no raw API bodies. Map: `authentication_error` → "Invalid RevenueCat API
  key…"; `resource_missing` on a bad `project_id` → "Project <id> not found…"; any 4xx `retryable:false` →
  a clear message. Genuinely unexpected errors bubble up as exit 2 (hidden from user); never
  `sys.exit(2)` for a user-actionable problem.
- **Re-raise with `from e`** to preserve the traceback; **no `except Exception` that silently swallows**.
- **Catch `requests.RequestException`** (and the `HttpClient` `RetryError` from exhausted retries), not
  only `HTTPError` — an uncaught `RetryError` would escape as exit 2. Retry logic **surfaces the final
  error** rather than swallowing it.
- **Every outbound HTTP request passes an explicit `timeout=`** — a timeout-less call hangs forever on a
  slow host (silent stall). The client sets a default request timeout on all calls.

### 6.5 Logging (logging dim)

- **No `print()`** — use the `logging` root logger.
- **No secrets/PII logged:** never log the `#api_key`/`Authorization` header; do not log full customer
  payloads (emails/PII) — log ids and counts only. Structured objects passed to the logger are scrubbed
  of sensitive fields.
- **INFO = meaningful progress, not noise:** per-entity row counts ("extracted N customers"), pagination
  progress (page k), and rate-limit waits ("429, backing off Ns"). Verbose/diagnostic detail uses
  `logging.debug(...)`.
- **No manual logging-level code** (`setLevel`/`basicConfig`) — the component base auto-switches to DEBUG
  from the platform `debug` parameter; writing manual level code would be the finding.

### 6.6 Dependencies

`keboola-component` (Common Interface, table defs, state, `UserException`, `@sync_action`),
`keboola-http-client` or `requests` (HTTP + retry/backoff + `timeout`), `pydantic` v2 (config model). All
already in `pyproject.toml`.

## 7. Testing

### 7.1 Datadir tests
- **Happy path — config entities:** seeded project → assert `projects`/`apps`/`products`/`entitlements`/
  `offerings`/`packages` tables written with expected columns + PK, flattened `subscription_*` columns.
- **Happy path — customer domain:** seeded `keboola-test-001` → `customers` (1 row),
  `customer_active_entitlements` (1 row), `subscriptions` (1 `store: promotional` row with flattened
  `total_revenue_in_usd_*`), `subscription_entitlements` (1 row), and `purchases`/`invoices` written as
  **empty tables** (header/manifest only).
- **Entity selection:** `entities` multipick excluding `customers` → only config tables produced.
- **Failure — bad auth:** invalid `#api_key` → `authentication_error` → **exit 1**, clear message.
- **Failure — bad project_id:** `resource_missing` → **exit 1**.
- **Pagination:** a multi-page `next_page` cassette → assert all items accumulated across pages.

### 7.2 VCR strategy
- Record genuine cassettes against the seeded project for config + customers + active_entitlements +
  subscriptions (real `store: promotional` row). **Re-poll `/subscriptions` until non-empty before
  recording** (the async propagation caveat, research §8a) so the cassette isn't wrongly empty.
- purchases/invoices recorded as genuine **empty-list** responses (valid; just no rows).
- **Sanitizers:** redact the `Authorization` header and the `sk_` value; scrub any `email`/customer PII in
  recorded customer payloads. Cassettes grepped clean of the `secrets.json` key and common secret tokens
  (the Phase 5 cassette-validation gate).
- Owned by `component-test` / `generate-vcr-tests`.

### 7.3 Sync action tests
- `testConnection`: 200 cassette → success; 401 cassette → failure object (not an exception).
- `listProjects`: returns the project list as dropdown options.

### 7.4 Seed payloads already captured
Research §8a/§3 captured the real shapes (subscription item keys, nested structures, ids) — these seed the
cassettes directly. Seed traceability + cleanup `DELETE` are in research §8a.

## 8. Deployment & validation (cf-dev, Phase 7)

- Build an image from `initial-implementation`; via **kbagent** create a config in **cf-dev** with the
  image tag overridden to that branch build and `#api_key` set to the seeded-project key.
- **Successful run:** exit 0; `projects`/`apps`/`products`/`entitlements`/`offerings`/`packages` populated;
  `customers`=1, `customer_active_entitlements`=1, `subscriptions`=1, `subscription_entitlements`=1;
  `purchases`/`invoices`=0 rows. Evidence must include the job id, `success`, and the resolved image tag
  (must be the branch build, not a stable release).
- Developer Portal value setup (configSchema, sync actions, `default_bucket`, `dataTypeSupport=authoritative`)
  is **Phase 6**, after the bootstrap release, owned by `component-dev-portal`.

## 9. Open risks & blockers

1. **Empty purchases/invoices fixtures** (low) — not API-creatable, so those two tables ship with
   empty-list cassettes; their column schema is taken from the documented v2 shape, unverified against live
   rows. *Mitigation:* derive schema from docs; mark for re-verification if a real store-backed account
   becomes available. Owner: tester (Phase 5).
2. **Subscription rows only `store: promotional` in test data** (low) — store-backed subscription variants
   (`app_store`/`play_store`) aren't in the seed, so their store-specific fields are untested. *Mitigation:*
   schema covers the documented union; flag for live re-verification. Owner: tester.
3. **Customer fan-out cost on large accounts** (medium) — N customers × 4 per-customer endpoints, serialized,
   under the 480/min customer-info limit. *Mitigation:* the `entities` multipick lets users skip the
   customer domain; backoff handles 429; a future incremental/webhook mode would cut volume. Owner: dev
   (Phase 4) — keep the pager efficient (limit=100) and log progress.
4. **`dataTypeSupport` switch + schema manifest must agree** (low) — if the Dev Portal flip (Phase 6) and the
   code's manifest format drift, types silently downgrade. *Mitigation:* Phase 7 smoke run confirms types on
   a real Storage import. Owner: dev-portal (Phase 6) + tester (Phase 7).
