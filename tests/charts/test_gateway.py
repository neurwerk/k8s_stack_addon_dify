"""Protect Dify's gateway identity, binding and network boundaries."""

import json
import subprocess
import unittest
from urllib.parse import urlsplit

import yaml

from test_endpoints import ROOT, render


class GatewayTests(unittest.TestCase):
    def documents(self, chart, values):
        result = render(chart, values)
        self.assertEqual(result.returncode, 0, result.stderr)
        return [item for item in yaml.safe_load_all(result.stdout) if item]

    def test_loopback_adapter_preserves_identity_and_streaming(self):
        objects = self.documents(
            "plugin-daemon", {"frontendDify": {"agentGateway": {"bindingId": "company-ai"}}}
        )
        config_map = next(item for item in objects if item["kind"] == "ConfigMap")
        config = config_map["data"]["nginx.conf"]
        shared = self.documents("shared", {})
        endpoint = urlsplit(
            next(item for item in shared if item["kind"] == "Secret")["stringData"][
                "LLM_PROXY_ENDPOINT"
            ]
        )
        self.assertEqual(endpoint.hostname, "127.0.0.1")
        self.assertIn(f"listen {endpoint.hostname}:{endpoint.port};", config)
        self.assertIn(f"location = {endpoint.path}/chat/completions", config)
        self.assertIn("if ($request_method != POST)", config)
        self.assertIn("/bindings/company-ai/v1/chat/completions;", config)
        self.assertIn(
            "infra-agentgateway-gateway.infra-agentgateway.svc.cluster.local:8082", config
        )
        self.assertIn("proxy_set_header X-Neurwerk-Authorization $http_authorization;", config)
        for header in ("Authorization", "Proxy-Authorization", "Cookie"):
            self.assertIn(f'proxy_set_header {header} "";', config)
        for directive in (
            "proxy_buffering off;",
            "proxy_request_buffering off;",
            "proxy_next_upstream off;",
            "access_log off;",
        ):
            self.assertIn(directive, config)
        deployment = next(item for item in objects if item["kind"] == "Deployment")
        pod = deployment["spec"]["template"]["spec"]
        sidecar = next(item for item in pod["containers"] if item["name"] == "gateway-adapter")
        mount = next(
            item for item in sidecar["volumeMounts"] if item.get("subPath") == "nginx.conf"
        )
        volume = next(item for item in pod["volumes"] if item["name"] == mount["name"])
        self.assertEqual(volume["configMap"]["name"], config_map["metadata"]["name"])
        self.assertIn(mount["mountPath"], sidecar["args"])
        self.assertNotIn("env", sidecar)
        self.assertIn("exec", sidecar["readinessProbe"])

    def test_binding_ids_reject_config_and_path_injection(self):
        for binding in ("local", "company-ai", "x" * 32):
            with self.subTest(binding=binding):
                self.documents(
                    "plugin-daemon", {"frontendDify": {"agentGateway": {"bindingId": binding}}}
                )
        for binding in (
            "",
            "x" * 33,
            "../local",
            "local; return 200; #",
            "LOCAL",
            "local_1",
            "https://other.test",
        ):
            with self.subTest(binding=binding):
                result = render(
                    "plugin-daemon", {"frontendDify": {"agentGateway": {"bindingId": binding}}}
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("bindingId", result.stderr)

    def test_company_grants_match_oidc_and_managed_keys(self):
        expected = {
            "llm:invoke",
            "provider:openrouter:company-use",
            "model:remote/example/model:invoke",
        }
        values = {
            "authKeycloak": {
                "agentgatewayClientRoles": sorted(expected),
                "difyAgentgatewayClientRoles": sorted(expected),
            }
        }
        oidc = self.documents("oidc-agentgateway", values)
        job = next(item for item in oidc if item["kind"] == "Job")
        env = {
            item["name"]: item.get("value")
            for item in job["spec"]["template"]["spec"]["containers"][0]["env"]
        }
        self.assertEqual(
            {item["roleName"] for item in json.loads(env["KC_SERVICE_ACCOUNT_ROLES"])}, expected
        )
        grants = next(
            item for item in self.documents("managed-keys", values) if item["kind"] == "ConfigMap"
        )
        for name in ("primary.json", "secondary.json"):
            self.assertEqual(set(json.loads(grants["data"][name])["permissions"]), expected)
        for bad in ("platform-admin", "provider:unknown:company-use"):
            rejected = {
                "authKeycloak": {
                    "agentgatewayClientRoles": sorted(expected | {"platform-admin"}),
                    "difyAgentgatewayClientRoles": ["llm:invoke", bad],
                }
            }
            for chart in ("oidc-agentgateway", "managed-keys"):
                with self.subTest(chart=chart, bad=bad):
                    self.assertNotEqual(render(chart, rejected).returncode, 0)

    def test_built_gateway_ingress_matches_plugin_egress(self):
        result = subprocess.run(
            ["kustomize", "build", str(ROOT / "releases/dify/managed-keys")],
            capture_output=True,
            text=True,
            check=True,
        )
        policy = next(
            item for item in yaml.safe_load_all(result.stdout) if item["kind"] == "NetworkPolicy"
        )
        self.assertEqual(policy["metadata"]["namespace"], "infra-agentgateway")
        incoming = policy["spec"]["ingress"][0]
        peer = incoming["from"][0]
        self.assertEqual(
            peer["namespaceSelector"]["matchLabels"]["kubernetes.io/metadata.name"], "frontend-dify"
        )
        self.assertEqual(
            peer["podSelector"]["matchLabels"]["app.kubernetes.io/name"], "plugin-daemon"
        )
        egress = next(
            item for item in self.documents("plugin-daemon", {}) if item["kind"] == "NetworkPolicy"
        )
        outgoing = next(
            rule
            for rule in egress["spec"]["egress"]
            if rule["to"][0]
            .get("namespaceSelector", {})
            .get("matchLabels", {})
            .get("kubernetes.io/metadata.name")
            == "infra-agentgateway"
        )
        self.assertIn("podSelector", outgoing["to"][0])
        self.assertEqual(incoming["ports"], [{"protocol": "TCP", "port": 8082}])
        self.assertEqual(outgoing["ports"], incoming["ports"])
