"""
Behavioral unit tests for Component.run() and the listProjects sync action.

These build a real Component against a temp KBC_DATADIR (so ComponentBase wiring is exercised) but
patch the RevenueCatClient so no HTTP happens. They cover:
- T5: a malformed-but-200 payload (rows with no `id`) must not crash to exit 2 — id-less rows are
  skipped, and an unexpected shape maps to UserException (exit 1).
- T10: the listProjects sync action raises UserException on an authentication_error (so the user
  sees "check your key" rather than a silently empty dropdown), but degrades to [] on transient
  failures.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from keboola.component.exceptions import UserException

from client.revenuecat_client import RevenueCatClientError
from component import Component


def _make_datadir(tmp: Path, parameters: dict) -> Path:
    """Build a minimal KBC_DATADIR with a config.json and the out/tables dir."""
    data_dir = tmp / "data"
    (data_dir / "out" / "tables").mkdir(parents=True, exist_ok=True)
    (data_dir / "in" / "tables").mkdir(parents=True, exist_ok=True)
    (data_dir / "config.json").write_text(json.dumps({"parameters": parameters}), encoding="utf-8")
    return data_dir


class _ComponentTestBase(unittest.TestCase):
    def _build_component(self, parameters: dict, client_mock: mock.MagicMock) -> Component:
        self._tmp = tempfile.TemporaryDirectory()
        data_dir = _make_datadir(Path(self._tmp.name), parameters)
        self.addCleanup(self._tmp.cleanup)
        env_patch = mock.patch.dict(os.environ, {"KBC_DATADIR": str(data_dir)})
        env_patch.start()
        self.addCleanup(env_patch.stop)
        client_patch = mock.patch("component.RevenueCatClient", return_value=client_mock)
        client_patch.start()
        self.addCleanup(client_patch.stop)
        return Component()


class TestRunIdGuard(_ComponentTestBase):
    """T5 — id-less rows in a 200 payload must be skipped, not crash to exit 2."""

    def test_projects_without_id_are_skipped_not_crash(self) -> None:
        client = mock.MagicMock()
        # Two projects: one valid, one malformed with no id.
        client.list_projects.return_value = [
            {"id": "proj_ok", "name": "OK"},
            {"name": "no id here"},
        ]
        client.list_apps.return_value = []
        client.list_products.return_value = []
        client.list_entitlements.return_value = []
        client.list_offerings.return_value = []

        comp = self._build_component({"#api_key": "sk_test", "entities": ["config"]}, client)
        # Must not raise — the id-less project is skipped, only proj_ok is traversed.
        comp.run()

        # Config entities were fetched exactly once, for the single valid project id.
        client.list_apps.assert_called_once_with("proj_ok")

    def test_unexpected_shape_maps_to_user_exception(self) -> None:
        # A KeyError surfacing from a malformed-but-200 traversal must map to UserException (exit 1)
        # via run()'s broadened (KeyError, TypeError, ValueError) handler — never an opaque exit 2.
        client = mock.MagicMock()
        client.list_projects.return_value = [{"id": "proj_ok"}]
        client.list_apps.side_effect = KeyError("apps")

        comp = self._build_component({"#api_key": "sk_test", "entities": ["config"]}, client)
        with self.assertRaises(UserException):
            comp.run()


class TestListProjectsActionAuthRaise(_ComponentTestBase):
    """T10 — listProjects raises UserException on auth error, returns [] on transient failure."""

    def test_authentication_error_surfaces_to_user(self) -> None:
        # The @sync_action wrapper catches the raised UserException, writes its message to stderr,
        # and exits 1 (SystemExit). The key point of T10: an auth error is surfaced to the user,
        # NOT swallowed into a silently empty dropdown ([]).
        client = mock.MagicMock()
        client.list_projects.side_effect = RevenueCatClientError(
            error_type="authentication_error",
            message="Invalid API key",
            retryable=False,
            status_code=401,
        )
        comp = self._build_component({"#api_key": "sk_bad"}, client)
        with self.assertRaises(SystemExit) as ctx:
            comp.list_projects_action()
        self.assertEqual(ctx.exception.code, 1)

    def test_authentication_error_raises_user_exception_unwrapped(self) -> None:
        # Call the undecorated implementation to assert the raised type directly: a bad key must
        # raise UserException (which the wrapper then turns into the stderr+exit(1) above).
        client = mock.MagicMock()
        client.list_projects.side_effect = RevenueCatClientError(
            error_type="authentication_error",
            message="Invalid API key",
            retryable=False,
            status_code=401,
        )
        comp = self._build_component({"#api_key": "sk_bad"}, client)
        with self.assertRaises(UserException):
            comp.list_projects_action.__wrapped__(comp)

    def test_transient_error_returns_empty_list(self) -> None:
        client = mock.MagicMock()
        client.list_projects.side_effect = RevenueCatClientError(
            error_type="rate_limit_error",
            message="Too many requests",
            retryable=True,
            status_code=429,
        )
        comp = self._build_component({"#api_key": "sk_test"}, client)
        result = comp.list_projects_action()
        self.assertEqual(result, [])

    def test_id_less_project_skipped_in_dropdown(self) -> None:
        client = mock.MagicMock()
        client.list_projects.return_value = [
            {"id": "proj_ok", "name": "OK"},
            {"name": "no id"},
        ]
        comp = self._build_component({"#api_key": "sk_test"}, client)
        result = comp.list_projects_action()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].value, "proj_ok")


if __name__ == "__main__":
    unittest.main()
