"""
Unit tests for Component static flatteners.

Uses the real sample shapes from research §3 (live-confirmed against the seeded RevenueCat project).
Static methods are called directly on the class without instantiating Component, avoiding any
ComponentBase.__init__ side effects.
"""

from __future__ import annotations

import unittest

from component import Component


class TestFlattenProduct(unittest.TestCase):
    """
    _flatten_product lifts the `subscription` AND `one_time` sub-objects into scalar
    columns and drops `subscription`/`one_time`/`object`.

    Real shape (verified against the recorded products cassette):
        id, store_identifier, type,
        one_time -> null | {is_consumable: bool|null},
        subscription -> null | {duration, grace_period_duration, trial_duration},
        display_name, app_id, state, created_at, object

    `one_time` and `subscription` are mutually exclusive: subscription products carry
    `subscription` with `one_time: null`; one-time products carry `one_time` with
    `subscription: null`.
    """

    def _sample_product(self) -> dict:
        """A subscription product — `one_time` is null, `subscription` is the nested object."""
        return {
            "id": "prod_yearly",
            "store_identifier": "com.example.yearly",
            "type": "subscription",
            "one_time": None,
            "subscription": {
                "duration": "P1Y",
                "grace_period_duration": "P3D",
                "trial_duration": "P7D",
            },
            "display_name": "Yearly Pro",
            "app_id": "app419959e85c",
            "state": "published",
            "created_at": 1700000000000,
            "object": "product",
        }

    def test_subscription_fields_lifted(self) -> None:
        result = Component._flatten_product(self._sample_product())
        self.assertEqual(result["subscription_duration"], "P1Y")
        self.assertEqual(result["subscription_grace_period_duration"], "P3D")
        self.assertEqual(result["subscription_trial_duration"], "P7D")

    def test_subscription_key_removed(self) -> None:
        result = Component._flatten_product(self._sample_product())
        self.assertNotIn("subscription", result)

    def test_one_time_key_removed(self) -> None:
        result = Component._flatten_product(self._sample_product())
        self.assertNotIn("one_time", result)

    def test_object_key_removed(self) -> None:
        result = Component._flatten_product(self._sample_product())
        self.assertNotIn("object", result)

    def test_other_fields_preserved(self) -> None:
        result = Component._flatten_product(self._sample_product())
        self.assertEqual(result["id"], "prod_yearly")
        self.assertEqual(result["store_identifier"], "com.example.yearly")
        self.assertEqual(result["display_name"], "Yearly Pro")
        self.assertEqual(result["app_id"], "app419959e85c")
        self.assertEqual(result["state"], "published")
        self.assertEqual(result["created_at"], 1700000000000)

    def test_null_one_time_gives_none_consumable(self) -> None:
        """A subscription product (one_time: null) yields one_time_is_consumable=None."""
        result = Component._flatten_product(self._sample_product())
        self.assertIsNone(result["one_time_is_consumable"])

    def test_missing_subscription_gives_none_columns(self) -> None:
        product = self._sample_product()
        del product["subscription"]
        result = Component._flatten_product(product)
        self.assertIsNone(result["subscription_duration"])
        self.assertIsNone(result["subscription_grace_period_duration"])
        self.assertIsNone(result["subscription_trial_duration"])

    def test_null_subscription_gives_none_columns(self) -> None:
        product = self._sample_product()
        product["subscription"] = None
        result = Component._flatten_product(product)
        self.assertIsNone(result["subscription_duration"])
        self.assertIsNone(result["subscription_grace_period_duration"])
        self.assertIsNone(result["subscription_trial_duration"])

    def _sample_one_time_product(self) -> dict:
        """A lifetime one-time product — `one_time` is the nested object, `subscription` is null."""
        return {
            "id": "prod_lifetime",
            "store_identifier": "com.example.lifetime",
            "type": "one_time",
            "one_time": {"is_consumable": None},
            "subscription": None,
            "display_name": "Lifetime Pro",
            "app_id": "app419959e85c",
            "state": "published",
            "created_at": 1700000001000,
            "object": "product",
        }

    def test_one_time_product_no_subscription(self) -> None:
        """A lifetime one_time product has no subscription sub-object."""
        result = Component._flatten_product(self._sample_one_time_product())
        self.assertIsNone(result["subscription_duration"])
        self.assertNotIn("subscription", result)
        self.assertNotIn("one_time", result)
        self.assertNotIn("object", result)

    def test_one_time_object_flattened_to_consumable_column(self) -> None:
        """one_time: {is_consumable: null} -> one_time_is_consumable=None (matches real cassette)."""
        result = Component._flatten_product(self._sample_one_time_product())
        self.assertIn("one_time_is_consumable", result)
        self.assertIsNone(result["one_time_is_consumable"])

    def test_one_time_consumable_true_lifted(self) -> None:
        """A consumable one-time product surfaces the boolean as a scalar column."""
        product = self._sample_one_time_product()
        product["one_time"] = {"is_consumable": True}
        result = Component._flatten_product(product)
        self.assertIs(result["one_time_is_consumable"], True)

    def test_one_time_consumable_false_lifted(self) -> None:
        product = self._sample_one_time_product()
        product["one_time"] = {"is_consumable": False}
        result = Component._flatten_product(product)
        self.assertIs(result["one_time_is_consumable"], False)

    def test_missing_one_time_gives_none_consumable(self) -> None:
        product = self._sample_product()
        del product["one_time"]
        result = Component._flatten_product(product)
        self.assertIsNone(result["one_time_is_consumable"])


