"""Contracts and manual launch boundaries, without a cluster or provider inference."""

import copy
import json
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml
from k8s_stack_tooling.api.jobs import JobError
from k8s_stack_tooling.gateway_catalog.catalog import GatewayCatalog
from k8s_stack_tooling.gateway_catalog.metadata import (
    Capabilities,
    MetadataError,
    SourceModel,
    canonical_json,
    load_json,
    revision,
    validate_companion,
)
from kubernetes.client import ApiClient, CoreV1Api
from kubernetes.client.rest import ApiException
from pydantic import ValidationError
from urllib3.exceptions import HTTPError
from urllib3.response import HTTPResponse

from dify_tooling.model_sync import launcher, main
from dify_tooling.model_sync.projection import (
    build_projection,
    require_installed_projection,
)
from dify_tooling.payloads import sync_models as payload

NS = SimpleNamespace


def catalog():
    def entry(name, source, *, custom=False):
        path = "custom/api" if custom else "bindings/" + name
        return {
            "id": name,
            "provider_id": name,
            "display_name": name,
            "routes": [
                {
                    "id": name,
                    "url": f"https://gateway.example.test/{path}/v1",
                    "entry_mode": "api",
                    "caller_auth_modes": ["neurwerk_bearer"],
                }
            ],
            "destinations": [
                {
                    "id": name + "/chat",
                    "kind": "model",
                    "display_name": name,
                    "endpoint_ids": [name],
                    "profile_ids": ["openai-chat-completions-v1"],
                    "required_permissions": ["llm:invoke", f"model:{name}/chat:invoke"],
                    "credential_modes": [
                        {
                            "source": source,
                            "required_permissions": [f"provider:{name}:company-use"]
                            if source == "company"
                            else [],
                        }
                    ],
                    "provider_id": name,
                }
            ],
        }

    return GatewayCatalog.model_validate(
        {
            "schema_version": 2,
            "catalog_revision": "a" * 64,
            "policy_revision": "b" * 64,
            "profiles": [
                {
                    "id": "openai-chat-completions-v1",
                    "revision": "1",
                    "operations": [],
                    "limitations": [],
                }
            ],
            "self-hosted-api-providers": [entry("local", "none")],
            "external-api-providers": [
                entry("cloud", "company", custom=True),
                entry("personal", "caller_supplied"),
            ],
            "external-subscription-providers": [entry("subscription", "company")],
            "mcp-servers": [],
        }
    )


