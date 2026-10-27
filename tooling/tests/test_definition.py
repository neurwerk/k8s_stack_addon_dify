"""The actual Dify definition preserves keys, accepted slots and reader boundaries."""

import hashlib
import secrets
from pathlib import Path
from unittest.mock import Mock

import pytest
from k8s_stack_tooling.addons import ContractError, check, load_manifest
from openbao_stack_setup import client as client_module
from openbao_stack_setup import declarative

from dify_tooling.cli import main

ROOT = Path(__file__).resolve().parents[2]


def test_candidate_definition_and_unpublished_installation_boundary():
    assert main([str(ROOT)]) == 0
    check(ROOT)
    with pytest.raises(ContractError, match="staged|digest"):
        check(ROOT, installable=True)


def test_real_setup_preserves_keys_and_secondary_slot_acceptance():
    addon = load_manifest(ROOT / "release/manifest.yaml")
    setups = {"dify": addon.spec.setup}
    snapshots = {}
    client = Mock(spec=client_module.OpenBaoClient)
    client.read_secret.side_effect = snapshots.get
    definitions = {
        "dify": hashlib.sha256((ROOT / "release/manifest.yaml").read_bytes()).hexdigest()
    }

    def plan_credentials():
        return declarative.plan_credentials(client, setups, definitions=definitions)

    # New administrator creation cannot bypass the protected handover planner.
    with pytest.raises(client_module.OpenBaoError):
        plan_credentials()
    snapshots["addons/dify/admin"] = client_module.SecretRecord(
        values={"initPassword": secrets.token_urlsafe(32)},
        version=1,
    )
    plan = plan_credentials()
    first = plan.writes[0]
    snapshots[first.path] = client_module.SecretRecord(values=first.values, version=1)
    resumed = plan_credentials()
    assert first.path not in {write.path for write in resumed.writes}
    for write in resumed.writes:
        snapshots[write.path] = client_module.SecretRecord(values=write.values, version=1)
    assert not plan_credentials().writes
    verifier = snapshots["addons/dify/gateway/verifiers"].values
    verifier["difyAgentgatewaySecondaryVerifierSha256"] = verifier[
        "difyAgentgatewayPrimaryVerifierSha256"
    ]
    verifier["difyAgentgatewayPrimaryVerifierSha256"] = "ab" * 32
    assert not plan_credentials().writes
    assert verifier["difyAgentgatewayPrimaryVerifierSha256"] == "ab" * 32
    readers = {reader.role: set(reader.records) for reader in declarative.compose_readers(setups)}
    assert readers["auth-keycloak-api-key-bridge"] == {"addons/dify/gateway/verifiers"}
    assert "addons/dify/gateway/key" in readers["addon-dify-runtime"]
    assert "addons/dify/oidc/service" not in readers["addon-dify-runtime"]
    assert "frontend-dify" not in readers
    handover = declarative.handover_definitions(setups, definitions)
    assert handover[0].definition == definitions["dify"]
    assert handover[0].label == "Dify break-glass administrator"
    verifier["difyAgentgatewaySecondaryVerifierSha256"] = "cd" * 32
    with pytest.raises(client_module.OpenBaoError):
        plan_credentials()
    client.write_secret.assert_not_called()
