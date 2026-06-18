# RevenueCat API Research — `keboola.ex-revenuecat` (Phase 2)

**Date:** 2026-06-12
**Component:** `keboola.ex-revenuecat` (extractor)
**Author:** plan-worker (Component Factory, Phase 2)
**Status:** research complete; settled from official docs + live calls with the secrets.json key. Human
decisions locked (§0): v2-only runtime + seed test data. Test data **seeded and confirmed** (§8a) —
customer `keboola-test-001`, one active entitlement, AND one real `store: promotional` **subscription**
(the promo grant materialized a subscription after a short propagation delay) now return live →
genuine cassettes for those tables. Only **purchases/invoices** remain empty (not API-creatable).

> Findings marked **[LIVE]** were verified firsthand with read-only `GET` calls against the
> RevenueCat v2 API using the working secret key in `secrets.json` (`parameters.#api_key`). The key
> value is never reproduced here.

---

## 0. Decisions locked (human, 2026-06-12)

- **Component runtime API scope: v2-only.** The component itself calls only the v2 API.
- **Test data: seed real data into the (currently empty) RevenueCat project**, so Phase 5 can record
  genuine cassettes for the heavy tables. The human explicitly authorized creating test data. Seeding
  is a **test-setup concern only** — it does NOT relax the component's v2-only runtime scope. (See §8a
  for what is and isn't seedable; in practice our key is v2-only so even seeding must go through v2.)

## 1. API surface & base URLs — verdict: build on v2

RevenueCat exposes two REST surfaces:

| Surface | Base URL | Purpose | Extractor fit |
|---|---|---|---|
| **v1 (legacy)** | `https://api.revenuecat.com/v1` | SDK + server-to-server. | **Poor.** No enumerable customer/subscriber list — you can only `GET /subscribers/{app_user_id}` if you already know the id. Cannot bulk-discover customers. |
| **v2 (current)** | `https://api.revenuecat.com/v2` | Modern, server-to-server only (not used by SDKs). Project-scoped, cursor-paginated list endpoints. | **Strong — this is the extractor spine.** |

**Verdict: build the extractor on v2 only.** RevenueCat recommends v2 for programmatic server-to-server
access; v1 is positioned only as a fallback for use cases v2 has not yet reached. No v1-only must-have
entity surfaced for an analytics extractor. **Human confirmed v2-only (§0).** This is further forced by
the key itself: our `sk_` key is **v2-only** — v1 calls return `403 code 7723` "incompatible with
RevenueCat API V1" **[LIVE]**, so v1 is not even reachable with this credential.

Docs:
- v1 overview — https://www.revenuecat.com/docs/api-v1
- v1 reference — https://www.revenuecat.com/reference/basic
- v2 overview — https://www.revenuecat.com/docs/api-v2
- v2 reference — https://www.revenuecat.com/reference/revenuecat-rest-api

---

## 2. Authentication — confirmed working firsthand

- **Header (v2):** `Authorization: Bearer sk_...` — v2 requires the `Bearer` prefix (RFC 7235);
  v1 omits it and accepts the bare key. (Another reason to standardize on v2: one consistent auth scheme.)
- **The secrets.json key is a v2 project-scoped secret API key (`sk_` prefix). [LIVE]** It returned `200`
  across `/v2/projects`, `/apps`, `/products`, `/entitlements`, `/offerings`, `/offerings/{id}/packages`,
  and `/metrics/overview` — i.e. it carries read scope across **all three** rate-limit domains:
  customer-information, project-configuration, AND charts/metrics.
- **Key types** (RevenueCat):
  - **Public/SDK key** (`pk_…`/appl_…) — client-side, non-sensitive reads only. Not for an extractor.
  - **Secret key** (`sk_`) — server-to-server; **project-scoped**, per-endpoint permission scopes
    (e.g. `customer_information:customers:read`, `project_configuration:products:read`). What we have.
  - **OAuth 2.0 token** (`atk_`) — developer-level, spans projects, PKCE auth-code flow. Not needed.
- **Provisioning:** minting an `sk_` key requires a **project Admin** in the RevenueCat dashboard
  (Settings → API keys → new V2 key, assign permissions). Not headless. **Not a blocker for us — we
  already hold a working, sufficiently-scoped key.**
- **Keboola mapping:** store as `parameters.#api_key` (already the case in `secrets.json`). The `#`
  prefix → encrypted `KBC::ProjectSecure` at rest on the platform.

