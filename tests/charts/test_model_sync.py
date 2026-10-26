"""Behavioral safety checks for manual native registration, without inference."""

import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import unittest
import zipfile
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import yaml

from test_endpoints import ROOT, render

SCRIPT = ROOT / "charts/dify/model-sync/files/sync_models.py"
spec = importlib.util.spec_from_file_location("sync_models", SCRIPT)
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


def projection():
    value = {
        "schema_version": 1,
        "catalog_revision": "catalog-example",
        "metadata_revision": "metadata-example",
        "roles": ["llm:invoke", "model:example/model:invoke", "provider:example:company-use"],
        "bindings": [{"id": "company", "api_path": "/custom/chat"}],
        "models": [
            {
                "binding_id": "company",
                "public_id": "example/model",
                "credential_mode": "company",
                "metadata": {
                    "binding_id": "company",
                    "public_id": "example/model",
                    "limits": {
                        "context_tokens": 64000,
                        "max_input_tokens": 60000,
                        "max_output_tokens": 8000,
                    },
                    "capabilities": {key: "supported" for key in sync.CAPABILITIES},
                    "provenance": {
                        "kind": "reviewed_config",
                        "source": "reviewed-example",
                        "observed_at": "2020-01-01T00:00:00Z",
                        "valid_until": "2099-01-01T00:00:00Z",
                    },
                    "gateway_restrictions": {
                        "blocked_capabilities": ["image_input"],
                        "evidence_refs": ["policy-example"],
                    },
                },
            }
        ],
    }
    return sign(value)


def sign(value):
    unsigned = {key: item for key, item in value.items() if key != "projection_revision"}
    value["projection_revision"] = hashlib.sha256(sync.canonical_json(unsigned)).hexdigest()
    return value


class Backend:
    def __init__(self):
        self.records, self.credentials, self.settings, self.defaults = [], [], [], []
        self.validations, self.attaches = 0, 0

    def read(self, model):
        return self.records, self.credentials, self.settings, self.defaults

    def create(self, model, credentials, credential_name):
        self.validations += 1
        self.credentials.append(
            SimpleNamespace(
                id="credential-original",
                credential_name=credential_name,
                decrypted=copy.deepcopy(credentials),
            )
        )
        self.records.append(
            SimpleNamespace(id="model-original", credential_id="credential-original", is_valid=True)
        )

    def update(self, model, credentials, credential_name, credential_id):
        self.validations += 1
        credential = next(c for c in self.credentials if c.id == credential_id)
        credential.decrypted = copy.deepcopy(credentials)

    def attach(self, model, credential_id):
        self.attaches += 1
        self.records.append(
            SimpleNamespace(id="recovered", credential_id=credential_id, is_valid=True)
        )