class TestFlattenSubscription(unittest.TestCase):
    """
    _flatten_subscription lifts total_revenue_in_usd sub-object AND extracts the nested
    entitlements list-envelope into linkage rows.
    Sample shape from research §3 (seeded store:promotional row).
    """

    def _sample_subscription(self) -> dict:
        return {
            "id": "sub_abc123",
            "object": "subscription",
            "customer_id": "keboola-test-001",
            "original_customer_id": "keboola-test-001",
            "product_id": None,
            "store": "promotional",
            "status": "active",
            "environment": "production",
            "ownership": "purchased",
            "gives_access": True,
            "auto_renewal_status": "will_not_renew",
            "starts_at": 1718000000000,
            "ends_at": 1798761600000,
            "current_period_starts_at": 1718000000000,
            "current_period_ends_at": 1798761600000,
            "store_subscription_identifier": None,
            "presented_offering_id": None,
            "management_url": None,
            "country": None,
            "pending_changes": None,
            "pending_payment": False,
            "total_revenue_in_usd": {
                "gross": 0.0,
                "proceeds": 0.0,
                "tax": 0.0,
                "commission": 0.0,
                "currency": "USD",
            },
            "entitlements": {
                "object": "list",
                "items": [
                    {
                        "id": "entlaa46372046",
                        "lookup_key": "Create an app called Keboola Pro",
                        "display_name": "Pro",
                        "state": "active",
                        "project_id": "proj2bfa9279",
                        "created_at": 1700000000000,
                        "object": "entitlement",
                    }
                ],
                "next_page": None,
                "url": "https://api.revenuecat.com/v2/...",
            },
        }

    def test_revenue_fields_lifted(self) -> None:
        flat, _ = Component._flatten_subscription(self._sample_subscription())
        self.assertEqual(flat["total_revenue_in_usd_gross"], 0.0)
        self.assertEqual(flat["total_revenue_in_usd_proceeds"], 0.0)
        self.assertEqual(flat["total_revenue_in_usd_tax"], 0.0)
        self.assertEqual(flat["total_revenue_in_usd_commission"], 0.0)
        self.assertEqual(flat["total_revenue_in_usd_currency"], "USD")

    def test_revenue_key_removed(self) -> None:
        flat, _ = Component._flatten_subscription(self._sample_subscription())
        self.assertNotIn("total_revenue_in_usd", flat)

    def test_entitlements_key_removed_from_flat_row(self) -> None:
        flat, _ = Component._flatten_subscription(self._sample_subscription())
        self.assertNotIn("entitlements", flat)

    def test_object_key_removed(self) -> None:
        flat, _ = Component._flatten_subscription(self._sample_subscription())
        self.assertNotIn("object", flat)

    def test_linkage_rows_correct_shape(self) -> None:
        _, linkage = Component._flatten_subscription(self._sample_subscription())
        self.assertEqual(len(linkage), 1)
        row = linkage[0]
        self.assertEqual(set(row.keys()), {"subscription_id", "entitlement_id"})
        self.assertEqual(row["subscription_id"], "sub_abc123")
        self.assertEqual(row["entitlement_id"], "entlaa46372046")

    def test_linkage_rows_do_not_contain_full_entitlement_columns(self) -> None:
        _, linkage = Component._flatten_subscription(self._sample_subscription())
        row = linkage[0]
        self.assertNotIn("lookup_key", row)
        self.assertNotIn("display_name", row)
        self.assertNotIn("state", row)

    def test_customer_id_preserved_in_flat_row(self) -> None:
        flat, _ = Component._flatten_subscription(self._sample_subscription())
        self.assertEqual(flat["customer_id"], "keboola-test-001")

    def test_missing_revenue_gives_none_columns(self) -> None:
        sub = self._sample_subscription()
        del sub["total_revenue_in_usd"]
        flat, _ = Component._flatten_subscription(sub)
        self.assertIsNone(flat["total_revenue_in_usd_gross"])
        self.assertIsNone(flat["total_revenue_in_usd_currency"])

    def test_null_revenue_gives_none_columns(self) -> None:
        sub = self._sample_subscription()
        sub["total_revenue_in_usd"] = None
        flat, _ = Component._flatten_subscription(sub)
        self.assertIsNone(flat["total_revenue_in_usd_gross"])

    def test_missing_entitlements_gives_empty_linkage(self) -> None:
        sub = self._sample_subscription()
        del sub["entitlements"]
        flat, linkage = Component._flatten_subscription(sub)
        self.assertEqual(linkage, [])
        self.assertNotIn("entitlements", flat)

    def test_empty_entitlements_items_gives_empty_linkage(self) -> None:
        sub = self._sample_subscription()
        sub["entitlements"]["items"] = []
        _, linkage = Component._flatten_subscription(sub)
        self.assertEqual(linkage, [])

    def test_multiple_entitlements_produce_multiple_linkage_rows(self) -> None:
        sub = self._sample_subscription()
        sub["entitlements"]["items"].append(
            {
                "id": "entl_second",
                "lookup_key": "extra",
                "display_name": "Extra",
                "state": "active",
                "project_id": "proj2bfa9279",
                "created_at": 1700000000001,
                "object": "entitlement",
            }
        )
        _, linkage = Component._flatten_subscription(sub)
        self.assertEqual(len(linkage), 2)
        ids = {r["entitlement_id"] for r in linkage}
        self.assertEqual(ids, {"entlaa46372046", "entl_second"})