Docs:
- Authentication & API keys — https://www.revenuecat.com/docs/projects/authentication
- OAuth 2.0 — https://www.revenuecat.com/docs/projects/oauth-overview

---

## 3. Entity catalog — what the extractor pulls (all v2)

All shapes below are **[LIVE]**-confirmed against the test project unless noted. Every list item carries
an `object` discriminator field and a millisecond-epoch `created_at`.

### Top-level (project-scoped) lists

| Endpoint | Live result | Item keys (confirmed) |
|---|---|---|
| `GET /v2/projects` | 1 project | `id, name, created_at, icon_url, icon_url_large, object` |
| `GET /v2/projects/{id}/apps` | 1 app | `id, name, type, project_id, created_at, object` |
| `GET /v2/projects/{id}/products` | 3 products | `id, store_identifier, type, one_time, subscription{duration, grace_period_duration, trial_duration}, display_name, app_id, state, created_at, object` |
| `GET /v2/projects/{id}/entitlements` | 1 entitlement | `id, lookup_key, display_name, project_id, state, created_at, object` |
| `GET /v2/projects/{id}/offerings` | 1 offering | `id, lookup_key, display_name, is_current, metadata, project_id, state, created_at, object` |

### Nested lists (NOT top-level)

- **Packages are nested under offerings, not a project-level list. [LIVE]**
  `GET /v2/projects/{id}/packages` returns **HTTP 404**; the doc-research draft claiming a top-level
  packages list was wrong and is corrected here by direct observation.
  `GET /v2/projects/{id}/offerings/{offering_id}/packages` → 3 items:
  `id, lookup_key, display_name, position, created_at, object`.
  → Extractor must iterate offerings, then fetch packages per offering.

### Customer-domain lists (the analytics payload)

| Endpoint | Notes |
|---|---|
| `GET /v2/projects/{id}/customers` | **The enumerable spine.** Paginated. v1 has no equivalent. |
| `GET /v2/projects/{id}/customers/{customer_id}/active_entitlements` | per-customer active entitlements — items are `object: customer.active_entitlement`. **This is the correct path** (NOT `/entitlements`, which is `404`). **[LIVE]** Confirmed populated after seeding (count 1). |
| `GET /v2/projects/{id}/customers/{customer_id}/subscriptions` | per-customer drill-down, paginated |
| `GET /v2/projects/{id}/customers/{customer_id}/purchases` | per-customer drill-down, paginated |
| `GET /v2/projects/{id}/customers/{customer_id}/invoices` | per-customer drill-down, paginated |

- **Per-customer entitlements live at `/active_entitlements`, not `/entitlements`. [LIVE]** The plain
  `/customers/{cid}/entitlements` path returns `404`; the extractor design must target
  `/active_entitlements` for the customer-to-entitlement table.
- **Subscription item shape [LIVE]** (from the seeded `store: promotional` row,
  `GET /customers/keboola-test-001/subscriptions`, count 1). Item keys:
  `id, object("subscription"), customer_id, original_customer_id, product_id (null for promo),
  store ("promotional"|"app_store"|"play_store"|…), status ("active"|…), environment ("production"),
  ownership ("purchased"), gives_access (bool), auto_renewal_status ("will_not_renew"|…),
  starts_at, ends_at, current_period_starts_at, current_period_ends_at (all epoch ms),
  store_subscription_identifier, presented_offering_id, management_url, country, pending_changes,
  pending_payment`. Two nested structures to flatten/child-table:
  - `total_revenue_in_usd` → object `{commission, currency, gross, proceeds, tax}` (floats; all 0 for promo).
  - `entitlements` → a **nested list-envelope** `{object:"list", items:[…], next_page, url}` whose items
    are full entitlement objects (`id, lookup_key, display_name, state, project_id, created_at, object`).
- **`expand` is supported** (e.g. `?expand=items.package` on offerings returned `200`). **[LIVE]**
- Known doc-noted limitation: the customers *list* endpoint does not inline `active_entitlements`/
  `attributes` — those come from the per-customer `/active_entitlements` (or single-customer) fetch. To
  get full per-customer detail the extractor either expands or does a per-customer GET.

