Configure the RevenueCat extractor with:

- **RevenueCat API Key** (`#api_key`) — required. The RevenueCat v2 secret key (`sk_…`). Stored encrypted.
- **Project** (`project_id`) — optional. Restrict extraction to a single RevenueCat project; leave empty
  to extract every project the key can access. Use **Load projects** to pick from a dropdown.
- **Entity groups** (`entities`) — which entity groups to extract: `config` (projects/apps/products/
  entitlements/offerings/packages) and/or `customers` (customers and their active entitlements,
  subscriptions, purchases, invoices). Defaults to both.
- **Load type** (`load_type`) — `full_load` (the only mode for now; RevenueCat v2 has no incremental
  cursor). Writes are incremental upserts on the primary key regardless.

Use **Test Connection** to verify the API key before running.