class TestFlattenActiveEntitlement(unittest.TestCase):
    """
    _flatten_active_entitlement injects customer_id and yields {customer_id, entitlement_id, expires_at}.
    Live item shape: {entitlement_id, expires_at, object} — no top-level id, no customer_id.
    """

    def _sample_item(self) -> dict:
        return {
            "entitlement_id": "entlaa46372046",
            "expires_at": 1798761600000,
            "object": "customer.active_entitlement",
        }

    def test_output_shape_correct(self) -> None:
        result = Component._flatten_active_entitlement(self._sample_item(), "keboola-test-001")
        self.assertEqual(set(result.keys()), {"customer_id", "entitlement_id", "expires_at"})

    def test_customer_id_injected(self) -> None:
        result = Component._flatten_active_entitlement(self._sample_item(), "keboola-test-001")
        self.assertEqual(result["customer_id"], "keboola-test-001")

    def test_entitlement_id_preserved(self) -> None:
        result = Component._flatten_active_entitlement(self._sample_item(), "keboola-test-001")
        self.assertEqual(result["entitlement_id"], "entlaa46372046")

    def test_expires_at_preserved(self) -> None:
        result = Component._flatten_active_entitlement(self._sample_item(), "keboola-test-001")
        self.assertEqual(result["expires_at"], 1798761600000)

    def test_object_key_not_in_output(self) -> None:
        result = Component._flatten_active_entitlement(self._sample_item(), "keboola-test-001")
        self.assertNotIn("object", result)


class TestStripEnvelope(unittest.TestCase):
    """_strip_envelope removes object/next_page/url and preserves other keys."""

    def test_strips_all_envelope_keys(self) -> None:
        row = {
            "id": "proj2bfa9279",
            "name": "My Project",
            "object": "project",
            "next_page": None,
            "url": "https://api.revenuecat.com/v2/projects",
        }
        result = Component._strip_envelope(row)
        self.assertNotIn("object", result)
        self.assertNotIn("next_page", result)
        self.assertNotIn("url", result)
        self.assertEqual(result["id"], "proj2bfa9279")
        self.assertEqual(result["name"], "My Project")

    def test_missing_envelope_keys_no_error(self) -> None:
        row = {"id": "x", "name": "y"}
        result = Component._strip_envelope(row)
        self.assertEqual(result, {"id": "x", "name": "y"})

    def test_does_not_mutate_original(self) -> None:
        row = {"id": "x", "object": "project"}
        original_keys = set(row.keys())
        Component._strip_envelope(row)
        self.assertEqual(set(row.keys()), original_keys)


if __name__ == "__main__":
    unittest.main()
