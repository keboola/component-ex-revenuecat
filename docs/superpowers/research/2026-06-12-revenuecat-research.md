# RevenueCat API Research — `keboola.ex-revenuecat` (Phase 2)

**Date:** 2026-06-12
**Component:** `keboola.ex-revenuecat` (extractor)
**Author:** plan-worker (Component Factory, Phase 2)
**Status:** research complete; settled from official docs + live read-only calls with the secrets.json key.

> Findings marked **[LIVE]** were verified firsthand with read-only `GET` calls against the
> RevenueCat v2 API using the working secret key in `secrets.json` (`parameters.#api_key`). The key
> value is never reproduced here.

---

## 1. API surface & base URLs — verdict: build on v2

RevenueCat exposes two REST surfaces:

| Surface | Base URL | Purpose | Extractor fit |
|---|---|---|---|
| **v1 (legacy)** | `https://api.revenuecat.com/v1` | SDK + server-to-server. | **Poor.** No enumerable customer/subscriber list — you can only `GET /subscribers/{app_user_id}` if you already know the id. Cannot bulk-discover customers. |
| **v2 (current)** | `https://api.revenuecat.com/v2` | Modern, server-to-server only (not used by SDKs). Project-scoped, cursor-paginated list endpoints. | **Strong — this is the extractor spine.** |

**Verdict: build the extractor on v2 only.** RevenueCat recommends v2 for programmatic server-to-server
access; v1 is positioned only as a fallback for use cases v2 has not yet reached. No v1-only must-have
entity surfaced for an analytics extractor. (Confirm v2-only scope as a Tier C question with the human in
Phase 3.)

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
| `GET /v2/projects/{id}/customers/{customer_id}/subscriptions` | per-customer drill-down, paginated |
| `GET /v2/projects/{id}/customers/{customer_id}/purchases` | per-customer drill-down, paginated |
| `GET /v2/projects/{id}/customers/{customer_id}/invoices` | per-customer drill-down, paginated |

- **`expand` is supported** (e.g. `?expand=items.package` on offerings returned `200`). **[LIVE]**
- Known doc-noted limitation: the customers *list* endpoint does not inline `active_entitlements`/
  `attributes` — those come from the single-customer fetch. To get full per-customer detail the
  extractor either expands or does a per-customer GET.

**Extraction shape implication:** config entities (projects/apps/products/entitlements/offerings/
packages) are few and cheap → enumerate fully. Customer-domain entities are the volume → list customers
(paginated), then fan out per-customer for subscriptions/purchases/invoices. The per-customer fan-out is
the cost/rate-limit driver and the main scaling concern.

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

## 8. Feasibility & provisioning verdict — ✅ GREEN (one data caveat)

- **Auth works.** The secrets.json key returned `200` live across projects, apps, products,
  entitlements, offerings, packages-under-offering, and metrics/overview. No admin setup needed — we
  already hold a sufficiently-scoped v2 secret key.
- **Headless?** No (minting a key needs a dashboard Admin), but **irrelevant** since the key already
  exists and works.
- **Config entities have live test data** in the sandbox project ("Create an app called Keboola"):
  1 app, 3 products, 1 entitlement, 1 offering, 3 packages → solid VCR fixtures for those tables.
- **CAVEAT (not a blocker): the test project has NO customer data. [LIVE]**
  `GET /v2/projects/{id}/customers` returns `200` with `items: []`. The customer/subscription/purchase/
  invoice tables — the richest part of the extractor — have **no real payload to fixture** from this
  project. Endpoints are reachable, so we can record "empty list" cassettes and exercise pagination,
  but not realistic per-customer content.
  **Phase 5 options:** (a) hand-craft/synthesize customer fixtures from the documented v2 schema, or
  (b) obtain a RevenueCat project with real subscriber data. Flagged to the team lead for the human's
  call.

### Open Tier C questions for Phase 3 (none blocking)
1. **v2-only scope** — confirm acceptable (no v1-only must-have entity identified).
2. **Empty-customers test data** — synthesize fixtures vs. obtain a project with real subscribers.

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
