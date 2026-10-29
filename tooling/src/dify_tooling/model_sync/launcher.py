"""Snapshot installed, inert job inputs; never execute code inside application pods."""

from __future__ import annotations

import copy
import re
import uuid
from typing import Any

from k8s_stack_tooling.api.jobs import (
    ConfigMapVersion,
    read_job_summary,
    submit_snapshot_job,
    wait_for_job,
)
from k8s_stack_tooling.gateway_catalog.catalog import GatewayCatalog
from k8s_stack_tooling.gateway_catalog.metadata import MetadataError, load_json

from dify_tooling.model_sync.projection import (
    build_projection,
    require_installed_projection,
)

PRIMARY = "agentgateway-catalog-config-map"
COMPANION = "gateway-model-metadata-config-map"
INSTALLED = "frontend-dify-model-sync"
TIMEOUT = (5, 30)
PAYLOAD_DIRECTORY = "/work/payloads"
PAYLOAD_PATH = PAYLOAD_DIRECTORY + "/sync_models.py"
EXPORT_COMMAND = ["dify-stage-payloads"]


def require_export_init(pod: dict[str, Any]) -> None:
    """Only the reviewed, credential-free exporter may stage the native payload."""
    initializers = pod.get("initContainers", [])
    if len(initializers) != 1 or pod.get("automountServiceAccountToken") is not False:
        raise MetadataError("manual job requires one tokenless code-export initializer")
    export = initializers[0]
    if (
        export.get("name") != "export-model-sync"
        or export.get("command") != EXPORT_COMMAND
        or export.get("args")
        or export.get("env")
        or export.get("envFrom")
        or not re.fullmatch(
            r"ghcr\.io/neurwerk/k8s-stack-addon-dify-tooling:[0-9]+\.[0-9]+\.[0-9]+@sha256:[a-f0-9]{64}",
            export.get("image", ""),
        )
        or export.get("volumeMounts") != [{"name": "payload", "mountPath": PAYLOAD_DIRECTORY}]
    ):
        raise MetadataError("unsupported model-sync export initializer")
    security = export.get("securityContext", {})
    if (
        security.get("runAsNonRoot") is not True
        or security.get("runAsUser") != 65532
        or security.get("runAsGroup") != 65532
        or security.get("readOnlyRootFilesystem") is not True
        or security.get("allowPrivilegeEscalation") is not False
        or security.get("capabilities", {}).get("drop") != ["ALL"]
        or security.get("seccompProfile") != {"type": "RuntimeDefault"}
    ):
        raise MetadataError("export initializer must use the reviewed non-root security context")
    volumes = [v for v in pod.get("volumes", []) if v.get("name") == "payload"]
    if volumes != [{"name": "payload", "emptyDir": {}}]:
        raise MetadataError("payload must use a dedicated emptyDir")
    native = pod["containers"][0]
    if native.get("command") != ["python", "-B", PAYLOAD_PATH] or native.get("args"):
        raise MetadataError("native container must directly execute the exported payload")
    mounts = [v for v in native.get("volumeMounts", []) if v.get("name") == "payload"]
    if mounts != [{"name": "payload", "mountPath": PAYLOAD_DIRECTORY, "readOnly": True}]:
        raise MetadataError("native payload must be mounted read-only")


def read_inputs(
    core: Any, catalog_namespace: str, namespace: str
) -> tuple[dict[str, Any], list[Any]]:
    maps = [
        core.read_namespaced_config_map(name, ns, _request_timeout=TIMEOUT)
        for name, ns in (
            (PRIMARY, catalog_namespace),
            (COMPANION, catalog_namespace),
            (INSTALLED, namespace),
        )
    ]
    primary = GatewayCatalog.model_validate(load_json(maps[0].data["catalog.json"]))
    proposed = build_projection(primary, load_json(maps[1].data["metadata.json"]))
    return proposed, maps


def prepare_job(
    data: dict[str, str], *, namespace: str, name: str, mode: str, expected: str, max_global: int
) -> dict[str, Any]:
    job = copy.deepcopy(load_json(data["job-template.json"]))
    if job.get("apiVersion") != "batch/v1" or job.get("kind") != "Job":
        raise MetadataError("installed template is not a batch/v1 Job")
    metadata = job.setdefault("metadata", {})
    if metadata.get("namespace", namespace) != namespace or metadata.get("ownerReferences"):
        raise MetadataError("invalid installed job ownership")
    metadata.pop("generateName", None)
    metadata.pop("name", None)
    metadata.update({"name": name, "namespace": namespace})
    annotations = metadata.get("annotations", {})
    if any(key.startswith("helm.sh/hook") for key in annotations):
        raise MetadataError("manual job must not be a Helm hook")
    spec = job["spec"]
    if spec.get("backoffLimit") != 0:
        raise MetadataError("manual job retries must be disabled")
    pod = spec["template"]["spec"]
    if pod.get("restartPolicy") != "Never":
        raise MetadataError("manual job must not restart its payload")
    containers = pod["containers"]
    if len(containers) != 1:
        raise MetadataError("manual job requires exactly one payload container")
    require_export_init(pod)
    env = containers[0].setdefault("env", [])
    supplied = {
        "DIFY_MODEL_SYNC_MODE": mode,
        "EXPECTED_PROJECTION_REVISION": expected,
        "MAX_GLOBAL_MODELS": str(max_global),
    }
    env[:] = [item for item in env if item.get("name") not in supplied]
    env.extend({"name": key, "value": value} for key, value in supplied.items())
    replaced = 0
    for volume in pod.get("volumes", []):
        cm = volume.get("configMap", {})
        if cm.get("name") == INSTALLED:
            cm["name"] = name
            replaced += 1
    if replaced != 1:
        raise MetadataError("template must mount exactly one installed sync ConfigMap")
    return job


