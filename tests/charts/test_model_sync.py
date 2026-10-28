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
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import yaml

from test_endpoints import ROOT, render

SCRIPT = Path(
    os.environ.get(
        "DIFY_MODEL_SYNC_TEST_PAYLOAD", ROOT / "tooling/src/dify_tooling/payloads/sync_models.py"
    )
)
spec = importlib.util.spec_from_file_location("sync_models", SCRIPT)
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


def projection():
    value = {
        "schema_version": 1,
        "catalog_revision": "a" * 64,
        "metadata_revision": "b" * 64,
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


def counts(**values):
    return {**dict.fromkeys(("created", "updated", "unchanged", "skipped"), 0), **values}


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
        self.projection["models"][0]["metadata"]["provenance"]["source"] = value["z"]
        sign(self.projection)
        sync.validate_projection(self.projection, self.projection["projection_revision"], 1)
        with self.assertRaises(UnicodeEncodeError):
            sync.canonical_json({"invalid": "\ud800"})

    def setUp(self):
        self.projection = projection()
        self.backend = Backend()
        self.reports = []
        sleeper = patch.object(sync.time, "sleep")
        self.sleep = sleeper.start()
        self.addCleanup(sleeper.stop)

    def run_sync(self, mode="apply"):
        sync.synchronize(self.projection, self.backend, mode, "test-secret", self.reports.append)

    def test_preview_is_read_only_then_second_apply_does_not_validate(self):
        self.run_sync("preview")
        self.sleep.assert_not_called()
        self.assertEqual(self.backend.validations, 0)
        self.assertEqual(self.backend.records, [])
        self.assertEqual(self.reports[-1]["counts"], counts())
        self.assertEqual(self.reports[-1]["planned_counts"], counts(created=1))
        self.run_sync()
        self.assertEqual(self.backend.validations, 1)
        self.assertEqual(self.backend.attaches, 0)
        self.run_sync()
        self.assertEqual(self.backend.validations, 1)
        self.assertEqual(self.reports[-1]["counts"], counts(unchanged=1))
        self.assertNotIn("test-secret", json.dumps(self.reports))

    def test_real_helper_matches_tooling_result_contract_fixture(self):
        fixture = json.loads((ROOT / "tests/fixtures/model-sync-results.json").read_text())
        for mode in ("preview", "apply"):
            self.run_sync(mode)
            result = json.loads(json.dumps(self.reports[-1]))
            self.assertEqual(
                {key: result[key] for key in ("mode", "counts", "planned_counts")}, fixture[mode]
            )

    def test_partial_failure_retains_completed_counts_and_static_intent(self):
        first = self.projection["models"][0]
        second = copy.deepcopy(first)
        second["public_id"] = second["metadata"]["public_id"] = "example/second"
        self.projection["models"].append(second)
        backend = self.backend

        class PartialBackend:
            def read(self, model):
                return (
                    backend.read(model)
                    if model["public_id"] == first["public_id"]
                    else ([], [], [], [])
                )

            def create(self, **kwargs):
                if kwargs["model"] == second["public_id"]:
                    raise RuntimeError("sensitive upstream body")
                backend.create(**kwargs)

        with self.assertRaises(RuntimeError):
            sync.synchronize(
                self.projection, PartialBackend(), "apply", "test-secret", self.reports.append
            )
        result = self.reports[-1]
        self.assertEqual(result["counts"], counts(created=1))
        self.assertEqual(result["planned_counts"], counts(created=2))
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("sensitive", json.dumps(result))

    def multiple_models(self):
        first = self.projection["models"][0]
        second = copy.deepcopy(first)
        second["public_id"] = second["metadata"]["public_id"] = "example/second"
        self.projection["models"].append(second)
        states = {m["public_id"]: Backend() for m in self.projection["models"]}
        backend = SimpleNamespace(
            read=lambda model: states[model["public_id"]].read(model),
            create=lambda **kwargs: states[kwargs["model"]].create(**kwargs),
            update=lambda **kwargs: states[kwargs["model"]].update(**kwargs),
        )
        self.backend = backend
        return states

    def test_rate_limited_model_does_not_block_later_model_and_rerun_converges(self):
        states = self.multiple_models()
        first = states["example/model"]
        with patch.object(first, "create", side_effect=sync.ModelValidationFailure(429)) as create:
            with self.assertRaisesRegex(RuntimeError, "some model credentials"):
                self.run_sync()
            self.assertEqual(create.call_count, 3)
        self.assertEqual(first.credentials, [])
        self.assertEqual(states["example/second"].validations, 1)
        self.assertEqual(self.reports[-1]["counts"], counts(created=1))
        self.assertEqual(self.reports[-1]["planned_counts"], counts(created=2))
        self.assertEqual(
            self.reports[-1]["failures"], [{"index": 1, "status_code": 429, "attempts": 3}]
        )
        self.assertEqual([c.args[0] for c in self.sleep.call_args_list], [2, 4, 1])
        self.run_sync()
        self.assertEqual(self.reports[-1]["counts"], counts(created=1, unchanged=1))
        self.assertEqual(self.reports[-1]["failures"], [])
        self.assertEqual(states["example/second"].validations, 1)

    def test_transient_validation_retries_without_mutating_desired_credentials(self):
        for status in (429, 500, 502, 503, 504):
            with self.subTest(status=status):
                self.backend = Backend()
                original = self.backend.create
                attempts = []

                def create(**kwargs):
                    self.assertEqual(kwargs["credentials"]["api_key"], "test-secret")
                    attempts.append(1)
                    if len(attempts) < 3:
                        kwargs["credentials"]["api_key"] = "mutated"
                        raise sync.ModelValidationFailure(status)
                    original(**kwargs)

                with patch.object(self.backend, "create", side_effect=create):
                    self.run_sync()
                self.assertEqual(len(attempts), 3)
                self.assertEqual(self.backend.validations, 1)
                self.assertEqual(self.reports[-1]["status"], "complete")
                self.assertEqual(self.reports[-1]["counts"], counts(created=1))

    def test_model_rejections_continue_without_retry(self):
        for status in (400, 404, 422):
            with self.subTest(status=status):
                self.projection = projection()
                states = self.multiple_models()
                with patch.object(
                    states["example/model"],
                    "create",
                    side_effect=sync.ModelValidationFailure(status),
                ) as create:
                    with self.assertRaises(RuntimeError):
                        self.run_sync()
                create.assert_called_once()
                self.assertEqual(states["example/second"].validations, 1)
                self.assertEqual(self.reports[-1]["failures"][0]["attempts"], 1)

    def test_fatal_native_failure_stops_later_models_without_retry(self):
        states = self.multiple_models()
        with patch.object(
            states["example/model"], "create", side_effect=RuntimeError("private database error")
        ) as create:
            with self.assertRaises(RuntimeError):
                self.run_sync()
        create.assert_called_once()
        self.sleep.assert_not_called()
        self.assertEqual(states["example/second"].validations, 0)
        self.assertNotIn("private", json.dumps(self.reports))

    def test_retry_rechecks_expiry_and_ownership_after_backoff(self):
        for mutation in ("expiry", "ownership"):
            with self.subTest(mutation=mutation):
                self.projection = projection()
                self.backend = Backend()

                def mutate(_delay):
                    if mutation == "expiry":
                        self.projection["models"][0]["metadata"]["provenance"]["valid_until"] = (
                            "2000-01-01T00:00:00Z"
                        )
                    else:
                        self.backend.records.append(SimpleNamespace(credential_id="user"))

                with (
                    patch.object(sync.time, "sleep", side_effect=mutate),
                    patch.object(
                        self.backend, "create", side_effect=sync.ModelValidationFailure(429)
                    ) as create,
                    self.assertRaises(ValueError),
                ):
                    self.run_sync()
                create.assert_called_once()

    def test_native_boundary_only_classifies_recognized_prewrite_validation_errors(self):
        class CredentialsValidateFailedError(Exception):
            pass

        native = sync.NativeBackend.__new__(sync.NativeBackend)
        native.tenant_id = "tenant"
        module = SimpleNamespace(CredentialsValidateFailedError=CredentialsValidateFailedError)
        with patch.dict(sys.modules, {"graphon.model_runtime.errors.validate": module}):
            for status in (400, 404, 422, 429, 500, 502, 503, 504):
                message = (
                    f"Credentials validation failed with status code {status} "
                    "and response body private-key-and-prompt"
                )
                operation = Mock(side_effect=CredentialsValidateFailedError(message))
                with self.assertRaises(sync.ModelValidationFailure) as raised:
                    native._save(operation)
                self.assertEqual(raised.exception.status_code, status)
                self.assertNotIn("private", str(raised.exception))
                operation.assert_called_once()
            for error in (
                CredentialsValidateFailedError(
                    "Credentials validation failed with status code 401 and response body private"
                ),
                CredentialsValidateFailedError(
                    "Credentials validation failed with status code 403 and response body private"
                ),
                CredentialsValidateFailedError(
                    "Credentials validation failed with status code 409 and response body private"
                ),
                CredentialsValidateFailedError("unrecognized private error"),
                CredentialsValidateFailedError(
                    "prefix Credentials validation failed with status code 429 and response body x"
                ),
                ValueError(
                    "Credentials validation failed with status code 429 and response body private"
                ),
            ):
                with self.subTest(error=type(error)), self.assertRaises(type(error)):
                    native._save(Mock(side_effect=error))

    def test_base_binding_provider_and_safe_integer_boundaries(self):
        value = self.projection
        value["bindings"][0]["id"] = "1company"
        value["models"][0]["binding_id"] = value["models"][0]["metadata"]["binding_id"] = "1company"
        value["roles"][-1] = "provider:1" + "a" * 47 + ":company-use"
        value["models"][0]["metadata"]["limits"] = dict.fromkeys(
            ("context_tokens", "max_input_tokens", "max_output_tokens"), sync.MAX_SAFE_INTEGER
        )
        sign(value)
        sync.validate_projection(value, value["projection_revision"], 1)
        values = {
            "frontendDify": {"modelSync": {"enabled": True, "projection": value}},
            "authKeycloak": {
                "difyAgentgatewayClientRoles": value["roles"],
                "agentgatewayClientRoles": value["roles"],
            },
        }
        for chart in ("model-sync", "plugin-daemon", "managed-keys", "oidc-agentgateway"):
            result = render(chart, values)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_metadata_boundary_rejections_and_hard_cloud_ceiling(self):
        invalid = []
        for field, value in (
            ("context_tokens", sync.MAX_SAFE_INTEGER + 1),
            ("max_output_tokens", 64001),
            ("max_input_tokens", 64001),
            ("max_output_tokens", True),
            ("context_tokens", 64000.0),
        ):
            candidate = projection()
            candidate["models"][0]["metadata"]["limits"][field] = value
            invalid.append(candidate)
        for field, value in (
            ("source", "x" * 513),
            ("source", "invalid\nsource"),
            ("observed_at", "2020-01-01T00:00:00+00:00"),
            ("valid_until", "2099-02-30T00:00:00Z"),
        ):
            candidate = projection()
            candidate["models"][0]["metadata"]["provenance"][field] = value
            invalid.append(candidate)
        candidate = projection()
        candidate["catalog_revision"] = "not-a-digest"
        invalid.append(candidate)
        candidate = projection()
        candidate["bindings"] *= 257
        invalid.append(candidate)
        candidate = projection()
        candidate["models"][0]["metadata"]["gateway_restrictions"]["evidence_refs"] = [
            str(i) for i in range(33)
        ]
        invalid.append(candidate)
        for index, candidate in enumerate(invalid):
            with self.subTest(index=index), self.assertRaises(ValueError):
                sign(candidate)
                sync.validate_projection(candidate, candidate["projection_revision"], 1)
        with self.assertRaisesRegex(ValueError, "company model cap"):
            sync.validate_projection(self.projection, self.projection["projection_revision"], 28)
        for raw in (
            '{"duplicate":1,"duplicate":2}',
            '{"limit":1.0}',
            '{"limit":NaN}',
            '{"limit":9007199254740992}',
        ):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                sync.load_projection(raw)

    def test_update_preserves_ids_active_selection_default_and_disabled_state(self):
        self.run_sync()
        record = self.backend.records[0]
        record.is_valid = False
        self.backend.settings.append(SimpleNamespace(enabled=False))
        self.backend.defaults.append(SimpleNamespace(model="existing-default"))
        before = copy.deepcopy((self.backend.records, self.backend.settings, self.backend.defaults))
        self.projection["models"][0]["metadata"]["limits"]["context_tokens"] = 128000
        self.projection["models"][0]["metadata"]["limits"]["max_input_tokens"] = 124000
        self.run_sync()
        self.assertEqual(self.backend.validations, 2)
        self.assertEqual(self.reports[-1]["counts"], counts(updated=1))
        self.assertEqual(self.reports[-1]["planned_counts"], counts(updated=1))
        self.assertEqual(self.backend.attaches, 0)
        self.assertEqual(
            (self.backend.records, self.backend.settings, self.backend.defaults), before
        )
        self.assertEqual(self.backend.credentials[0].id, "credential-original")

    def test_inactive_owned_credential_is_a_nonmutating_conflict(self):
        self.run_sync()
        self.backend.records[0].credential_id = "user-selected-other-credential"
        self.projection["models"][0]["metadata"]["limits"]["max_output_tokens"] = 9000
        before = copy.deepcopy(self.backend.credentials)
        self.run_sync()
        self.assertEqual(self.backend.validations, 1)
        self.assertEqual(self.backend.credentials, before)
        self.assertEqual(self.reports[-1]["counts"], counts(skipped=1))
        self.assertEqual(self.reports[-1]["reasons"], {"skip_active_credential_conflict": 1})

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
        self.assertEqual(self.reports[-1]["counts"], counts())
        self.assertEqual(self.reports[-1]["planned_counts"], counts(created=1))
        self.assertEqual(self.reports[-1]["status"], "failed")
        self.run_sync()
        self.assertEqual(self.backend.validations, 1)
        self.assertEqual(self.reports[-1]["counts"], counts(unchanged=1))

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
        self.assertEqual(self.reports[-1]["counts"], counts(skipped=1))
        self.assertEqual(self.reports[-1]["reasons"], {"skip_unmanaged_conflict": 1})
        self.backend.defaults.append(SimpleNamespace(model="default"))
        self.run_sync()
        self.assertEqual(self.reports[-1]["counts"], counts(skipped=1))
        self.assertEqual(self.reports[-1]["reasons"], {"skip_unmanaged_default": 1})
        self.assertEqual(self.backend.validations, 0)

    def test_missing_metadata_or_limits_do_not_guess(self):
        model = self.projection["models"][0]
        model["metadata"]["limits"]["max_output_tokens"] = None
        self.run_sync()
        model["metadata"] = None
        self.run_sync()
        self.assertEqual(self.backend.validations, 0)
        self.assertEqual(self.reports[-1]["counts"], counts(skipped=1))

    def test_revision_cap_stale_metadata_and_binding_collision_rejected(self):
        value = self.projection
        sync.validate_projection(value, value["projection_revision"], 1)
        with self.assertRaisesRegex(ValueError, "revision mismatch"):
            sync.validate_projection(value, "c" * 64, 1)
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
        self.assertEqual(set(support["data"]), {"projection.json", "job-template.json"})
        job = json.loads(support["data"]["job-template.json"])
        self.assertFalse(job["spec"]["template"]["spec"]["automountServiceAccountToken"])
        container = job["spec"]["template"]["spec"]["containers"][0]
        staging = job["spec"]["template"]["spec"]["initContainers"][0]
        manifest = yaml.safe_load((ROOT / "release/manifest.yaml").read_text())
        artifacts = {item["id"]: item["image"] for item in manifest["spec"]["artifacts"]}
        self.assertEqual(staging["image"], artifacts["tooling"])
        self.assertEqual(container["image"], artifacts["api"])
        self.assertEqual(staging["command"], ["dify-stage-payloads"])
        self.assertNotIn("env", staging)
        self.assertNotIn("envFrom", staging)
        self.assertEqual(staging["securityContext"]["runAsUser"], 65532)
        self.assertEqual(container["command"], ["python", "-B", "/work/payloads/sync_models.py"])
        self.assertIn(
            {"name": "payload", "mountPath": "/work/payloads", "readOnly": True},
            container["volumeMounts"],
        )
        self.assertNotIn("envFrom", container)
        self.assertEqual(
            {
                item["valueFrom"]["secretKeyRef"]["key"]
                for item in container["env"]
                if "valueFrom" in item
            },
            {
                "DB_PASSWORD",
                "REDIS_PASSWORD",
                "SECRET_KEY",
                "PLUGIN_DAEMON_KEY",
                "AGENTGATEWAY_API_KEY",
            },
        )
        for chart in ("managed-keys", "oidc-agentgateway"):
            self.assertNotEqual(render(chart, values).returncode, 0)
            values["authKeycloak"] = {
                "difyAgentgatewayClientRoles": self.projection["roles"],
                "agentgatewayClientRoles": self.projection["roles"],
            }
            self.assertEqual(render(chart, values).returncode, 0)
            del values["authKeycloak"]