**Extraction shape implication:** config entities (projects/apps/products/entitlements/offerings/
packages) are few and cheap → enumerate fully. Customer-domain entities are the volume → list customers
(paginated), then fan out per-customer for active_entitlements/subscriptions/purchases/invoices. The
per-customer fan-out is the cost/rate-limit driver and the main scaling concern.

Docs: v2 reference — https://www.revenuecat.com/reference/revenuecat-rest-api

---

## 4. Pagination — confirmed

- **Cursor-based.** Response envelope **[LIVE]**:
  ```json
  { "object": "list", "items": [ ... ], "next_page": "<full URL | null>", "url": "<request URL>" }
  ```
- Params: `limit` (default 20, **max 100** — API caps higher requests at 100) and
  `starting_after=<last item id>`.
- **Drive pagination by following `next_page` until it is `null`.** The single-project list returned
  `next_page: null` (one page). `next_page` is a fully-formed URL carrying the next cursor.
- Forward-only. **List order is NOT guaranteed** by the API.

---

## 5. Rate limits — confirmed firsthand via response headers

Per-**domain** rate limiting:

| Domain | Limit | Endpoints |
|---|---|---|
| Customer information | **480 req/min** | customers, subscriptions, purchases, invoices |
| Project configuration | **60 req/min** | apps, products, entitlements, offerings, packages |
| Charts / metrics | **25 req/min** | analytics/metrics |

- **[LIVE]** on a project-configuration call the response carried:
  `revenuecat-rate-limit-current-usage: 5`, `revenuecat-rate-limit-current-limit: 60` — confirming the
  60/min config-domain ceiling and that usage is surfaced per response.
- **429 model:** body `{ "type": "rate_limit_error", "message": ..., "retryable": true, "backoff_ms": <n> }`
  plus a `Retry-After` header (seconds). Honour `Retry-After`/`backoff_ms` with exponential backoff.
- **Concurrency caveat:** RevenueCat also returns 429 for **concurrent requests against the same
  resource** ("another request in flight"). The extractor should serialize requests per resource
  (no parallel hits on the same customer/app_user_id).

Docs:
- Rate limiting — https://www.revenuecat.com/docs/api-v2/rate-limit
- Community confirmation of per-domain limits —
  https://community.revenuecat.com/general-questions-7/what-are-the-current-rate-limits-on-the-rest-api-4946

---

## 6. Incremental extraction — the main design caveat

**There is no `updated_since` / `created_after` / date-filter query parameter on v2 list endpoints**
(confirmed against docs; none documented for any list endpoint). Available change mechanisms:

- **Pagination cursor (`starting_after`)** — enables resumable full pulls, but because **list order is
  not guaranteed**, the cursor **cannot be treated as a reliable high-watermark** for "everything new
  since last run." It is a resumability aid, not a delta filter.
- **Webhooks** (Pro plan+) — the only real-time change feed (`INITIAL_PURCHASE`, `RENEWAL`,
  `PRODUCT_CHANGE`, `EXPIRATION`, …). A push mechanism; **out of scope for a pull extractor.**
- **Scheduled Data Exports** — dashboard-configured CSV/Parquet drops to S3/GCS/Azure, and the export
  rows carry an `updated_at` field with a "new and updated only" mode. **But there is no public API to
  trigger or fetch these exports** — the feature is dashboard-only, so it is **not usable
  programmatically by the extractor.** (Could only be consumed by separately polling the customer's
  cloud bucket, which is a different integration entirely.)

**Proposed incremental story for v1 of the component:**
- **Config entities** (projects/apps/products/entitlements/offerings/packages) are small → **full
  refresh every run** (full-load output).
- **Customer-domain entities** (customers + per-customer subscriptions/purchases/invoices) are the
  volume → **full paginated pull**, with **state storing the last successfully-completed cursor/position
  for resumability within/across runs**, NOT as a true server-side delta filter. True "changed-since"
  incremental is not achievable via the v2 list API today.
- Worth a Tier C confirmation with the human in Phase 3 (acceptable that v1 is full-refresh on the
  customer tables given no delta param exists?).

Docs:
- Webhooks — https://www.revenuecat.com/docs/integrations/webhooks
- Scheduled data exports — https://www.revenuecat.com/docs/integrations/scheduled-data-exports
- Data export v4 schema (incl. `updated_at`) — https://www.revenuecat.com/docs/data-export-version-4

---

## 7. Error model — confirmed live