def record(binding="cloud"):
    now = datetime.now(UTC)
    return {
        "binding_id": binding,
        "public_id": binding + "/chat",
        "limits": {"context_tokens": 10000, "max_input_tokens": 9000, "max_output_tokens": 8000},
        "capabilities": {
            **dict.fromkeys(Capabilities.model_fields, "unknown"),
            "tool_calling": "supported",
            "image_input": "supported",
        },
        "provenance": {
            "kind": "provider_api",
            "source": "https://provider.example.test/models",
            "observed_at": (now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
            "valid_until": (now + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        },
        "gateway_restrictions": {
            "blocked_capabilities": ["image_input"],
            "evidence_refs": ["attachment-policy"],
        },
    }


def companion():
    value = {"schema_version": 1, "catalog_revision": "a" * 64, "models": [record()]}
    value["metadata_revision"] = revision(value, "metadata_revision")
    return value


def rehash(value):
    value["metadata_revision"] = revision(value, "metadata_revision")
    return value


def test_projection_and_native_credential_identity_match_immutable_legacy_fixture():
    metadata = companion()
    metadata["models"][0]["provenance"].update(
        observed_at="2020-01-01T00:00:00Z", valid_until="2099-01-01T00:00:00Z"
    )
    projected = build_projection(catalog(), rehash(metadata))
    # Generated with the consumer at shared Tooling 7103a9f22642b27916508dec8b2b0e68d8fa3072.
    expected = Path(__file__).parent / "fixtures/legacy-projection.json"
    assert canonical_json(projected).encode() == expected.read_bytes()
    assert payload.credential_name(projected["models"][0]) == (
        "gateway-sync-ef06bb6bb5b806c7f054737861b2eaa96ff5219e2084d0a4"
    )


@pytest.mark.parametrize("value", [True, "100", 1.5, 0, -1])
def test_limits_are_strict_and_positive(value):
    data = companion()
    data["models"][0]["limits"]["context_tokens"] = value
    with pytest.raises((ValidationError, MetadataError)):
        validate_companion(rehash(data), catalog())


def test_individual_limits_not_sum_and_restrictions_do_not_rewrite_upstream():
    validated = validate_companion(companion(), catalog())
    model = validated.models[0]
    assert model.limits.max_input_tokens is not None
    assert model.limits.max_output_tokens is not None
    assert model.limits.context_tokens is not None
    assert (
        model.limits.max_input_tokens + model.limits.max_output_tokens > model.limits.context_tokens
    )
    assert model.capabilities.image_input == "supported"
    assert model.gateway_restrictions.blocked_capabilities == ["image_input"]


@pytest.mark.parametrize(
    "mutation",
    [
        "extra",
        "limit",
        "unknown_capability",
        "expired",
        "future",
        "timezone",
        "duplicate",
        "pair",
        "schema",
    ],
)
def test_reject_invalid_metadata(mutation):
    value = companion()
    model = value["models"][0]
    if mutation == "extra":
        model["credentials"] = "not-allowed"
    elif mutation == "limit":
        model["limits"]["max_output_tokens"] = 10001
    elif mutation == "unknown_capability":
        model["gateway_restrictions"]["blocked_capabilities"] = ["invented"]
    elif mutation == "expired":
        model["provenance"].update(
            observed_at="2020-01-01T00:00:00Z", valid_until="2020-01-02T00:00:00Z"
        )
    elif mutation == "future":
        model["provenance"].update(
            observed_at="2090-01-01T00:00:00Z", valid_until="2090-01-02T00:00:00Z"
        )
    elif mutation == "timezone":
        model["provenance"]["observed_at"] = "2026-01-01T00:00:00+01:00"
    elif mutation == "duplicate":
        value["models"].append(copy.deepcopy(model))
    elif mutation == "pair":
        model["binding_id"] = "absent"
    else:
        value["schema_version"] = True
    with pytest.raises((ValidationError, MetadataError)):
        validate_companion(rehash(value), catalog())


def test_hashes_and_duplicate_keys_are_checked():
    assert (
        canonical_json({"z": "<&>\u2028\u2029café", "a": 1})
        == '{"a":1,"z":"\\u003c\\u0026\\u003e\\u2028\\u2029café"}'
    )
    with pytest.raises(MetadataError):
        load_json('{"models":[],"models":[]}')
    for invalid in ({"value": "\ud800"}, {"value": 1.0}, {"value": float("nan")}):
        with pytest.raises(MetadataError):
            canonical_json(invalid)
    value = companion()
    value["metadata_revision"] = "0" * 64
    with pytest.raises(MetadataError, match="revision"):
        validate_companion(value, catalog())
    value = companion()
    value["catalog_revision"] = "f" * 64
    with pytest.raises(MetadataError, match="primary"):
        validate_companion(rehash(value), catalog())


def test_projection_same_grants_selection_custom_routes_and_missing_default_metadata():
    projection = build_projection(catalog(), companion())
    assert [(m["binding_id"], m["credential_mode"]) for m in projection["models"]] == [
        ("cloud", "company"),
        ("local", "local"),
    ]
    assert projection["models"][1]["metadata"] is None
    assert projection["bindings"] == [
        {"id": "cloud", "api_path": "/custom/api"},
        {"id": "local", "api_path": "/bindings/local"},
    ]
    assert projection["roles"] == [
        "llm:invoke",
        "model:cloud/chat:invoke",
        "model:local/chat:invoke",
        "provider:cloud:company-use",
    ]
    assert revision(projection, "projection_revision") == projection["projection_revision"]
    require_installed_projection(projection, projection, projection["projection_revision"])
    stale = copy.deepcopy(projection)
    stale["metadata_revision"] = "0" * 64
    with pytest.raises(MetadataError):
        require_installed_projection(stale, projection, projection["projection_revision"])


@pytest.mark.parametrize("mutation", ["grant", "billing", "path", "responses"])
def test_projection_rejects_bad_grants_routes_or_excludes_non_chat(mutation):
    raw = catalog().model_dump(by_alias=True)
    entry = raw["external-api-providers"][0]
    if mutation == "grant":
        entry["destinations"][0]["required_permissions"] = ["llm:invoke"]
    elif mutation == "billing":
        entry["destinations"][0]["credential_modes"][0]["required_permissions"] = []
    elif mutation == "path":
        entry["routes"][0]["url"] = "https://gateway.example.test/custom/api/v1/"
    else:
        entry["destinations"][0]["profile_ids"] = []
        assert len(build_projection(GatewayCatalog.model_validate(raw), companion())["models"]) == 1
        return
    with pytest.raises(MetadataError):
        build_projection(GatewayCatalog.model_validate(raw), companion())


def template():
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"generateName": "sync-"},
        "spec": {
            "backoffLimit": 0,
            "template": {
                "spec": {
                    "restartPolicy": "Never",
                    "automountServiceAccountToken": False,
                    "initContainers": [
                        {
                            "name": "export-model-sync",
                            "image": "ghcr.io/neurwerk/k8s-stack-addon-dify-tooling:0.1.0@sha256:"
                            + "b" * 64,
                            "command": launcher.EXPORT_COMMAND,
                            "securityContext": {
                                "runAsNonRoot": True,
                                "runAsUser": 65532,
                                "runAsGroup": 65532,
                                "readOnlyRootFilesystem": True,
                                "allowPrivilegeEscalation": False,
                                "capabilities": {"drop": ["ALL"]},
                                "seccompProfile": {"type": "RuntimeDefault"},
                            },
                            "volumeMounts": [
                                {"name": "payload", "mountPath": launcher.PAYLOAD_DIRECTORY}
                            ],
                        }
                    ],
                    "containers": [
                        {
                            "name": "model-sync",
                            "image": "example.test/dify@sha256:" + "a" * 64,
                            "command": ["python", "-B", launcher.PAYLOAD_PATH],
                            "volumeMounts": [
                                {
                                    "name": "payload",
                                    "mountPath": launcher.PAYLOAD_DIRECTORY,
                                    "readOnly": True,
                                }
                            ],
                        }
                    ],
                    "volumes": [
                        {"name": "input", "configMap": {"name": launcher.INSTALLED}},
                        {"name": "payload", "emptyDir": {}},
                    ],
                }
            },
        },
    }


