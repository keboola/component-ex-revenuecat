## RevenueCat extractor

Extracts subscription and in-app-purchase analytics data from [RevenueCat](https://www.revenuecat.com/)
into Keboola Storage using the RevenueCat **REST API v2**.

### What it extracts

The component pulls two groups of entities, selectable per configuration:

**Configuration entities** (`config`)
- `projects`, `apps`, `products`, `entitlements`, `offerings`, and `packages` (packages are read per offering).

**Customer-domain entities** (`customers`)
- `customers`, and per customer: `customer_active_entitlements`, `subscriptions`, `purchases`, and
  `invoices`. The nested subscription entitlements are written to a `subscription_entitlements` linkage
  table (`subscription_id` + `entitlement_id`) that joins to `entitlements`.

Each output table carries a primary key and is written with native data types. Tables are loaded
incrementally (upsert on the primary key), so re-runs do not duplicate rows.

### Authentication

Provide a RevenueCat **v2 secret API key** (`sk_…`). Mint one in the RevenueCat dashboard under
**Settings → API keys** (a V2 secret key with read scopes). The key is project-scoped and stored
encrypted in Keboola.

### Loading

RevenueCat v2 exposes no "updated-since" cursor, so the component performs a full refresh on every run.
Writes are incremental upserts on the primary key. A `last_run` watermark is persisted to enable a future
incremental mode without a state migration.

### Notes

- The `purchases` and `invoices` tables can be empty for accounts without store-backed transactions —
  they are still written (header/manifest only) so downstream mappings remain stable.
- Use the **Entity groups** option to skip the heavier per-customer fan-out when only the configuration
  catalog is needed.