class ModelSyncTests(unittest.TestCase):
    def test_go_json_hash_escaping_fixture_and_malformed_unicode(self):
        value = {"z": "&(<>) café\u2028\u2029", "a": 1}
        expected = '{"a":1,"z":"\\u0026(\\u003c\\u003e) café\\u2028\\u2029"}'.encode("utf-8")
        self.assertEqual(sync.canonical_json(value), expected)
        self.projection["catalog_revision"] = value["z"]
        sign(self.projection)
        sync.validate_projection(self.projection, self.projection["projection_revision"], 1)
        with self.assertRaises(UnicodeEncodeError):
            sync.canonical_json({"invalid": "\ud800"})

    def setUp(self):
        self.projection = projection()
        self.backend = Backend()
        self.reports = []

    def run_sync(self, mode="apply"):
        sync.synchronize(self.projection, self.backend, mode, "test-secret", self.reports.append)

    def test_preview_is_read_only_then_second_apply_does_not_validate(self):
        self.run_sync("preview")
        self.assertEqual(self.backend.validations, 0)
        self.assertEqual(self.backend.records, [])
        self.run_sync()
        self.assertEqual(self.backend.validations, 1)
        self.assertEqual(self.backend.attaches, 0)
        self.run_sync()
        self.assertEqual(self.backend.validations, 1)
        self.assertEqual(self.reports[-1]["counts"], {"noop": 1})
        self.assertNotIn("test-secret", json.dumps(self.reports))

    def test_update_preserves_ids_active_selection_default_and_disabled_state(self):
        self.run_sync()
        record = self.backend.records[0]
        record.is_valid = False
        record.credential_id = "user-selected-other-credential"
        self.backend.settings.append(SimpleNamespace(enabled=False))
        self.backend.defaults.append(SimpleNamespace(model="existing-default"))
        before = copy.deepcopy((self.backend.records, self.backend.settings, self.backend.defaults))
        self.projection["models"][0]["metadata"]["limits"]["context_tokens"] = 128000
        self.projection["models"][0]["metadata"]["limits"]["max_input_tokens"] = 124000
        self.run_sync()
        self.assertEqual(self.backend.validations, 2)
        self.assertEqual(self.backend.attaches, 0)
        self.assertEqual(
            (self.backend.records, self.backend.settings, self.backend.defaults), before
        )
        self.assertEqual(self.backend.credentials[0].id, "credential-original")

    def test_recovers_only_proven_credential_only_interruption(self):
        self.run_sync()
        self.backend.records.clear()
        self.run_sync()
        self.assertEqual(self.backend.attaches, 1)
        self.assertEqual(self.backend.validations, 1)
        self.run_sync()
        self.assertEqual(self.backend.attaches, 1)

    def test_native_commit_followed_by_failure_converges_without_revalidation(self):
        original = self.backend.create

        def commit_then_fail(**kwargs):
            original(**kwargs)
            raise RuntimeError("native cache failure")

        with patch.object(self.backend, "create", side_effect=commit_then_fail):
            with self.assertRaises(RuntimeError):
                self.run_sync()
        self.run_sync()
        self.assertEqual(self.backend.validations, 1)
        self.assertEqual(self.reports[-1]["counts"], {"noop": 1})

    def test_unsafe_interruption_and_identity_collision_fail_closed(self):
        self.run_sync()
        self.backend.credentials[0].decrypted["endpoint_url"] = "http://unmanaged.invalid/v1"
        with self.assertRaisesRegex(ValueError, "ownership collision"):
            self.run_sync()
        self.assertEqual(self.backend.validations, 1)
        self.backend.credentials[0].decrypted["endpoint_url"] = (
            "http://127.0.0.1:8080/bindings/company/v1"
        )
        self.backend.records.clear()
        self.backend.settings.append(SimpleNamespace(enabled=False))
        with self.assertRaisesRegex(ValueError, "unsafe interrupted"):
            self.run_sync()

    def test_unmanaged_model_and_default_remain_unchanged(self):
        self.backend.records.append(
            SimpleNamespace(id="user", credential_id="user", is_valid=False)
        )
        self.run_sync()
        self.assertEqual(self.reports[-1]["counts"], {"skip_unmanaged_conflict": 1})
        self.backend.defaults.append(SimpleNamespace(model="default"))
        self.run_sync()
        self.assertEqual(self.reports[-1]["counts"], {"skip_unmanaged_default": 1})
        self.assertEqual(self.backend.validations, 0)

    def test_missing_metadata_or_limits_do_not_guess(self):
        model = self.projection["models"][0]
        model["metadata"]["limits"]["max_output_tokens"] = None
        self.run_sync()
        model["metadata"] = None
        self.run_sync()
        self.assertEqual(self.backend.validations, 0)
        self.assertEqual(self.reports[-1]["counts"], {"skip_metadata": 1})

    def test_revision_cap_stale_metadata_and_binding_collision_rejected(self):
        value = self.projection
        sync.validate_projection(value, value["projection_revision"], 1)
        with self.assertRaisesRegex(ValueError, "revision mismatch"):
            sync.validate_projection(value, "wrong", 1)
        with self.assertRaisesRegex(ValueError, "cap exceeded"):
            sync.validate_projection(value, value["projection_revision"], 0)
        with self.assertRaisesRegex(ValueError, "stale"):
            sync.validate_projection(
                value, value["projection_revision"], 1, datetime(2100, 1, 1, tzinfo=timezone.utc)
            )
        value["models"].append(copy.deepcopy(value["models"][0]))
        sign(value)
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            sync.validate_projection(value, value["projection_revision"], 2)

    def test_bundled_plugin_schema_mapping_and_policy_restrictions(self):
        with zipfile.ZipFile(
            ROOT / "customizations/plugins/langgenius-openai_api_compatible_0.0.56.difypkg"
        ) as package:
            provider = yaml.safe_load(package.read("provider/openai_api_compatible.yaml"))
        schemas = [
            s
            for s in provider["model_credential_schema"]["credential_form_schemas"]
            if not s.get("show_on") or {"variable": "__model_type", "value": "llm"} in s["show_on"]
        ]
        credentials = sync.credentials_for(self.projection["models"][0], "test-secret")
        for schema in schemas:
            field = schema["variable"]
            if schema.get("required"):
                self.assertIn(field, credentials)
            if field in credentials and "options" in schema:
                self.assertIn(credentials[field], [o["value"] for o in schema["options"]])
        self.assertLessEqual(len(sync.credential_name(self.projection["models"][0])), 64)
        self.assertEqual(credentials["context_size"], "60000")
        self.assertEqual(credentials["max_tokens_to_sample"], "8000")
        self.assertEqual(credentials["vision_support"], "no_support")
        self.assertEqual(credentials["structured_output_support"], "not_supported")
        self.assertEqual(credentials["function_calling_type"], "tool_call")
        self.projection["models"][0]["metadata"]["capabilities"] = {
            key: "unknown" for key in sync.CAPABILITIES
        }
        unknown = sync.credentials_for(self.projection["models"][0], "test-secret")
        self.assertEqual(unknown["function_calling_type"], "no_call")
        self.assertEqual(unknown["stream_function_calling"], "not_supported")
        self.assertEqual(unknown["vision_support"], "no_support")

    def test_lock_contention_release_and_exception_cleanup(self):
        class Connection:
            def __init__(self, acquired):
                self.acquired = acquired
                self.unlocks = 0

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def execute(self, *args):
                pass

            def commit(self):
                pass

            def scalar(self, query, params):
                if "pg_advisory_unlock" in query:
                    self.unlocks += 1
                    return True
                return self.acquired

        with patch.dict(sys.modules, {"sqlalchemy": SimpleNamespace(text=lambda value: value)}):
            connection = Connection(False)
            with (
                self.assertRaises(TimeoutError),
                sync.bootstrap_lock(SimpleNamespace(connect=lambda: connection), timeout=0),
            ):
                self.fail("lock contention must not enter")
            self.assertEqual(connection.unlocks, 0)
            connection.acquired = True
            with (
                self.assertRaisesRegex(RuntimeError, "failure"),
                sync.bootstrap_lock(SimpleNamespace(connect=lambda: connection)),
            ):
                raise RuntimeError("failure")
            self.assertEqual(connection.unlocks, 1)

    def test_cli_errors_are_secret_safe(self):
        result = subprocess.run(
            [sys.executable, "-B", str(SCRIPT)],
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "DIFY_MODEL_SYNC_MODE": "secret-example",
                "LLM_PROXY_API_KEY": "secret-example",
            },
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("secret-example", result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stdout + result.stderr)

    def test_support_is_inert_and_roles_match(self):
        values = {"frontendDify": {"modelSync": {"enabled": True, "projection": self.projection}}}
        rendered = render("model-sync", values)
        self.assertEqual(rendered.returncode, 0, rendered.stderr)
        resources = list(yaml.safe_load_all(rendered.stdout))
        self.assertEqual(
            {r["kind"] for r in resources}, {"ConfigMap", "ServiceAccount", "NetworkPolicy"}
        )
        support = next(r for r in resources if r["kind"] == "ConfigMap")
        self.assertEqual(
            set(support["data"]), {"projection.json", "sync_models.py", "job-template.json"}
        )
        job = json.loads(support["data"]["job-template.json"])
        self.assertFalse(job["spec"]["template"]["spec"]["automountServiceAccountToken"])
        for chart in ("managed-keys", "oidc-agentgateway"):
            self.assertNotEqual(render(chart, values).returncode, 0)
            values["authKeycloak"] = {
                "difyAgentgatewayClientRoles": self.projection["roles"],
                "agentgatewayClientRoles": self.projection["roles"],
            }
            self.assertEqual(render(chart, values).returncode, 0)
            del values["authKeycloak"]