def cluster_inputs():
    primary = catalog()
    meta = companion()
    projection = build_projection(primary, meta)
    data = {
        "projection.json": json.dumps(projection),
        "job-template.json": json.dumps(template()),
    }
    maps = [
        NS(data={"catalog.json": primary.model_dump_json(by_alias=True)}),
        NS(data={"metadata.json": json.dumps(meta)}),
        NS(data=data),
    ]
    for i, item in enumerate(maps):
        item.metadata = NS(uid=str(i), resource_version="1")
    return projection, maps


def test_preview_launcher_snapshots_rechecks_and_never_calls_inference(monkeypatch):
    projection, maps = cluster_inputs()
    core, batch = Mock(), Mock()
    core.read_namespaced_config_map.side_effect = maps + maps
    batch.create_namespaced_job.return_value = NS(metadata=NS(uid="run-uid"))
    monkeypatch.setattr("requests.post", Mock(side_effect=AssertionError("inference forbidden")))
    result = launcher.launch(
        core,
        batch,
        catalog_namespace="catalog",
        namespace="dify",
        mode="preview",
        expected=projection["projection_revision"],
        max_company=0,
        wait_seconds=0,
    )
    assert result["status"] == "submitted"
    snapshot = core.create_namespaced_config_map.call_args.args[1]
    job = batch.create_namespaced_job.call_args.args[1]
    assert snapshot["immutable"]
    assert snapshot["data"] == {"projection.json": maps[2].data["projection.json"]}
    pod = job["spec"]["template"]["spec"]
    assert pod["volumes"][0]["configMap"]["name"] == snapshot["metadata"]["name"]
    assert {v["name"]: v["value"] for v in pod["containers"][0]["env"]}[
        "DIFY_MODEL_SYNC_MODE"
    ] == "preview"
    assert core.read_namespaced_config_map.call_count == 6


