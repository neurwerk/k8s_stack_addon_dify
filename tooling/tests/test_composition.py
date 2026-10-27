"""Check actual Dify machine scopes and inert Job images through the shared validator."""

import copy
import json
import os
from pathlib import Path

import pytest
import yaml
from k8s_stack_tooling.addons import ContractError, load_manifest
from k8s_stack_tooling.addons.helm_images import validate_helm_images
from k8s_stack_tooling.addons.validation import render

ROOT = Path(__file__).resolve().parents[2]


def test_effective_machine_approval_and_enabled_job_images():
    selected_base = os.environ.get("BASE_REPO")
    if not selected_base:
        pytest.fail("BASE_REPO must identify the selected Base candidate for composition")
    addon = load_manifest(ROOT / "release/manifest.yaml")
    packages = {
        package.name: render(ROOT / package.path, root=ROOT) for package in addon.spec.packages
    }
    configmaps = {
        (obj["metadata"]["namespace"], obj["metadata"]["name"]): obj
        for objects in packages.values()
        for obj in objects
        if obj["kind"] == "ConfigMap"
    }
    settings = yaml.safe_load((ROOT / "tests/validation/helm-lint-values.yaml").read_text())
    projection = json.loads((ROOT / "tooling/tests/fixtures/legacy-projection.json").read_text())
    settings["frontendDify"]["modelSync"] = {"enabled": True, "projection": projection}
    settings["authKeycloak"]["agentgatewayClientRoles"] = projection["roles"]
    settings["authKeycloak"]["difyAgentgatewayClientRoles"] = projection["roles"]
    approval = settings.pop("serviceApproval")
    approval["gatewayPermissions"] = projection["roles"]
    browser = yaml.safe_load((ROOT / "tests/validation/oidc-approval-values.yaml").read_text())
    for item in addon.spec.inputs:
        assert item.kind == "ConfigMap"
        data = {}
        if item.name == "client-values":
            data = settings
        elif item.name == "dify-service-approval-values":
            data = {"serviceApproval": approval}
        elif item.name == "dify-oidc-approval-values":
            data = browser
        configmaps[(item.namespace, item.name)] = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"namespace": item.namespace, "name": item.name},
            "data": {"values.yaml": json.dumps(data)},
        }

    def validate(definition, inputs):
        return validate_helm_images(
            definition,
            packages,
            sources={
                ("GitRepository", "flux-system", "dify-addon"): ROOT,
                ("GitRepository", "flux-system", "k8s-stack"): Path(selected_base),
            },
            configmaps=inputs,
            kube_version="1.34.1",
            api_versions=(
                "gateway.networking.k8s.io/v1",
                "external-secrets.io/v1",
                "policy.cert-manager.io/v1alpha1",
            ),
        )

    validate(addon, configmaps)
    rejected = copy.deepcopy(configmaps)
    rejected[("auth-keycloak", "dify-service-approval-values")]["data"]["values.yaml"] = (
        yaml.safe_dump({"serviceApproval": {**approval, "gatewayPermissions": ["llm:invoke"]}})
    )
    with pytest.raises(ContractError, match="approval|scope"):
        validate(addon, rejected)
    altered = addon.model_copy(deep=True)
    next(
        a for a in altered.spec.artifacts if a.id == "tooling"
    ).image = "ghcr.io/neurwerk/k8s-stack-addon-dify-tooling:0.1.5"
    with pytest.raises(ContractError, match="Job template|image"):
        validate(altered, configmaps)
