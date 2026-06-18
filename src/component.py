"""
keboola.ex-revenuecat — main component.

Orchestrates extraction of RevenueCat v2 data into Keboola Storage tables.
"""

import csv
import json
import logging

import requests
from keboola.component.base import ComponentBase, sync_action
from keboola.component.dao import BaseType, ColumnDefinition
from keboola.component.exceptions import UserException
from keboola.component.sync_actions import MessageType, SelectElement, ValidationResult
from pydantic import ValidationError

from client.revenuecat_client import RevenueCatClient, RevenueCatClientError
from configuration import Configuration, EntityGroup

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# VCR sanitizers — read by the keboola.datadirtest scaffold CLI when recording
# cassettes. DefaultSanitizer:
#   - strips all headers except content-type, content-length, accept
#     (eliminates Authorization: Bearer sk_... from every cassette)
#   - redacts `api_key` wherever it appears in request/response bodies
#
# keboola.vcr ships only with keboola.datadirtest (a dev dependency), so it is
# ABSENT from the production image (built with `uv sync --no-dev`). The import is
# therefore guarded: in production VCR_SANITIZERS is simply an empty list and the
# component imports/runs normally; in the dev/test env (where keboola.vcr is
# present) the scaffold CLI picks up the real sanitizers. A hard top-level import
# would crash the production extractor with ModuleNotFoundError.
# ---------------------------------------------------------------------------
try:
    from keboola.vcr import DefaultSanitizer

    VCR_SANITIZERS = [DefaultSanitizer(additional_sensitive_fields=["api_key"])]
except ModuleNotFoundError:  # pragma: no cover - production image has no keboola.vcr
    VCR_SANITIZERS = []

# ---------------------------------------------------------------------------
# Per-table schemas — column order determines CSV field order.
# Native types applied per spec §6.2 and research §3.
# FLAG: purchases and invoices column types are docs-derived (empty fixtures in live test data;
#       no live rows available for verification — see spec §9 risk 1).
# ---------------------------------------------------------------------------

_S = BaseType.string
_I = BaseType.integer
_N = BaseType.numeric
_B = BaseType.boolean