@pytest.mark.parametrize(
    "mutation", ["secret", "token", "image", "command", "root", "writable", "volume", "legacy"]
)
def test_launcher_requires_reviewed_credential_free_export_initializer(mutation):
    value = template()
    pod = value["spec"]["template"]["spec"]
    export = pod["initContainers"][0]
    if mutation == "secret":
        export["envFrom"] = [{"secretRef": {"name": "not-for-export"}}]
    elif mutation == "token":
        pod["automountServiceAccountToken"] = True
    elif mutation == "image":
        export["image"] = "ghcr.io/neurwerk/k8s-stack-addon-dify-tooling:latest"
    elif mutation == "command":
        export["command"] = ["dify-model-sync", "launch"]
    elif mutation == "root":
        export["securityContext"]["runAsUser"] = 0
    elif mutation == "writable":
        pod["containers"][0]["volumeMounts"][0]["readOnly"] = False
    elif mutation == "volume":
        pod["volumes"][1] = {"name": "payload", "secret": {"secretName": "not-code"}}
    else:
        pod.pop("initContainers")
    with pytest.raises(MetadataError):
        launcher.prepare_job(
            {"job-template.json": json.dumps(value)},
            namespace="dify",
            name="manual",
            mode="preview",
            expected="a" * 64,
            max_company=27,
        )


@pytest.mark.parametrize("mutation", ["changed", "stale", "cap", "retries"])
def test_launcher_fail_closed_before_creating_resources(mutation):
    projection, maps = cluster_inputs()
    again = copy.deepcopy(maps)
    cap = 27
    if mutation == "changed":
        again[2].metadata.resource_version = "2"
    elif mutation == "stale":
        maps[2].data["projection.json"] = "{}"
    elif mutation == "cap":
        cap = 0
    else:
        value = template()
        value["spec"]["backoffLimit"] = 1
        maps[2].data["job-template.json"] = json.dumps(value)
    core, batch = Mock(), Mock()
    core.read_namespaced_config_map.side_effect = maps + again
    with pytest.raises((MetadataError, JobError)):
        launcher.launch(
            core,
            batch,
            catalog_namespace="catalog",
            namespace="dify",
            mode="apply",
            expected=projection["projection_revision"],
            max_company=cap,
            wait_seconds=0,
        )
    core.create_namespaced_config_map.assert_not_called()
    batch.create_namespaced_job.assert_not_called()


def helper_summary(mode="apply"):
    return {
        "mode": mode,
        "counts": {
            "created": 0 if mode == "preview" else 1,
            "updated": 0,
            "unchanged": 3,
            "skipped": 1,
        },
        "planned_counts": {"created": 2, "updated": 1, "unchanged": 3, "skipped": 1},
    }


@pytest.mark.parametrize("mode", ["preview", "apply"])
def test_result_keeps_helper_completed_and_planned_counts_separate(mode):
    summary = helper_summary(mode)
    noisy = copy.deepcopy(summary)
    noisy["private_reason"] = "not forwarded"
    noisy["counts"]["private_errors"] = 1
    assert launcher.safe_summary("private error line\n" + json.dumps(noisy), mode=mode) == summary


@pytest.mark.parametrize("invalid", [True, -1, 257, 1.5, "1", None])
def test_result_uses_last_valid_bounded_summary_not_malformed_trailing_counts(invalid):
    valid = helper_summary()
    malformed = copy.deepcopy(valid)
    malformed["planned_counts"]["created"] = invalid
    logs = json.dumps(valid) + "\n" + json.dumps(malformed)
    assert launcher.safe_summary(logs, mode="apply") == valid


def test_result_rejects_wrong_mode_incomplete_oversized_or_mutating_preview_summary():
    assert launcher.safe_summary(json.dumps(helper_summary("preview")), mode="apply") == {}
    incomplete = helper_summary()
    incomplete.pop("planned_counts")
    assert launcher.safe_summary(json.dumps(incomplete), mode="apply") == {}
    oversized = {**helper_summary(), "private_detail": "x" * 16384}
    assert launcher.safe_summary(json.dumps(oversized), mode="apply") == {}
    preview = helper_summary("preview")
    preview["counts"]["created"] = 1
    assert launcher.safe_summary(json.dumps(preview), mode="preview") == {}


