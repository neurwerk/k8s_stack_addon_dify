"""Protect direct credential delivery and the unchanged application key contract."""

import re
import subprocess
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def render(chart):
    result = subprocess.run(
        [
            "helm",
            "template",
            "test",
            str(chart),
            "--namespace",
            "frontend-dify",
            "-f",
            str(ROOT / "tests/validation/helm-lint-values.yaml"),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return [obj for obj in yaml.safe_load_all(result.stdout) if obj]


class SecretDeliveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        result = subprocess.run(
            ["kustomize", "build", str(ROOT / "releases/dify/secret-sync")],
            check=True,
            capture_output=True,
            text=True,
        )
        cls.delivery = list(yaml.safe_load_all(result.stdout))
        cls.secrets = {
            obj["spec"]["target"]["name"]: obj
            for obj in cls.delivery
            if obj["kind"] == "ExternalSecret"
        }
        cls.charts = {
            chart.name: render(chart)
            for chart in (ROOT / "charts/dify").iterdir()
            if chart.is_dir()
        }

    def test_runtime_contract_maps_directly_to_canonical_records(self):
        spec = self.secrets["frontend-dify-secret"]["spec"]
        template = spec["target"]["template"]
        refs = {item["secretKey"]: item["remoteRef"] for item in spec["data"]}
        resolved = {}
        for key, expression in template["data"].items():
            match = re.fullmatch(r"\{\{ \.(\w+) \}\}", expression)
            self.assertIsNotNone(match)
            ref = refs[match[1]]
            resolved[key] = (ref["key"], ref["property"])
        self.assertEqual(
            resolved,
            {
                "SECRET_KEY": ("addons/dify/runtime", "secretKey"),
                "DB_PASSWORD": ("addons/dify/database", "postgresPassword"),
                "REDIS_PASSWORD": ("addons/dify/runtime", "redisPassword"),
                "SANDBOX_API_KEY": ("addons/dify/runtime", "sandboxApiKey"),
                "PLUGIN_DAEMON_KEY": ("addons/dify/runtime", "pluginDaemonKey"),
                "DIFY_AGENT_API_TOKEN": ("addons/dify/runtime", "difyAgentApiToken"),
                "DIFY_AGENT_SERVER_SECRET_KEY": ("addons/dify/runtime", "difyAgentServerSecretKey"),
                "DIFY_AGENT_SANDBOX_AUTH_TOKEN": (
                    "addons/dify/runtime",
                    "difyAgentSandboxAuthToken",
                ),
            },
        )
        self.assertEqual(template["mergePolicy"], "Replace")
        settings = self.charts["shared"][0]
        self.assertEqual(settings["kind"], "ConfigMap")
        imported = template["templateFrom"][0]["configMap"]
        self.assertEqual(imported["name"], settings["metadata"]["name"])
        self.assertEqual({item["key"] for item in imported["items"]}, set(settings["data"]))
        self.assertEqual(
            settings["data"],
            {
                "KEYCLOAK_OIDC_ISSUER_URL": "https://keycloak.example.test/realms/lint",
                "KEYCLOAK_OIDC_CLIENT_ID": "dify",
                "LLM_PROXY_ENDPOINT": "http://127.0.0.1:8080/v1",
            },
        )
        self.assertEqual(len(resolved) + len(settings["data"]), 11)

    def test_record_boundaries_and_database_browser_references_agree(self):
        def refs(name):
            return {
                item["secretKey"]: (item["remoteRef"]["key"], item["remoteRef"]["property"])
                for item in self.secrets[name]["spec"]["data"]
            }

        runtime = refs("frontend-dify-secret")
        app = refs("frontend-dify-openbao-secret")
        oidc = refs("dify-oidc-secret")
        self.assertEqual(
            refs("frontend-dify-postgres-password"),
            {
                "password": runtime["postgresPassword"],
            },
        )
        self.assertEqual(
            app,
            {
                "KEYCLOAK_OIDC_CLIENT_SECRET": ("addons/dify/oidc/browser", "difyOidcClientSecret"),
                "AUTO_SETUP_ADMIN_PASSWORD": ("addons/dify/admin", "initPassword"),
                "AGENTGATEWAY_API_KEY": ("addons/dify/gateway/key", "agentgatewayApiKey"),
            },
        )
        self.assertEqual(
            oidc,
            {
                "difyOidcClientSecret": app["KEYCLOAK_OIDC_CLIENT_SECRET"],
                "difyAgentgatewayClientSecret": (
                    "addons/dify/oidc/service",
                    "difyAgentgatewayClientSecret",
                ),
            },
        )
        self.assertEqual(
            refs("dify-managed-key-verifiers"),
            {
                field: ("addons/dify/gateway/verifiers", field)
                for field in (
                    "difyAgentgatewayPrimaryVerifierSha256",
                    "difyAgentgatewaySecondaryVerifierSha256",
                )
            },
        )

    def test_consumed_secret_keys_exist_and_helm_never_materializes_credentials(self):
        delivered = {}
        for name, resource in self.secrets.items():
            spec = resource["spec"]
            template = spec["target"].get("template", {})
            if "data" in template:
                keys = set(template["data"])
                keys.update(self.charts["shared"][0]["data"])
            else:
                keys = {item["secretKey"] for item in spec["data"]}
            delivered[name] = keys

        def inspect(value):
            if isinstance(value, dict):
                ref = value.get("secretKeyRef", {})
                if ref.get("name") in delivered:
                    self.assertIn(ref["key"], delivered[ref["name"]])
                for child in value.values():
                    inspect(child)
            elif isinstance(value, list):
                for child in value:
                    inspect(child)

        for objects in self.charts.values():
            for obj in objects:
                self.assertNotEqual(obj["kind"], "Secret")
                inspect(obj)
        for path in (ROOT / "releases/dify").rglob("*.yaml"):
            for obj in yaml.safe_load_all(path.read_text()):
                if isinstance(obj, dict) and obj.get("kind") == "HelmRelease":
                    for ref in obj["spec"].get("valuesFrom", []):
                        self.assertEqual(ref["kind"], "ConfigMap")
        self.assertNotIn("frontend-dify-runtime-secret", delivered)

    def test_secret_values_input_is_rejected(self):
        result = subprocess.run(
            [
                "helm",
                "template",
                "test",
                str(ROOT / "charts/dify/shared"),
                "--set",
                "difySecrets.SECRET_KEY=forbidden-synthetic-input",
            ],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ESO delivers credentials directly", result.stderr)


if __name__ == "__main__":
    unittest.main()