SCHEMAS: dict[str, dict[str, ColumnDefinition]] = {
    "projects": {
        "id": ColumnDefinition(data_types=_S(), primary_key=True),
        "name": ColumnDefinition(data_types=_S()),
        "created_at": ColumnDefinition(data_types=_I()),
        "icon_url": ColumnDefinition(data_types=_S()),
        "icon_url_large": ColumnDefinition(data_types=_S()),
    },
    "apps": {
        "id": ColumnDefinition(data_types=_S(), primary_key=True),
        "name": ColumnDefinition(data_types=_S()),
        "type": ColumnDefinition(data_types=_S()),
        "project_id": ColumnDefinition(data_types=_S()),
        "created_at": ColumnDefinition(data_types=_I()),
    },
    "products": {
        "id": ColumnDefinition(data_types=_S(), primary_key=True),
        "store_identifier": ColumnDefinition(data_types=_S()),
        "type": ColumnDefinition(data_types=_S()),
        # `one_time` is a nested object on the API (null for subscriptions,
        # {"is_consumable": bool|null} for one-time products) — flattened to scalar
        # columns, mirroring how `subscription` is flattened below.
        "one_time_is_consumable": ColumnDefinition(data_types=_B()),
        "display_name": ColumnDefinition(data_types=_S()),
        "app_id": ColumnDefinition(data_types=_S()),
        "state": ColumnDefinition(data_types=_S()),
        "created_at": ColumnDefinition(data_types=_I()),
        "subscription_duration": ColumnDefinition(data_types=_S()),
        "subscription_grace_period_duration": ColumnDefinition(data_types=_S()),
        "subscription_trial_duration": ColumnDefinition(data_types=_S()),
    },
    "entitlements": {
        "id": ColumnDefinition(data_types=_S(), primary_key=True),
        "lookup_key": ColumnDefinition(data_types=_S()),
        "display_name": ColumnDefinition(data_types=_S()),
        "project_id": ColumnDefinition(data_types=_S()),
        "state": ColumnDefinition(data_types=_S()),
        "created_at": ColumnDefinition(data_types=_I()),
    },
    "offerings": {
        "id": ColumnDefinition(data_types=_S(), primary_key=True),
        "lookup_key": ColumnDefinition(data_types=_S()),
        "display_name": ColumnDefinition(data_types=_S()),
        "is_current": ColumnDefinition(data_types=_B()),
        "metadata": ColumnDefinition(data_types=_S()),
        "project_id": ColumnDefinition(data_types=_S()),
        "state": ColumnDefinition(data_types=_S()),
        "created_at": ColumnDefinition(data_types=_I()),
    },
    "packages": {
        "id": ColumnDefinition(data_types=_S(), primary_key=True),
        "offering_id": ColumnDefinition(data_types=_S()),
        "lookup_key": ColumnDefinition(data_types=_S()),
        "display_name": ColumnDefinition(data_types=_S()),
        "position": ColumnDefinition(data_types=_I()),
        "created_at": ColumnDefinition(data_types=_I()),
    },
    "customers": {
        # The v2 /customers object has no `created_at`; `first_seen_at` / `last_seen_at` are the
        # captured timestamps.
        "id": ColumnDefinition(data_types=_S(), primary_key=True),
        "project_id": ColumnDefinition(data_types=_S()),
        "first_seen_at": ColumnDefinition(data_types=_I()),
        "last_seen_at": ColumnDefinition(data_types=_I()),
    },
    "customer_active_entitlements": {
        "customer_id": ColumnDefinition(data_types=_S(), primary_key=True),
        "entitlement_id": ColumnDefinition(data_types=_S(), primary_key=True),
        "expires_at": ColumnDefinition(data_types=_I()),
    },
    "subscriptions": {
        "id": ColumnDefinition(data_types=_S(), primary_key=True),
        "customer_id": ColumnDefinition(data_types=_S()),
        "original_customer_id": ColumnDefinition(data_types=_S()),
        "product_id": ColumnDefinition(data_types=_S()),
        "store": ColumnDefinition(data_types=_S()),
        "status": ColumnDefinition(data_types=_S()),
        "environment": ColumnDefinition(data_types=_S()),
        "ownership": ColumnDefinition(data_types=_S()),
        "gives_access": ColumnDefinition(data_types=_B()),
        "auto_renewal_status": ColumnDefinition(data_types=_S()),
        "starts_at": ColumnDefinition(data_types=_I()),
        "ends_at": ColumnDefinition(data_types=_I()),
        "current_period_starts_at": ColumnDefinition(data_types=_I()),
        "current_period_ends_at": ColumnDefinition(data_types=_I()),
        "store_subscription_identifier": ColumnDefinition(data_types=_S()),
        "presented_offering_id": ColumnDefinition(data_types=_S()),
        "management_url": ColumnDefinition(data_types=_S()),
        "country": ColumnDefinition(data_types=_S()),
        "pending_payment": ColumnDefinition(data_types=_B()),
        "total_revenue_in_usd_gross": ColumnDefinition(data_types=_N()),
        "total_revenue_in_usd_proceeds": ColumnDefinition(data_types=_N()),
        "total_revenue_in_usd_tax": ColumnDefinition(data_types=_N()),
        "total_revenue_in_usd_commission": ColumnDefinition(data_types=_N()),
        "total_revenue_in_usd_currency": ColumnDefinition(data_types=_S()),
    },
    "subscription_entitlements": {
        "subscription_id": ColumnDefinition(data_types=_S(), primary_key=True),
        "entitlement_id": ColumnDefinition(data_types=_S(), primary_key=True),
    },
    # FLAG: purchases and invoices schemas are docs-derived — no live rows available for
    # verification (purchases/invoices are not API-creatable; only empty-list cassettes exist).
    "purchases": {
        "id": ColumnDefinition(data_types=_S(), primary_key=True),
        "customer_id": ColumnDefinition(data_types=_S()),
        "product_id": ColumnDefinition(data_types=_S()),
        "store": ColumnDefinition(data_types=_S()),
        "purchased_at": ColumnDefinition(data_types=_I()),
        "created_at": ColumnDefinition(data_types=_I()),
        "revenue_in_usd": ColumnDefinition(data_types=_N()),
    },
    "invoices": {
        "id": ColumnDefinition(data_types=_S(), primary_key=True),
        "customer_id": ColumnDefinition(data_types=_S()),
        "issued_at": ColumnDefinition(data_types=_I()),
        "created_at": ColumnDefinition(data_types=_I()),
        "total_amount": ColumnDefinition(data_types=_N()),
        "currency": ColumnDefinition(data_types=_S()),
    },
}