def test_result_keeps_last_valid_summary_including_plans():
    first = helper_summary()
    last = helper_summary()
    last["counts"]["created"] = 2
    last["planned_counts"]["created"] = 3
    logs = json.dumps(first) + "\nDIFY_MODEL_SYNC_RESULT " + json.dumps(last)
    assert launcher.safe_summary(logs, mode="apply") == last


def test_result_reports_only_safe_model_failure_details():
    failure = {"index": 15, "status_code": 429, "attempts": 3}
    summary = {**helper_summary(), "failures": [failure]}
    noisy = {**summary, "failures": [{**failure, "response_body": "private key and prompt"}]}
    assert launcher.safe_summary(json.dumps(noisy), mode="apply") == summary


@pytest.mark.parametrize(
    "failure",
    [
        {"index": True, "status_code": 429, "attempts": 3},
        {"index": 0, "status_code": 429, "attempts": 3},
        {"index": 257, "status_code": 429, "attempts": 3},
        {"index": 1, "status_code": "private", "attempts": 3},
        {"index": 1, "status_code": 401, "attempts": 3},
        {"index": 1, "status_code": 429, "attempts": 4},
        {"index": 1, "status_code": 429, "attempts": True},
    ],
)
def test_result_rejects_malformed_model_failure_details(failure):
    valid = helper_summary()
    malformed = {**valid, "failures": [failure]}
    assert launcher.safe_summary(json.dumps(malformed), mode="apply") == {}
    logs = json.dumps(valid) + "\n" + json.dumps(malformed)
    assert launcher.safe_summary(logs, mode="apply") == valid


def test_result_rejects_duplicate_oversized_and_preview_failures():
    failure = {"index": 1, "status_code": 429, "attempts": 3}
    for failures in ([failure, failure], [failure] * 257, "private error"):
        summary = {**helper_summary(), "failures": failures}
        assert launcher.safe_summary(json.dumps(summary), mode="apply") == {}
    summary = {**helper_summary("preview"), "failures": [failure]}
    assert launcher.safe_summary(json.dumps(summary), mode="preview") == {}


def test_uncertain_job_creation_is_named_and_not_retried():
    projection, maps = cluster_inputs()
    core, batch = Mock(), Mock()
    core.read_namespaced_config_map.side_effect = maps + maps
    batch.create_namespaced_job.side_effect = ApiException(status=504)
    result = launcher.launch(
        core,
        batch,
        catalog_namespace="catalog",
        namespace="dify",
        mode="apply",
        expected=projection["projection_revision"],
        max_company=27,
        wait_seconds=0,
    )
    assert result["status"] == "uncertain" and result["phase"] == "job"
    assert result["job"].startswith("frontend-dify-model-sync-")
    assert batch.create_namespaced_job.call_count == 1


def test_launcher_identifies_failed_models_from_verified_projection(monkeypatch):
    projection, maps = cluster_inputs()
    core, batch = Mock(), Mock()
    core.read_namespaced_config_map.side_effect = maps + maps
    batch.create_namespaced_job.return_value = NS(metadata=NS(uid="run-uid"))
    failure = {"index": 1, "status_code": 429, "attempts": 3}
    monkeypatch.setattr(
        launcher, "wait_result", lambda *a, **kw: {"status": "failed", "failures": [failure]}
    )
    result = launcher.launch(
        core,
        batch,
        catalog_namespace="catalog",
        namespace="dify",
        mode="apply",
        expected=projection["projection_revision"],
        max_company=27,
        wait_seconds=1,
    )
    assert result["status"] == "failed"
    assert result["failures"][0]["model"] == projection["models"][0]["public_id"]