def launch(
    core: Any,
    batch: Any,
    *,
    catalog_namespace: str,
    namespace: str,
    mode: str,
    expected: str,
    max_global: int,
    wait_seconds: int,
) -> dict[str, Any]:
    if mode not in {"preview", "apply"} or not 0 <= max_global <= 27:
        raise MetadataError("invalid mode or global-model cap (maximum 27)")
    proposed, maps = read_inputs(core, catalog_namespace, namespace)
    data = maps[2].data
    require_installed_projection(load_json(data["projection.json"]), proposed, expected)
    global_count = sum(m["credential_mode"] == "global" for m in proposed["models"])
    if mode == "apply" and global_count > max_global:
        raise MetadataError("selected global models exceed the approved cap")
    name = f"frontend-dify-model-sync-{uuid.uuid4().hex[:12]}"
    job = prepare_job(
        data, namespace=namespace, name=name, mode=mode, expected=expected, max_global=max_global
    )
    inputs = [
        ConfigMapVersion(ns, cm_name, obj.metadata.uid, obj.metadata.resource_version)
        for obj, (cm_name, ns) in zip(
            maps,
            ((PRIMARY, catalog_namespace), (COMPANION, catalog_namespace), (INSTALLED, namespace)),
            strict=True,
        )
    ]
    submitted = submit_snapshot_job(
        core,
        batch,
        job=job,
        snapshot_data={"projection.json": data["projection.json"]},
        inputs=inputs,
    )
    result: dict[str, Any] = {
        "job": name,
        "mode": mode,
        "global_models": global_count,
        "selected_models": len(proposed["models"]),
        "status": submitted.status,
    }
    if submitted.status == "uncertain":
        return {**result, "phase": submitted.phase}
    if wait_seconds and submitted.uid:
        result.update(
            wait_result(
                core,
                batch,
                name,
                namespace,
                wait_seconds,
                mode=mode,
                uid=submitted.uid,
            )
        )
        for failure in result.get("failures", []):
            if failure["index"] > len(proposed["models"]):
                result["status"] = "uncertain"
                continue
            # Names come from the verified projection, never from provider logs.
            failure["model"] = proposed["models"][failure["index"] - 1]["public_id"]
    return result


def safe_summary(logs: str, *, mode: str) -> dict[str, Any]:
    """Keep completed and planned counts separate; suppress every other log field."""
    allowed = {"created", "updated", "unchanged", "skipped"}
    summary: dict[str, Any] = {}
    for line in logs.splitlines():
        candidate = line.removeprefix("DIFY_MODEL_SYNC_RESULT ")
        try:
            value = load_json(candidate, max_bytes=16384)
        except (ValueError, RecursionError):
            continue
        if not isinstance(value, dict) or value.get("mode") != mode:
            continue
        counts, planned_counts = value.get("counts"), value.get("planned_counts")
        if not isinstance(counts, dict) or not isinstance(planned_counts, dict):
            continue
        if any(
            any(type(group.get(key)) is not int or not 0 <= group[key] <= 256 for key in allowed)
            for group in (counts, planned_counts)
        ):
            continue
        if mode == "preview" and (counts["created"] or counts["updated"]):
            continue
        failures = value.get("failures", [])
        if not isinstance(failures, list) or len(failures) > 256:
            continue
        if any(
            not isinstance(failure, dict)
            or type(failure.get("index")) is not int
            or not 1 <= failure["index"] <= 256
            or type(failure.get("status_code")) is not int
            or failure["status_code"] not in {400, 404, 422, 429, 500, 502, 503, 504}
            or type(failure.get("attempts")) is not int
            or not 1 <= failure["attempts"] <= 3
            for failure in failures
        ):
            continue
        if (mode == "preview" and failures) or len({f["index"] for f in failures}) != len(failures):
            continue
        summary = {
            "mode": mode,
            "counts": {k: counts[k] for k in sorted(allowed)},
            "planned_counts": {k: planned_counts[k] for k in sorted(allowed)},
        }
        if "failures" in value:
            summary["failures"] = [
                {key: failure[key] for key in ("index", "status_code", "attempts")}
                for failure in failures
            ]
    return summary


def wait_result(
    core: Any, batch: Any, name: str, namespace: str, seconds: int, *, mode: str, uid: str
) -> dict[str, Any]:
    status = wait_for_job(batch, namespace=namespace, name=name, uid=uid, seconds=seconds)
    if status not in {"complete", "failed"}:
        return {"status": status}
    result = read_job_summary(
        core,
        namespace=namespace,
        name=name,
        uid=uid,
        container="model-sync",
        summarize=lambda logs: safe_summary(logs, mode=mode) or None,
    )
    return {
        "status": "failed"
        if status == "failed"
        else ("complete" if result.summary else "uncertain"),
        **(result.summary or {"counts": {}, "planned_counts": {}}),
    }
