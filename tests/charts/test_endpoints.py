"""Verify that one hostname controls browser, OIDC and certificate endpoints."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def render(chart, overrides):
    with tempfile.TemporaryDirectory() as directory:
        values = Path(directory) / "values.json"
        values.write_text(json.dumps(overrides))
        return subprocess.run(
            [
                "helm",
                "template",
                "test",
                str(ROOT / "charts/dify" / chart),
                "-f",
                str(ROOT / "tests/validation/helm-lint-values.yaml"),
                "-f",
                str(values),
            ],
            capture_output=True,
            text=True,
            check=False,
        )


class EndpointTests(unittest.TestCase):
    def test_single_hostname_reaches_all_consumers(self):
        for chart in ("api", "web", "oidc", "certificate-approval"):
            with self.subTest(chart=chart):
                result = render(
                    chart,
                    {
                        "dify": {"enabled": True, "hostname": "chat.example.test"},
                        "externalGateway": {"enabled": True},
                    },
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("chat.example.test", result.stdout)
                self.assertNotIn("dify.example.test", result.stdout)
                if chart == "oidc":
                    self.assertIn(
                        "https://chat.example.test/console/api/oauth/authorize/keycloak",
                        result.stdout,
                    )
                    self.assertIn("kind: ConfigMap", result.stdout)
                    self.assertIn("realm_access.roles", result.stdout)

    def test_invalid_or_competing_hostname_is_rejected(self):
        for chart in ("api", "web", "oidc", "certificate-approval"):
            for overrides in (
                {"dify": {"hostname": "https://wrong.test/path"}},
                {"dify": {"hostname": ""}},
                {"frontendDify": {"hostname": "different.example.test"}},
                {"authKeycloak": {"difyRedirectUri": "https://different.example.test/callback"}},
            ):
                with self.subTest(chart=chart, overrides=overrides):
                    self.assertNotEqual(render(chart, overrides).returncode, 0)