@pytest.mark.parametrize(
    ("mode", "condition", "expected_status"),
    [
        ("preview", "Complete", "complete"),
        ("apply", "Complete", "complete"),
        ("apply", "Failed", "failed"),
    ],
)
def test_wait_reports_helper_plans_separately_on_preview_apply_and_partial_failure(
    mode, condition, expected_status
):
    core, batch = Mock(), Mock()
    batch.read_namespaced_job_status.return_value = NS(
        metadata=NS(uid="run-uid"), status=NS(conditions=[NS(type=condition, status="True")])
    )
    core.list_namespaced_pod.return_value = NS(
        items=[
            NS(
                metadata=NS(
                    name="run-pod",
                    owner_references=[NS(uid="run-uid", kind="Job", controller=True)],
                )
            )
        ]
    )
    summary = helper_summary(mode)
    if mode == "apply" and condition == "Complete":
        summary["counts"] = copy.deepcopy(summary["planned_counts"])
    response = Mock()
    response.read.return_value = json.dumps({**summary, "private_error": "omitted"}).encode()
    core.read_namespaced_pod_log.return_value = response
    assert launcher.wait_result(core, batch, "run", "dify", 1, mode=mode, uid="run-uid") == {
        "status": expected_status,
        **summary,
    }
    core.read_namespaced_pod_log.assert_called_once_with(
        "run-pod",
        "dify",
        container="model-sync",
        tail_lines=1,
        limit_bytes=16384,
        _preload_content=False,
        _request_timeout=launcher.TIMEOUT,
    )
    response.read.assert_called_once_with(16385)
    response.close.assert_called_once_with()
    response.release_conn.assert_called_once_with()


def test_sdk_json_log_deserialization_changes_json_but_raw_response_preserves_it(monkeypatch):
    summary = helper_summary("preview")
    encoded = json.dumps(summary).encode("utf-8")
    deserialized_response = HTTPResponse(body=BytesIO(encoded), status=200, preload_content=False)
    raw_response = HTTPResponse(body=BytesIO(encoded), status=200, preload_content=False)
    with ApiClient() as api:
        get = Mock(side_effect=[deserialized_response, raw_response])
        monkeypatch.setattr(api.rest_client, "GET", get)
        core = CoreV1Api(api)
        try:
            coerced = core.read_namespaced_pod_log("run-pod", "dify")
            assert coerced == str(summary)
            assert launcher.safe_summary(coerced, mode="preview") == {}
            raw = core.read_namespaced_pod_log("run-pod", "dify", _preload_content=False)
            assert raw is raw_response
            assert launcher.safe_summary(raw.read(16385).decode("utf-8"), mode="preview") == summary
        finally:
            for response in (deserialized_response, raw_response):
                response.close()
                response.release_conn()
        assert get.call_args.kwargs["_preload_content"] is False


@pytest.mark.parametrize("case", ["malformed", "missing", "utf8", "oversize", "mode", "counts"])
def test_wait_raw_log_errors_are_bounded_released_and_uncertain(case, capsys):
    summary = helper_summary("preview")
    if case == "mode":
        summary["mode"] = "apply"
    if case == "counts":
        summary["counts"]["created"] = True
    payload = {
        "malformed": b"not JSON",
        "missing": b"",
        "utf8": b"\xff",
        "oversize": b" " * 20000,
    }.get(case, json.dumps(summary).encode())
    stream = BytesIO(payload)
    raw = HTTPResponse(body=stream, status=200, preload_content=False)
    response = Mock(wraps=raw)
    core, batch = Mock(), Mock()
    batch.read_namespaced_job_status.return_value = NS(
        metadata=NS(uid="run-uid"), status=NS(conditions=[NS(type="Complete", status="True")])
    )
    core.list_namespaced_pod.return_value = NS(
        items=[
            NS(
                metadata=NS(
                    name="run-pod",
                    owner_references=[NS(uid="run-uid", kind="Job", controller=True)],
                )
            )
        ]
    )
    core.read_namespaced_pod_log.return_value = response
    assert launcher.wait_result(core, batch, "run", "dify", 1, mode="preview", uid="run-uid") == {
        "status": "uncertain",
        "counts": {},
        "planned_counts": {},
    }
    response.read.assert_called_once_with(16385)
    assert raw.tell() <= 16385
    assert stream.closed
    response.release_conn.assert_called_once_with()
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("stage", ["request", "read"])
@pytest.mark.parametrize(
    ("condition", "expected_status"),
    [
        ("Complete", "uncertain"),
        ("Failed", "failed"),
    ],
)
def test_wait_raw_log_transport_errors_are_sanitized_and_not_retried(
    stage, condition, expected_status, capsys
):
    core, batch = Mock(), Mock()
    batch.read_namespaced_job_status.return_value = NS(
        metadata=NS(uid="run-uid"), status=NS(conditions=[NS(type=condition, status="True")])
    )
    core.list_namespaced_pod.return_value = NS(
        items=[
            NS(
                metadata=NS(
                    name="run-pod",
                    owner_references=[NS(uid="run-uid", kind="Job", controller=True)],
                )
            )
        ]
    )
    response = Mock()
    if stage == "request":
        core.read_namespaced_pod_log.side_effect = ApiException(status=503, reason="private detail")
    else:
        core.read_namespaced_pod_log.return_value = response
        response.read.side_effect = HTTPError("private detail")
    assert launcher.wait_result(core, batch, "run", "dify", 1, mode="preview", uid="run-uid") == {
        "status": expected_status,
        "counts": {},
        "planned_counts": {},
    }
    core.read_namespaced_pod_log.assert_called_once()
    if stage == "read":
        response.close.assert_called_once_with()
        response.release_conn.assert_called_once_with()
    assert capsys.readouterr() == ("", "")


