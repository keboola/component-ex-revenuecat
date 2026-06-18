Configure the RevenueCat extractor with:

- **RevenueCat API Key** (`#api_key`) — required. The RevenueCat v2 secret key (`sk_…`). Stored encrypted.
- **Project** (`project_id`) — optional. Restrict extraction to a single RevenueCat project; leave empty
  to extract every project the key can access. Use **Load projects** to pick from a dropdown.
- **Entity groups** (`entities`) — which entity groups to extract: `config` (projects/apps/products/
  entitlements/offerings/packages) and/or `customers` (customers and their active entitlements,
  subscriptions, purchases, invoices). Defaults to both.

Writes are incremental upserts on each table's primary key, so re-runs do not duplicate rows.

Use **Test Connection** to verify the API key before running.