Consistent JSON error body **[LIVE]**:
```json
{ "object": "error", "type": "<error_type>", "message": "<human readable>", "retryable": <bool>, "doc_url": "https://errors.rev.cat/<type>" }
```
Observed firsthand:
- Bad key → `type: "authentication_error"`, `retryable: false` (HTTP 401).
- Unknown id → `type: "resource_missing"`, `retryable: false` (HTTP 404).

**Keboola mapping:**
- `retryable: true` (and 429 / 5xx) → retry with exponential backoff honouring `Retry-After`/`backoff_ms`.
- 4xx with `retryable: false` (auth, bad param, missing resource) → raise `UserException` (exit 1):
  these are user/config faults, not platform faults. Reserve exit 2 for genuinely unexpected errors.

Docs: error handling — https://www.revenuecat.com/docs/test-and-launch/errors

---

## 8. Feasibility & provisioning verdict — ✅ GREEN

- **Auth works.** The secrets.json key returned `200` live across projects, apps, products,
  entitlements, offerings, packages-under-offering, and metrics/overview. No admin setup needed — we
  already hold a sufficiently-scoped v2 secret key.
- **Headless?** No (minting a key needs a dashboard Admin), but **irrelevant** since the key already
  exists and works.
- **Config entities have live test data** in the sandbox project ("Create an app called Keboola"):
  1 app (type `test_store`, id `app419959e85c`), 3 products (`yearly` subscription, `monthly`
  subscription, `lifetime` one_time), 1 entitlement (lookup_key `Create an app called Keboola Pro`,
  id `entlaa46372046`), 1 offering, 3 packages → solid VCR fixtures for those tables.
- **Customer data has been SEEDED** (was empty; the human authorized it and the team lead ran the seed —
  see §8a). `GET /v2/projects/proj2bfa9279/customers` now returns count **1** (`keboola-test-001`) with
  one granted active entitlement, so the **customers** and **active_entitlements** tables are
  fixtureable from genuine reads. The promo grant also **materialized a real `store: promotional`
  subscription** (count **1** on `/subscriptions`, after a short propagation delay) → **subscriptions
  is ALSO fixtureable from genuine reads, not empty.** Only **purchases and invoices remain count 0**
  (not API-creatable — they need a real store/SDK transaction) and will be recorded as empty-list cassettes.

## 8a. Test-data seeding — what IS and ISN'T creatable via the API

**Goal (human-authorized):** seed minimal real data so Phase 5 records genuine cassettes for the heavy
customer-domain tables.

**Critical key constraint discovered [LIVE]:** our `sk_` key is a **v2-only key**. A v1 call returned
`HTTP 403 {"code": 7723, "message": "You're trying to use a secret API key incompatible with RevenueCat
API V1."}`. → **All v1 seeding levers are unavailable** with this key: v1 implicit-create
(`GET /v1/subscribers/{id}`), v1 promotional grant, and v1 `POST /receipts` are all out. **Seeding must
go through v2 write endpoints**, which keeps it consistent with the v2-only runtime decision.

| Entity | Seedable? | Mechanism |
|---|---|---|
| **Customer** | ✅ via v2 | `POST /v2/projects/{id}/customers` body `{"id": "<app_user_id>"}` (optionally `attributes`) — creates a real customer that then appears in the `/customers` list. Returned `201`. |
| **Promotional entitlement (active entitlement state on a customer)** | ✅ via v2 | `POST /v2/projects/{id}/customers/{customer_id}/actions/grant_entitlement` body **`{"entitlement_id": "<id>", "expires_at": <epoch_ms>}`**. Produces visible active-entitlement state on the customer. Returned `201`. ⚠️ Body must be exactly these fields — `end_time_ms` and `duration` are **rejected** (`parameter_error "Additional properties are not allowed"`); `expires_at` (epoch ms) is **required**. |
| **Subscription** | ✅ via v2 (promo) | The `grant_entitlement` call **materializes a real subscription** with `store: "promotional"` — confirmed live: `/customers/{id}/subscriptions` → `count=1` (the row appeared after a short propagation delay; an immediate post-grant read showed 0, a re-read minutes later showed 1). This is a genuine subscription object, so the **subscriptions table IS fixtureable** from the seed. (A *store-backed* subscription — `store: app_store`/`play_store`/etc. — would still need a real/SDK purchase, but the promotional one is real and sufficient for a cassette.) |
| **Purchases / Transactions** | ❌ NOT via API | v2 has **no** create-purchase/transaction endpoint; purchases originate only from real store transactions or the SDK sandbox flow + webhooks. The `test_store` app type is exercised via the SDK, not a server write. Confirmed live: `/purchases` stayed `count=0` after seeding. |
| **Invoices** | ❌ NOT via API | Same — invoices derive from real billing events, no create endpoint. Confirmed live: `/invoices` stayed `count=0`. |

