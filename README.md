ex-revenuecat
=============

Extracts subscription and in-app-purchase analytics data from
[RevenueCat](https://www.revenuecat.com/) (REST API v2) into Keboola Storage.

**Table of Contents:**

[TOC]

Functionality Notes
===================

The component reads the RevenueCat v2 API and writes one Storage table per entity. It performs a
full refresh on every run (RevenueCat v2 exposes no updated-since cursor); writes are incremental
upserts on each table's primary key, so re-runs do not duplicate rows. A `last_run` watermark is
persisted in state for a future incremental mode.

Prerequisites
=============

A RevenueCat **v2 secret API key** (`sk_…`). Mint one in the RevenueCat dashboard under
**Settings → API keys** (V2 Secret key with read scopes). The key is project-scoped.

Features
========

| **Feature**         | **Description**                                                        |
|---------------------|------------------------------------------------------------------------|
| Generic UI Form     | Dynamic UI form for easy configuration.                                |
| Incremental Loading | Output tables are written as incremental upserts on the primary key.   |
| Test Connection     | Validates the API key before running.                                  |
| Project Selection   | Optionally scope extraction to a single RevenueCat project.            |
| Entity Selection    | Choose which entity groups to extract (skip the per-customer fan-out). |

Supported Endpoints
===================

Configuration entities: `projects`, `apps`, `products`, `entitlements`, `offerings`, `packages`.
Customer-domain entities: `customers`, `customer_active_entitlements`, `subscriptions`,
`subscription_entitlements`, `purchases`, `invoices`.

If you need additional endpoints, please submit your request to
[ideas.keboola.com](https://ideas.keboola.com/).

Configuration
=============

| Parameter    | Required | Description                                                                       |
|--------------|----------|-----------------------------------------------------------------------------------|
| `#api_key`   | yes      | RevenueCat v2 secret key (`sk_…`). Stored encrypted.                              |
| `project_id` | no       | Restrict to one project; empty extracts all projects the key can access.          |
| `entities`   | no       | Entity groups to extract: `config` and/or `customers`. Defaults to both.          |
| `load_type`  | no       | `full_load` (only mode for v1). Writes are PK upserts regardless.                  |

Output
======

One Storage table per entity (default bucket). Each table has an explicit schema with native data
types and a primary key. Nested objects are flattened to scalar columns; the subscription→entitlement
relationship is written to the `subscription_entitlements` linkage table. The `purchases` and
`invoices` tables may be empty (header/manifest only) for accounts without store-backed transactions.

Development
-----------

To customize the local data folder path, replace the `CUSTOM_FOLDER` placeholder with your desired path in the `docker-compose.yml` file:

~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    volumes:
      - ./:/code
      - ./CUSTOM_FOLDER:/data
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Clone this repository, initialize the workspace, and run the component using the following
commands:

~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
git clone  component-ex-revenuecat
cd component-ex-revenuecat
docker-compose build
docker-compose run --rm dev
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Run the test suite and perform lint checks using this command:

~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
docker-compose run --rm test
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Integration
===========

For details about deployment and integration with Keboola, refer to the
[deployment section of the developer
documentation](https://developers.keboola.com/extend/component/deployment/).
