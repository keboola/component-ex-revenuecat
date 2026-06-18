"""
Output-content assertions for keboola.ex-revenuecat VCR functional tests.

These tests read the expected output files captured during cassette recording and
assert against the actual produced rows — composite PKs, linkage shapes, empty
tables, and column presence. They run offline (no HTTP, no cassette replay needed)
against the committed expected/ directories.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

FUNCTIONAL_DIR = Path(__file__).parent / "functional"


def _expected_tables(test_name: str) -> Path:
    return FUNCTIONAL_DIR / test_name / "expected" / "data" / "out" / "tables"


def _read_csv(path: Path, schema_names: list[str]) -> list[dict]:
    """Read a headerless CSV using schema column names (same order as schema)."""
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        return [dict(zip(schema_names, row, strict=False)) for row in reader]


def _read_manifest(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# -----------------------------------------------------------------------
# Test 01 — happy path: config entities
# -----------------------------------------------------------------------


class TestConfigEntities:
    """Assert all six config tables are present with real rows and correct schema."""

    tables = FUNCTIONAL_DIR / "01_happy_config_entities" / "expected" / "data" / "out" / "tables"

    @pytest.mark.skipif(
        not (FUNCTIONAL_DIR / "01_happy_config_entities").exists(),
        reason="01_happy_config_entities cassette not recorded",
    )
    def test_projects_table_present_and_non_empty(self) -> None:
        proj_file = self.tables / "projects"
        assert proj_file.exists(), "projects table missing"
        content = proj_file.read_text(encoding="utf-8").strip()
        assert content, "projects table is empty — expected at least 1 row"

    @pytest.mark.skipif(
        not (FUNCTIONAL_DIR / "01_happy_config_entities").exists(),
        reason="01_happy_config_entities cassette not recorded",
    )
    def test_projects_pk_column_in_manifest(self) -> None:
        manifest = _read_manifest(self.tables / "projects.manifest")
        pk_cols = [col["name"] for col in manifest.get("schema", []) if col.get("primary_key")]
        assert "id" in pk_cols, f"Expected 'id' as PK in projects manifest; got {pk_cols}"

    @pytest.mark.skipif(
        not (FUNCTIONAL_DIR / "01_happy_config_entities").exists(),
        reason="01_happy_config_entities cassette not recorded",
    )
    def test_products_has_flattened_subscription_columns(self) -> None:
        manifest = _read_manifest(self.tables / "products.manifest")
        col_names = [col["name"] for col in manifest.get("schema", [])]
        for expected_col in (
            "subscription_duration",
            "subscription_grace_period_duration",
            "subscription_trial_duration",
        ):
            assert expected_col in col_names, f"Missing flattened column '{expected_col}' in products manifest"

    @pytest.mark.skipif(
        not (FUNCTIONAL_DIR / "01_happy_config_entities").exists(),
        reason="01_happy_config_entities cassette not recorded",
    )
    def test_products_one_time_flattened_not_raw_object(self) -> None:
        """`one_time` is a nested object on the API; it must be flattened to a scalar
        `one_time_is_consumable` column. The raw scalar `one_time` column (which received a
        stringified dict and broke the authoritative-types import) must NOT be present."""
        manifest = _read_manifest(self.tables / "products.manifest")
        cols = {col["name"]: col for col in manifest.get("schema", [])}
        assert "one_time_is_consumable" in cols, "Missing flattened column 'one_time_is_consumable'"
        assert "one_time" not in cols, "Raw nested 'one_time' column must be flattened away"
        # The flattened column is a real BOOLEAN — coercible from the real values (null / bool).
        assert cols["one_time_is_consumable"]["data_type"]["base"]["type"] == "BOOLEAN"
        # No row may carry a stringified dict in the one_time_is_consumable position.
        rows = _read_csv(self.tables / "products", list(cols))
        for row in rows:
            val = row.get("one_time_is_consumable", "")
            assert "{" not in val, f"one_time_is_consumable holds a stringified object: {val!r}"

    @pytest.mark.skipif(
        not (FUNCTIONAL_DIR / "01_happy_config_entities").exists(),
        reason="01_happy_config_entities cassette not recorded",
    )
    def test_products_has_three_rows(self) -> None:
        manifest = _read_manifest(self.tables / "products.manifest")
        col_names = [col["name"] for col in manifest.get("schema", [])]
        rows = _read_csv(self.tables / "products", col_names)
        assert len(rows) == 3, f"Expected 3 products, got {len(rows)}"

    @pytest.mark.skipif(
        not (FUNCTIONAL_DIR / "01_happy_config_entities").exists(),
        reason="01_happy_config_entities cassette not recorded",
    )
    def test_packages_table_present_and_non_empty(self) -> None:
        pkg_file = self.tables / "packages"
        assert pkg_file.exists(), "packages table missing"
        content = pkg_file.read_text(encoding="utf-8").strip()
        assert content, "packages table is empty — expected at least 1 row"

    @pytest.mark.skipif(
        not (FUNCTIONAL_DIR / "01_happy_config_entities").exists(),
        reason="01_happy_config_entities cassette not recorded",
    )
    def test_all_config_tables_present(self) -> None:
        expected_tables = {"projects", "apps", "products", "entitlements", "offerings", "packages"}
        present = {f.name for f in self.tables.iterdir() if not f.name.endswith(".manifest")}
        missing = expected_tables - present
        assert not missing, f"Missing config tables: {missing}"

    @pytest.mark.skipif(
        not (FUNCTIONAL_DIR / "01_happy_config_entities").exists(),
        reason="01_happy_config_entities cassette not recorded",
    )
    def test_no_customer_tables_when_config_only(self) -> None:
        customer_tables = {
            "customers",
            "customer_active_entitlements",
            "subscriptions",
            "subscription_entitlements",
            "purchases",
            "invoices",
        }
        present = {f.name for f in self.tables.iterdir() if not f.name.endswith(".manifest")}
        leaked = customer_tables & present
        assert not leaked, f"Customer tables should NOT be present with entities=['config'], got: {leaked}"

    @pytest.mark.skipif(
        not (FUNCTIONAL_DIR / "01_happy_config_entities").exists(),
        reason="01_happy_config_entities cassette not recorded",
    )
    def test_manifests_incremental_true(self) -> None:
        for manifest_file in self.tables.glob("*.manifest"):
            manifest = _read_manifest(manifest_file)
            assert manifest.get("incremental") is True, (
                f"{manifest_file.name}: expected incremental=True, got {manifest.get('incremental')}"
            )


# -----------------------------------------------------------------------
# Test 02 — happy path: customer domain
# -----------------------------------------------------------------------


class TestCustomerDomain:
    """Assert customer domain tables have real rows with correct PKs and shapes."""

    tables = FUNCTIONAL_DIR / "02_happy_customer_domain" / "expected" / "data" / "out" / "tables"

    @pytest.mark.skipif(
        not (FUNCTIONAL_DIR / "02_happy_customer_domain").exists(),
        reason="02_happy_customer_domain cassette not recorded",
    )
    def test_customers_one_row(self) -> None:
        manifest = _read_manifest(self.tables / "customers.manifest")
        col_names = [col["name"] for col in manifest["schema"]]
        rows = _read_csv(self.tables / "customers", col_names)
        assert len(rows) == 1, f"Expected 1 customer, got {len(rows)}"
        assert rows[0]["id"] == "keboola-test-001", f"Unexpected customer id: {rows[0]['id']}"

    @pytest.mark.skipif(
        not (FUNCTIONAL_DIR / "02_happy_customer_domain").exists(),
        reason="02_happy_customer_domain cassette not recorded",
    )
    def test_customer_active_entitlements_composite_pk(self) -> None:
        manifest = _read_manifest(self.tables / "customer_active_entitlements.manifest")
        pk_cols = [col["name"] for col in manifest["schema"] if col.get("primary_key")]
        assert set(pk_cols) == {"customer_id", "entitlement_id"}, (
            f"Expected composite PK {{customer_id, entitlement_id}}, got {pk_cols}"
        )

    @pytest.mark.skipif(
        not (FUNCTIONAL_DIR / "02_happy_customer_domain").exists(),
        reason="02_happy_customer_domain cassette not recorded",
    )
    def test_customer_active_entitlements_one_row_real_data(self) -> None:
        manifest = _read_manifest(self.tables / "customer_active_entitlements.manifest")
        col_names = [col["name"] for col in manifest["schema"]]
        rows = _read_csv(self.tables / "customer_active_entitlements", col_names)
        assert len(rows) == 1, f"Expected 1 active entitlement, got {len(rows)}"
        assert rows[0]["customer_id"] == "keboola-test-001"
        assert rows[0]["entitlement_id"] == "entlaa46372046"

    @pytest.mark.skipif(
        not (FUNCTIONAL_DIR / "02_happy_customer_domain").exists(),
        reason="02_happy_customer_domain cassette not recorded",
    )
    def test_subscriptions_one_row_promotional_store(self) -> None:
        manifest = _read_manifest(self.tables / "subscriptions.manifest")
        col_names = [col["name"] for col in manifest["schema"]]
        rows = _read_csv(self.tables / "subscriptions", col_names)
        assert len(rows) == 1, f"Expected 1 subscription, got {len(rows)}"
        assert rows[0]["store"] == "promotional", f"Expected store=promotional, got {rows[0]['store']}"
        assert rows[0]["customer_id"] == "keboola-test-001"

    @pytest.mark.skipif(
        not (FUNCTIONAL_DIR / "02_happy_customer_domain").exists(),
        reason="02_happy_customer_domain cassette not recorded",
    )
    def test_subscriptions_has_flattened_revenue_columns(self) -> None:
        manifest = _read_manifest(self.tables / "subscriptions.manifest")
        col_names = [col["name"] for col in manifest["schema"]]
        for col in ("total_revenue_in_usd_gross", "total_revenue_in_usd_proceeds", "total_revenue_in_usd_currency"):
            assert col in col_names, f"Missing flattened revenue column '{col}' in subscriptions"

    @pytest.mark.skipif(
        not (FUNCTIONAL_DIR / "02_happy_customer_domain").exists(),
        reason="02_happy_customer_domain cassette not recorded",
    )
    def test_subscription_entitlements_exactly_two_columns(self) -> None:
        manifest = _read_manifest(self.tables / "subscription_entitlements.manifest")
        col_names = [col["name"] for col in manifest["schema"]]
        assert set(col_names) == {"subscription_id", "entitlement_id"}, (
            f"subscription_entitlements must have exactly {{subscription_id, entitlement_id}}, got {col_names}"
        )

    @pytest.mark.skipif(
        not (FUNCTIONAL_DIR / "02_happy_customer_domain").exists(),
        reason="02_happy_customer_domain cassette not recorded",
    )
    def test_subscription_entitlements_composite_pk(self) -> None:
        manifest = _read_manifest(self.tables / "subscription_entitlements.manifest")
        pk_cols = {col["name"] for col in manifest["schema"] if col.get("primary_key")}
        assert pk_cols == {"subscription_id", "entitlement_id"}, f"Expected composite PK, got {pk_cols}"

    @pytest.mark.skipif(
        not (FUNCTIONAL_DIR / "02_happy_customer_domain").exists(),
        reason="02_happy_customer_domain cassette not recorded",
    )
    def test_subscription_entitlements_one_linkage_row(self) -> None:
        manifest = _read_manifest(self.tables / "subscription_entitlements.manifest")
        col_names = [col["name"] for col in manifest["schema"]]
        rows = _read_csv(self.tables / "subscription_entitlements", col_names)
        assert len(rows) == 1, f"Expected 1 linkage row, got {len(rows)}"
        assert rows[0]["entitlement_id"] == "entlaa46372046"

    @pytest.mark.skipif(
        not (FUNCTIONAL_DIR / "02_happy_customer_domain").exists(),
        reason="02_happy_customer_domain cassette not recorded",
    )
    def test_purchases_table_present_and_empty(self) -> None:
        purchases_file = self.tables / "purchases"
        assert purchases_file.exists(), "purchases table file missing — must be written even when empty"
        assert purchases_file.stat().st_size == 0, "purchases expected to be empty (0 bytes)"
        assert (self.tables / "purchases.manifest").exists(), "purchases.manifest missing"

    @pytest.mark.skipif(
        not (FUNCTIONAL_DIR / "02_happy_customer_domain").exists(),
        reason="02_happy_customer_domain cassette not recorded",
    )
    def test_invoices_table_present_and_empty(self) -> None:
        invoices_file = self.tables / "invoices"
        assert invoices_file.exists(), "invoices table file missing — must be written even when empty"
        assert invoices_file.stat().st_size == 0, "invoices expected to be empty (0 bytes)"
        assert (self.tables / "invoices.manifest").exists(), "invoices.manifest missing"

    @pytest.mark.skipif(
        not (FUNCTIONAL_DIR / "02_happy_customer_domain").exists(),
        reason="02_happy_customer_domain cassette not recorded",
    )
    def test_all_customer_tables_present(self) -> None:
        expected = {
            "customers",
            "customer_active_entitlements",
            "subscriptions",
            "subscription_entitlements",
            "purchases",
            "invoices",
        }
        present = {f.name for f in self.tables.iterdir() if not f.name.endswith(".manifest")}
        missing = expected - present
        assert not missing, f"Missing customer domain tables: {missing}"


# -----------------------------------------------------------------------
# Test 03 — entity selection: config only
# -----------------------------------------------------------------------


class TestEntitySelectionConfigOnly:
    """Assert that entities=['config'] produces only config tables, not customer tables."""

    tables = FUNCTIONAL_DIR / "03_entity_selection_config_only" / "expected" / "data" / "out" / "tables"

    @pytest.mark.skipif(
        not (FUNCTIONAL_DIR / "03_entity_selection_config_only").exists(),
        reason="03_entity_selection_config_only cassette not recorded",
    )
    def test_no_customer_tables(self) -> None:
        customer_tables = {
            "customers",
            "customer_active_entitlements",
            "subscriptions",
            "subscription_entitlements",
            "purchases",
            "invoices",
        }
        present = {f.name for f in self.tables.iterdir() if not f.name.endswith(".manifest")}
        leaked = customer_tables & present
        assert not leaked, f"Customer tables produced despite entities=['config']: {leaked}"

    @pytest.mark.skipif(
        not (FUNCTIONAL_DIR / "03_entity_selection_config_only").exists(),
        reason="03_entity_selection_config_only cassette not recorded",
    )
    def test_config_tables_present(self) -> None:
        expected_config = {"projects", "apps", "products", "entitlements", "offerings", "packages"}
        present = {f.name for f in self.tables.iterdir() if not f.name.endswith(".manifest")}
        missing = expected_config - present
        assert not missing, f"Expected config tables missing: {missing}"


# -----------------------------------------------------------------------
# Test 04 — testConnection valid
# -----------------------------------------------------------------------


class TestConnectionValid:
    """Assert testConnection with valid key returns a success ValidationResult."""

    cassettes = FUNCTIONAL_DIR / "04_testConnection_valid" / "source" / "data" / "cassettes"

    @pytest.mark.skipif(
        not (FUNCTIONAL_DIR / "04_testConnection_valid").exists(),
        reason="04_testConnection_valid cassette not recorded",
    )
    def test_sync_action_result_is_success(self) -> None:
        result_file = self.cassettes / "sync_action_result.json"
        assert result_file.exists(), "sync_action_result.json missing for testConnection"
        result = json.loads(result_file.read_text(encoding="utf-8"))
        assert result.get("type") == "success", f"Expected type=success, got {result}"

    @pytest.mark.skipif(
        not (FUNCTIONAL_DIR / "04_testConnection_valid").exists(),
        reason="04_testConnection_valid cassette not recorded",
    )
    def test_recorded_2xx_response(self) -> None:
        requests_file = self.cassettes / "requests.json"
        cassette = json.loads(requests_file.read_text(encoding="utf-8"))
        statuses = [inter["response"]["status"]["code"] for inter in cassette.get("interactions", [])]
        assert all(200 <= s < 300 for s in statuses), f"Expected all 2xx, got statuses: {statuses}"


# -----------------------------------------------------------------------
# Test 05 — testConnection invalid key
# -----------------------------------------------------------------------


class TestConnectionInvalidKey:
    """Assert testConnection with invalid key returns a failure ValidationResult (not exception)."""

    cassettes = FUNCTIONAL_DIR / "05_testConnection_invalid_key" / "source" / "data" / "cassettes"

    @pytest.mark.skipif(
        not (FUNCTIONAL_DIR / "05_testConnection_invalid_key").exists(),
        reason="05_testConnection_invalid_key cassette not recorded",
    )
    def test_sync_action_result_is_error(self) -> None:
        result_file = self.cassettes / "sync_action_result.json"
        assert result_file.exists(), "sync_action_result.json missing for invalid key testConnection"
        result = json.loads(result_file.read_text(encoding="utf-8"))
        assert result.get("type") == "error", f"Expected type=error ValidationResult, got {result}"

    @pytest.mark.skipif(
        not (FUNCTIONAL_DIR / "05_testConnection_invalid_key").exists(),
        reason="05_testConnection_invalid_key cassette not recorded",
    )
    def test_exit_code_zero_sync_action_never_exits_nonzero(self) -> None:
        logs = json.loads((self.cassettes / "logs.json").read_text(encoding="utf-8"))
        # sync actions always succeed at the process level (exit 0 or None)
        assert logs.get("exit_code") in (0, None), f"sync action should exit 0, got {logs.get('exit_code')}"

    @pytest.mark.skipif(
        not (FUNCTIONAL_DIR / "05_testConnection_invalid_key").exists(),
        reason="05_testConnection_invalid_key cassette not recorded",
    )
    def test_recorded_401_response(self) -> None:
        cassette = json.loads((self.cassettes / "requests.json").read_text(encoding="utf-8"))
        statuses = [inter["response"]["status"]["code"] for inter in cassette.get("interactions", [])]
        assert 401 in statuses, f"Expected a 401 in the cassette, got {statuses}"


# -----------------------------------------------------------------------
# Test 06 — listProjects
# -----------------------------------------------------------------------


class TestListProjects:
    """Assert listProjects returns SelectElement list with at least one project."""

    cassettes = FUNCTIONAL_DIR / "06_listProjects" / "source" / "data" / "cassettes"

    @pytest.mark.skipif(
        not (FUNCTIONAL_DIR / "06_listProjects").exists(),
        reason="06_listProjects cassette not recorded",
    )
    def test_sync_action_result_is_list(self) -> None:
        result_file = self.cassettes / "sync_action_result.json"
        assert result_file.exists()
        result = json.loads(result_file.read_text(encoding="utf-8"))
        assert isinstance(result, list), f"listProjects should return a list, got {type(result)}"

    @pytest.mark.skipif(
        not (FUNCTIONAL_DIR / "06_listProjects").exists(),
        reason="06_listProjects cassette not recorded",
    )
    def test_project_select_element_shape(self) -> None:
        result = json.loads((self.cassettes / "sync_action_result.json").read_text(encoding="utf-8"))
        assert len(result) >= 1, "Expected at least 1 project in dropdown"
        element = result[0]
        assert "value" in element, f"SelectElement missing 'value': {element}"
        assert "label" in element, f"SelectElement missing 'label': {element}"
        assert element["value"] == "proj2bfa9279", f"Expected project proj2bfa9279, got {element['value']}"


# -----------------------------------------------------------------------
# Test 07 — bad auth run
# -----------------------------------------------------------------------


class TestBadAuthRun:
    """Assert run with invalid key exits 1 with authentication_error message."""

    cassettes = FUNCTIONAL_DIR / "07_bad_auth_run" / "source" / "data" / "cassettes"

    @pytest.mark.skipif(
        not (FUNCTIONAL_DIR / "07_bad_auth_run").exists(),
        reason="07_bad_auth_run cassette not recorded",
    )
    def test_exit_code_one(self) -> None:
        logs = json.loads((self.cassettes / "logs.json").read_text(encoding="utf-8"))
        assert logs.get("exit_code") == 1, f"Expected exit 1, got {logs.get('exit_code')}"

    @pytest.mark.skipif(
        not (FUNCTIONAL_DIR / "07_bad_auth_run").exists(),
        reason="07_bad_auth_run cassette not recorded",
    )
    def test_error_message_mentions_api_key(self) -> None:
        logs = json.loads((self.cassettes / "logs.json").read_text(encoding="utf-8"))
        stderr = logs.get("stderr", "")
        all_messages = " ".join(log.get("message", "") for log in logs.get("logs", []))
        combined = stderr + " " + all_messages
        assert "api_key" in combined.lower() or "api key" in combined.lower(), (
            f"Error message should mention api_key; got: {combined[:300]}"
        )

    @pytest.mark.skipif(
        not (FUNCTIONAL_DIR / "07_bad_auth_run").exists(),
        reason="07_bad_auth_run cassette not recorded",
    )
    def test_recorded_401_response(self) -> None:
        cassette = json.loads((self.cassettes / "requests.json").read_text(encoding="utf-8"))
        statuses = [inter["response"]["status"]["code"] for inter in cassette.get("interactions", [])]
        assert 401 in statuses, f"Expected 401 in bad_auth_run cassette, got {statuses}"


# -----------------------------------------------------------------------
# Test 08 — bad project id
# -----------------------------------------------------------------------


class TestBadProjectId:
    """Assert run with bad project_id exits 1 with resource_missing message."""

    cassettes = FUNCTIONAL_DIR / "08_bad_project_id" / "source" / "data" / "cassettes"

    @pytest.mark.skipif(
        not (FUNCTIONAL_DIR / "08_bad_project_id").exists(),
        reason="08_bad_project_id cassette not recorded",
    )
    def test_exit_code_one(self) -> None:
        expected = json.loads((self.cassettes / "expected_status.json").read_text(encoding="utf-8"))
        assert expected.get("exit_code") == 1, f"Expected exit 1, got {expected}"

    @pytest.mark.skipif(
        not (FUNCTIONAL_DIR / "08_bad_project_id").exists(),
        reason="08_bad_project_id cassette not recorded",
    )
    def test_error_message_mentions_project(self) -> None:
        logs = json.loads((self.cassettes / "logs.json").read_text(encoding="utf-8"))
        stderr = logs.get("stderr", "")
        all_messages = " ".join(log.get("message", "") for log in logs.get("logs", []))
        combined = stderr + " " + all_messages
        assert "project" in combined.lower() or "resource" in combined.lower(), (
            f"Error message should mention project/resource; got: {combined[:300]}"
        )