# Primary keys per table — derived from the schema (columns marked primary_key=True).
_PKS: dict[str, list[str]] = {
    name: [col for col, defn in schema.items() if defn.primary_key] for name, schema in SCHEMAS.items()
}


class Component(ComponentBase):
    """RevenueCat v2 extractor component."""

    def __init__(self) -> None:
        super().__init__()
        try:
            self._config = Configuration.model_validate(self.configuration.parameters, by_alias=True)
        except ValidationError as exc:
            raise UserException(f"Invalid configuration: {exc}") from exc
        self._client = RevenueCatClient(self._config.api_key)

    # ------------------------------------------------------------------
    # Orchestrator — under 30 lines
    # ------------------------------------------------------------------

    def run(self) -> None:
        try:
            all_projects = self._client.list_projects()
            if not all_projects:
                logger.info("No projects found for the given API key")
                return
            if self._config.project_id:
                project_ids = [self._config.project_id]
            else:
                project_ids = [p["id"] for p in all_projects if p.get("id")]
            if EntityGroup.config in self._config.entities:
                # The projects table is always the full parent dimension — every project the API key
                # can see — regardless of a configured project_id. The child entities below are what
                # the project_id filter scopes; the projects roster intentionally stays complete so a
                # downstream join always has the parent row available.
                self._write_table(
                    "projects",
                    [self._strip_envelope(p) for p in all_projects if p.get("id")],
                    _PKS["projects"],
                    SCHEMAS["projects"],
                )
                for pid in project_ids:
                    self._extract_config_entities(pid)
            if EntityGroup.customers in self._config.entities:
                for pid in project_ids:
                    self._extract_customer_domain(pid)
        except RevenueCatClientError as exc:
            raise self._map_client_error(exc) from exc
        except (KeyError, TypeError, ValueError) as exc:
            # A malformed-but-HTTP-200 payload (missing/oddly-typed keys) must surface as a
            # user-actionable error (exit 1), never an opaque exit 2.
            raise UserException(f"Unexpected RevenueCat API response shape: {exc}") from exc

    # ------------------------------------------------------------------
    # Traversal methods
    # ------------------------------------------------------------------

    def _extract_config_entities(self, pid: str) -> None:
        apps = [self._strip_envelope(a) for a in self._client.list_apps(pid)]
        logger.info("Project %s: fetched %d apps", pid, len(apps))
        self._write_table("apps", apps, _PKS["apps"], SCHEMAS["apps"])

        products = [self._flatten_product(p) for p in self._client.list_products(pid)]
        logger.info("Project %s: fetched %d products", pid, len(products))
        self._write_table("products", products, _PKS["products"], SCHEMAS["products"])

        entitlements = [self._strip_envelope(e) for e in self._client.list_entitlements(pid)]
        logger.info("Project %s: fetched %d entitlements", pid, len(entitlements))
        self._write_table("entitlements", entitlements, _PKS["entitlements"], SCHEMAS["entitlements"])

        offerings = [self._flatten_offering(o) for o in self._client.list_offerings(pid)]
        logger.info("Project %s: fetched %d offerings", pid, len(offerings))
        self._write_table("offerings", offerings, _PKS["offerings"], SCHEMAS["offerings"])

        packages: list[dict] = []
        for offering in offerings:
            oid = offering.get("id")
            if not oid:
                logger.warning("Project %s: skipping offering with no id: %s", pid, offering)
                continue
            for pkg in self._client.list_packages(pid, oid):
                row = self._strip_envelope(pkg)
                row["offering_id"] = oid
                packages.append(row)
        logger.info("Project %s: fetched %d packages", pid, len(packages))
        self._write_table("packages", packages, _PKS["packages"], SCHEMAS["packages"])

    def _extract_customer_domain(self, pid: str) -> None:
        raw_customers = self._client.list_customers(pid)
        customers = []
        for c in raw_customers:
            row = self._strip_envelope(c)
            row.setdefault("project_id", pid)
            customers.append(row)
        logger.info("Project %s: fetched %d customers", pid, len(customers))
        self._write_table("customers", customers, _PKS["customers"], SCHEMAS["customers"])

        active_ent_rows: list[dict] = []
        subscription_rows: list[dict] = []
        sub_ent_rows: list[dict] = []
        purchase_rows: list[dict] = []
        invoice_rows: list[dict] = []

        for idx, customer in enumerate(raw_customers, 1):
            cid = customer.get("id")
            if not cid:
                logger.warning("Project %s: skipping customer with no id: %s", pid, customer)
                continue
            logger.debug("Processing customer %d/%d (id: %s)", idx, len(raw_customers), cid)

            for item in self._client.list_customer_active_entitlements(pid, cid):
                active_ent_rows.append(self._flatten_active_entitlement(item, cid))

            for sub in self._client.list_customer_subscriptions(pid, cid):
                flat, linkage = self._flatten_subscription(sub)
                subscription_rows.append(flat)
                sub_ent_rows.extend(linkage)

            for purchase in self._client.list_customer_purchases(pid, cid):
                row = self._strip_envelope(purchase)
                row.setdefault("customer_id", cid)
                purchase_rows.append(row)

            for invoice in self._client.list_customer_invoices(pid, cid):
                row = self._strip_envelope(invoice)
                row.setdefault("customer_id", cid)
                invoice_rows.append(row)

        logger.info(
            "Project %s: active_entitlements=%d subscriptions=%d sub_entitlements=%d purchases=%d invoices=%d",
            pid,
            len(active_ent_rows),
            len(subscription_rows),
            len(sub_ent_rows),
            len(purchase_rows),
            len(invoice_rows),
        )
        self._write_table(
            "customer_active_entitlements",
            active_ent_rows,
            _PKS["customer_active_entitlements"],
            SCHEMAS["customer_active_entitlements"],
        )
        self._write_table("subscriptions", subscription_rows, _PKS["subscriptions"], SCHEMAS["subscriptions"])
        self._write_table(
            "subscription_entitlements",
            sub_ent_rows,
            _PKS["subscription_entitlements"],
            SCHEMAS["subscription_entitlements"],
        )
        self._write_table("purchases", purchase_rows, _PKS["purchases"], SCHEMAS["purchases"])
        self._write_table("invoices", invoice_rows, _PKS["invoices"], SCHEMAS["invoices"])

    # ------------------------------------------------------------------
    # Pure flatteners (static — no self needed)
    # ------------------------------------------------------------------

    @staticmethod
    def _strip_envelope(row: dict) -> dict:
        """Remove list-envelope keys that must not appear as columns."""
        result = dict(row)
        for key in ("object", "next_page", "url"):
            result.pop(key, None)
        return result

    @staticmethod
    def _flatten_offering(offering: dict) -> dict:
        """
        Strip the list envelope and JSON-serialize the `metadata` object into a STRING column.

        `metadata` is an arbitrary JSON object on the API. Writing it raw would coerce a populated
        object to Python's `str(dict)` (single-quoted, not valid JSON) in the CSV; json.dumps keeps
        it as parseable JSON. A None metadata stays None (empty cell), not the literal "null".
        """
        row = Component._strip_envelope(offering)
        metadata = row.get("metadata")
        if metadata is not None:
            row["metadata"] = json.dumps(metadata)
        return row

    @staticmethod
    def _flatten_product(product: dict) -> dict:
        """
        Flatten the nested `subscription` and `one_time` sub-objects into scalar columns.

        Input keys per research §3 (verified against the recorded products cassette):
            id, store_identifier, type,
            one_time -> null | {is_consumable: bool|null},
            subscription -> null | {duration, grace_period_duration, trial_duration},
            display_name, app_id, state, created_at, object

        `one_time` and `subscription` are mutually exclusive: subscription products carry
        `subscription` (with `one_time: null`); one-time products carry `one_time` (with
        `subscription: null`). Both are nested objects, so both are flattened — writing the
        raw object into a scalar BOOLEAN column is what broke the authoritative-types import.
        """
        row = dict(product)
        row.pop("object", None)

        sub = row.pop("subscription", None) or {}
        row["subscription_duration"] = sub.get("duration")
        row["subscription_grace_period_duration"] = sub.get("grace_period_duration")
        row["subscription_trial_duration"] = sub.get("trial_duration")

        one_time = row.pop("one_time", None) or {}
        row["one_time_is_consumable"] = one_time.get("is_consumable")
        return row

    @staticmethod
    def _flatten_subscription(subscription: dict) -> tuple[dict, list[dict]]:
        """
        Flatten `total_revenue_in_usd` and extract `entitlements` into linkage rows.

        Returns:
            flat_row: subscription dict with nested objects replaced by scalar columns.
            linkage_rows: list of {subscription_id, entitlement_id} dicts.
        """
        row = dict(subscription)
        row.pop("object", None)

        # Flatten total_revenue_in_usd
        revenue = row.pop("total_revenue_in_usd", None) or {}
        row["total_revenue_in_usd_gross"] = revenue.get("gross")
        row["total_revenue_in_usd_proceeds"] = revenue.get("proceeds")
        row["total_revenue_in_usd_tax"] = revenue.get("tax")
        row["total_revenue_in_usd_commission"] = revenue.get("commission")
        row["total_revenue_in_usd_currency"] = revenue.get("currency")

        # Extract nested entitlements list-envelope into linkage rows
        ent_envelope = row.pop("entitlements", None) or {}
        ent_items = ent_envelope.get("items") if isinstance(ent_envelope, dict) else []
        ent_items = ent_items or []
        sub_id = subscription.get("id")
        # Defensive `.get()` (matching the other flatteners); skip id-less items rather than emit a
        # linkage row with a null entitlement_id, which is half the table's composite primary key.
        linkage_rows = [
            {"subscription_id": sub_id, "entitlement_id": item.get("id")} for item in ent_items if item.get("id")
        ]

        return row, linkage_rows

    @staticmethod
    def _flatten_active_entitlement(item: dict, customer_id: str) -> dict:
        """
        Build a customer_active_entitlements row.

        Live item shape: {entitlement_id, expires_at, object} — no top-level id/customer_id.
        PK is customer_id + entitlement_id.
        """
        return {
            "customer_id": customer_id,
            "entitlement_id": item.get("entitlement_id"),
            "expires_at": item.get("expires_at"),
        }

    # ------------------------------------------------------------------
    # Output writer
    # ------------------------------------------------------------------

    def _write_table(
        self,
        name: str,
        rows: list[dict],
        primary_key: list[str],
        schema: dict[str, ColumnDefinition],
    ) -> None:
        """
        Write a headerless CSV + manifest for one output table.

        An empty rows list still produces a manifest and an empty CSV file — required for
        purchases/invoices which can be legitimately empty.
        """
        table_def = self.create_out_table_definition(
            name,
            primary_key=primary_key,
            incremental=True,
            schema=schema,
        )
        fieldnames = list(schema.keys())
        with open(table_def.full_path, mode="w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            # Headerless CSV — schema manifest names the columns.
            writer.writerows(rows)
        self.write_manifest(table_def)
        logger.debug("Wrote table %s: %d rows", name, len(rows))

    # ------------------------------------------------------------------
    # Error mapping
    # ------------------------------------------------------------------

    @staticmethod
    def _map_client_error(exc: RevenueCatClientError) -> UserException:
        """
        Map a RevenueCatClientError to a UserException with a plain-language message.

        Choice for exhausted-retry (retryable=True but retries depleted): surface as UserException
        so the user sees a readable message ("RevenueCat API temporarily unavailable after retries;
        please retry the job") rather than an opaque stack trace.  The user CAN retry the job, so
        UserException (exit 1) is the right signal — it is user-actionable in the sense that
        retrying or waiting is the correct remediation.
        """
        if exc.error_type == "authentication_error":
            return UserException("Invalid RevenueCat API key — check the #api_key value.")
        if exc.error_type == "resource_missing":
            return UserException(f"RevenueCat resource not found (project_id may be wrong): {exc.message}")
        if exc.retryable:
            return UserException(
                f"RevenueCat API temporarily unavailable after retries; please retry the job. "
                f"({exc.error_type}: {exc.message})"
            )
        return UserException(f"RevenueCat API error ({exc.error_type}): {exc.message}")

    # ------------------------------------------------------------------
    # Sync actions
    # ------------------------------------------------------------------

    @sync_action("testConnection")
    def test_connection(self) -> ValidationResult:
        """
        Validate the RevenueCat API key by probing GET /v2/projects?limit=1.

        Returns a ValidationResult (success or failure) — never raises an unhandled exception.
        The @sync_action wrapper catches any un-caught exception and writes it to stderr with
        exit 1, but the defensive try/except below ensures we always return a clean result object
        (visible as a coloured message in the UI) instead of an opaque internal-error banner.

        __init__ builds self._client from #api_key before this method is called.  A blank or
        missing key causes __init__ to raise UserException, which the @sync_action wrapper
        converts to a stderr message + exit 1 — surfaced cleanly to the user as a validation
        error, not an opaque failure.
        """
        try:
            self._client.test_connection()
            return ValidationResult("Connection successful.", MessageType.SUCCESS)
        except RevenueCatClientError as exc:
            if exc.error_type == "authentication_error":
                return ValidationResult(
                    "Invalid RevenueCat API key — check the #api_key value.",
                    MessageType.ERROR,
                )
            return ValidationResult(
                f"Connection test failed: {exc.message}",
                MessageType.ERROR,
            )
        except requests.RequestException as exc:
            return ValidationResult(
                f"Could not reach RevenueCat: {exc}",
                MessageType.ERROR,
            )
        except Exception as exc:  # noqa: BLE001
            return ValidationResult(
                f"Connection test failed: {exc}",
                MessageType.ERROR,
            )

    @sync_action("listProjects")
    def list_projects_action(self) -> list[SelectElement]:
        """
        Populate the project_id dropdown with projects visible to the configured API key.

        On a bad API key (authentication_error) the action raises UserException so the user sees a
        clear "check your key" message rather than a silently empty dropdown. Transient or
        unexpected failures still degrade to an empty list — the field is optional, so the user can
        type an id manually or leave it blank to extract all projects.

        Named list_projects_action to avoid shadowing RevenueCatClient.list_projects; the
        @sync_action decorator registers the action id "listProjects" independently of the
        Python method name.
        """
        try:
            projects = self._client.list_projects()
            result = []
            for p in projects:
                project_id = p.get("id")
                if not project_id:
                    # Skip malformed entries that have no id — cannot build a valid SelectElement.
                    logger.debug("Skipping project entry with no id: %s", p)
                    continue
                result.append(SelectElement(value=project_id, label=p.get("name") or project_id))
            return result
        except RevenueCatClientError as exc:
            if exc.error_type == "authentication_error":
                raise UserException("Invalid RevenueCat API key — check the #api_key value.") from exc
            logger.debug("listProjects sync action failed (RevenueCatClientError): %s", exc)
            return []
        except requests.RequestException as exc:
            logger.debug("listProjects sync action failed (RequestException): %s", exc)
            return []
        except Exception as exc:  # noqa: BLE001
            logger.debug("listProjects sync action failed (unexpected): %s", exc)
            return []


"""
Main entrypoint
"""
if __name__ == "__main__":
    try:
        comp = Component()
        # this triggers the run method by default and is controlled by the configuration.action parameter
        comp.execute_action()
    except UserException as exc:
        logging.exception(exc)
        exit(1)
    except Exception as exc:
        logging.exception(exc)
        exit(2)