**The seed was performed and SUCCEEDED (live, 2026-06-12), run directly by the team lead with the
human's approval** (it could not run from this planning worker — the auto-mode classifier blocks
external writes authorized only via relayed cross-session messages; the lead's session established
direct human intent). Results:
- `POST /v2/projects/proj2bfa9279/customers` body `{"id":"keboola-test-001"}` → **201**, customer created.
- `POST /v2/projects/proj2bfa9279/customers/keboola-test-001/actions/grant_entitlement`
  body `{"entitlement_id":"entlaa46372046","expires_at":1798761600000}` → **201**.
- Confirmed reads: `/customers` → count **1** (`keboola-test-001`);
  `/customers/keboola-test-001/active_entitlements` → count **1** (`entlaa46372046`,
  `expires_at` `1798761600000` ≈ 2027-01-01); `/customers/keboola-test-001/subscriptions` → count **1**
  (a real `store: promotional` subscription — see §3 for its shape; it appeared after a short
  propagation delay, an immediate post-grant read had shown 0). `/purchases`, `/invoices` → count **0**
  (expected — purchases/invoices are not API-creatable).

**Propagation-delay caveat (important for Phase 5):** the subscription is created asynchronously by the
grant. An immediate read after `grant_entitlement` returned `count=0`; a re-read minutes later returned
`count=1`. When recording the subscriptions cassette, read *after* the row has propagated (re-poll until
non-empty), not immediately after the grant, or the cassette will wrongly capture an empty list.

**Phase 5 fixture coverage from this seed:** genuine cassettes for **customers**,
**active_entitlements-on-customer**, AND **subscriptions** (one real `store: promotional` row).
**Only purchases and invoices will be recorded as empty-list cassettes** (still valid for exercising
pagination/extraction, just no row content) — those two tables cannot be populated through the API.

### Traceability (for cleanup)
- Project id: `proj2bfa9279` ("Create an app called Keboola")
- Seeded customer `app_user_id`: `keboola-test-001` (clearly test-labeled)
- Entitlement granted: id `entlaa46372046` / lookup_key `Create an app called Keboola Pro`,
  `expires_at` `1798761600000`
- Endpoints used: `POST /v2/projects/proj2bfa9279/customers`, then
  `POST /v2/projects/proj2bfa9279/customers/keboola-test-001/actions/grant_entitlement`
- Cleanup: `DELETE /v2/projects/proj2bfa9279/customers/keboola-test-001` removes the seeded customer.

### Open Tier C questions for Phase 3 — all resolved
1. **v2-only scope** — ✅ confirmed by human (locked, §0).
2. **Test data** — ✅ human chose to seed; **seed performed and confirmed** (§8a). The seed yields
   genuine fixtures for customers, active_entitlements, AND subscriptions (one `store: promotional`
   row). Accepted limitation: **only purchases and invoices fixtures will be empty** (not
   API-creatable). No open question remains; the design must treat purchases/invoices as
   possibly-empty tables and must re-poll subscriptions for the propagation delay when recording
   cassettes (§8a).

---

## Doc citations (consolidated)

- API v1 overview — https://www.revenuecat.com/docs/api-v1
- API v1 reference — https://www.revenuecat.com/reference/basic
- API v2 overview — https://www.revenuecat.com/docs/api-v2
- API v2 reference — https://www.revenuecat.com/reference/revenuecat-rest-api
- Authentication & API keys — https://www.revenuecat.com/docs/projects/authentication
- OAuth 2.0 — https://www.revenuecat.com/docs/projects/oauth-overview
- Rate limiting — https://www.revenuecat.com/docs/api-v2/rate-limit
- Error handling — https://www.revenuecat.com/docs/test-and-launch/errors
- Webhooks — https://www.revenuecat.com/docs/integrations/webhooks
- Scheduled data exports — https://www.revenuecat.com/docs/integrations/scheduled-data-exports
- Data export version 4 — https://www.revenuecat.com/docs/data-export-version-4