def test_preview_generation_does_not_write_and_write_preserves_enablement(
    tmp_path, monkeypatch, capsys
):
    primary, metadata, output = (
        tmp_path / name for name in ("primary.json", "metadata.json", "values.yaml")
    )
    primary.write_text(catalog().model_dump_json(by_alias=True))
    metadata.write_text(json.dumps(companion()))
    output.write_text("frontendDify:\n  modelSync:\n    enabled: true\nunrelated: preserved\n")
    before = output.read_text()
    args = [
        "project",
        "--catalog",
        str(primary),
        "--metadata",
        str(metadata),
        "--output",
        str(output),
    ]
    assert main.main(args) == 0
    assert output.read_text() == before
    assert "projection_revision" in capsys.readouterr().out
    assert main.main([*args, "--write"]) == 0
    result = yaml.safe_load(output.read_text())
    assert result["frontendDify"]["modelSync"]["enabled"] is True
    assert result["unrelated"] == "preserved"


def test_explicit_context_required_without_api_calls(monkeypatch):
    create_client = Mock()
    monkeypatch.setattr(main, "job_api_client", create_client)
    with pytest.raises(SystemExit):
        main.main(["launch", "--expected-projection-revision", "a" * 64])
    create_client.assert_not_called()


def test_source_model_rejects_gateway_manufactured_restrictions():
    with pytest.raises(ValidationError):
        SourceModel.model_validate(record())


@pytest.mark.parametrize("limit", [2_147_483_648, 9_007_199_254_740_991])
def test_source_limits_keep_base_safe_integer_range(limit):
    source = record()
    source.pop("gateway_restrictions")
    source["limits"] = dict.fromkeys(source["limits"], limit)
    assert SourceModel.model_validate(source).limits.context_tokens == limit


def test_source_limits_reject_values_above_base_safe_integer_range():
    source = record()
    source.pop("gateway_restrictions")
    source["limits"]["context_tokens"] = 9_007_199_254_740_992
    with pytest.raises(ValidationError):
        SourceModel.model_validate(source)


def test_source_binding_provenance_and_timestamp_boundaries_match_base():
    source = record()
    source.pop("gateway_restrictions")
    source["binding_id"] = "a" + "-" * 30 + "b"
    source["provenance"].update(
        source="s" * 512,
        observed_at="2025-01-01T00:00:00.123456789Z",
        valid_until="2026-01-01T00:00:00.123456789Z",
    )
    SourceModel.model_validate(source)
    for field, invalid in (
        ("binding_id", "a" * 33),
        ("binding_id", "a-"),
        ("source", "s" * 513),
        ("observed_at", "2025-01-01T00:00:00.1234567890Z"),
        ("observed_at", "2025-01-01T00:00:00+00:00"),
    ):
        malformed = copy.deepcopy(source)
        if field == "binding_id":
            malformed[field] = invalid
        else:
            malformed["provenance"][field] = invalid
        with pytest.raises(ValidationError):
            SourceModel.model_validate(malformed)
