# SPDX-License-Identifier: MIT
"""Manual, additive Dify model registration using the installed native services."""

import hashlib
import json
import logging
import os
import re
import sys
import time
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

PROVIDER = "langgenius/openai_api_compatible/openai_api_compatible"
PROVIDERS = [PROVIDER, "openai_api_compatible"]
CAPABILITIES = {
    "streaming",
    "tool_calling",
    "streaming_tool_calling",
    "structured_output",
    "image_input",
    "audio_input",
    "video_input",
    "document_input",
}
LOCK_KEY = 0x44494659424F4F54


def canonical_json(value: object) -> bytes:
    """Match Go encoding/json (Helm toJson), including HTML escaping."""
    result = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    for character, escaped in (
        ("&", r"\u0026"),
        ("<", r"\u003c"),
        (">", r"\u003e"),
        ("\u2028", r"\u2028"),
        ("\u2029", r"\u2029"),
    ):
        result = result.replace(character, escaped)
    return result.encode("utf-8", errors="strict")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def timestamp(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(result.tzinfo is not None, "metadata timestamp requires timezone")
    return result


def validate_projection(
    projection: dict, expected: str, cap: int, now: datetime | None = None
) -> dict:
    """Validate the immutable projection before loading Dify or touching state."""
    require(
        set(projection)
        == {
            "schema_version",
            "catalog_revision",
            "metadata_revision",
            "projection_revision",
            "roles",
            "bindings",
            "models",
        },
        "invalid projection fields",
    )
    require(
        type(projection["schema_version"]) is int and projection["schema_version"] == 1,
        "unsupported projection schema",
    )
    unsigned = {k: v for k, v in projection.items() if k != "projection_revision"}
    revision = hashlib.sha256(canonical_json(unsigned)).hexdigest()
    require(
        revision == expected == projection["projection_revision"], "projection revision mismatch"
    )
    require(
        all(
            isinstance(projection[k], str) and projection[k]
            for k in (
                "catalog_revision",
                "metadata_revision",
            )
        ),
        "missing source revision",
    )
    roles = projection["roles"]
    require(
        isinstance(roles, list) and roles == sorted(set(roles)) and "llm:invoke" in roles,
        "invalid projection roles",
    )
    require(
        all(
            re.fullmatch(
                r"(?:llm:invoke|model:[A-Za-z0-9][A-Za-z0-9._/-]{0,127}:invoke|provider:[a-z][a-z0-9-]{0,31}:company-use)",
                role,
            )
            for role in roles
        ),
        "invalid projection role",
    )
    bindings = {}
    for binding in projection["bindings"]:
        require(set(binding) == {"id", "api_path"}, "invalid binding fields")
        require(
            re.fullmatch(r"[a-z](?:[-a-z0-9]{0,30}[a-z0-9])?", binding["id"]), "invalid binding id"
        )
        require(binding["id"] not in bindings, "duplicate binding")
        require(
            re.fullmatch(r"(?:/[A-Za-z0-9_-]+)+", binding["api_path"]), "invalid binding API path"
        )
        bindings[binding["id"]] = binding["api_path"]
    require(type(cap) is int and cap >= 0, "invalid company model cap")
    require(
        sum(m.get("credential_mode") == "company" for m in projection["models"]) <= cap,
        "company model cap exceeded",
    )
    now = now or datetime.now(timezone.utc)
    seen = set()
    for model in projection["models"]:
        require(
            set(model) == {"binding_id", "public_id", "credential_mode", "metadata"},
            "invalid model fields",
        )
        require(model["binding_id"] in bindings, "model binding absent")
        public_id = model["public_id"]
        require(
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}", public_id), "invalid public model id"
        )
        require(public_id not in seen, "ambiguous public model identity")
        seen.add(public_id)
        require(f"model:{public_id}:invoke" in roles, "model permission absent")
        require(model["credential_mode"] in {"company", "local"}, "unsupported credential mode")
        metadata = model["metadata"]
        if metadata is None:
            continue
        require(
            set(metadata)
            == {
                "binding_id",
                "public_id",
                "limits",
                "capabilities",
                "provenance",
                "gateway_restrictions",
            },
            "invalid metadata fields",
        )
        require(
            all(metadata[k] == model[k] for k in ("binding_id", "public_id")),
            "metadata identity mismatch",
        )
        limits = metadata["limits"]
        require(
            set(limits) == {"context_tokens", "max_input_tokens", "max_output_tokens"},
            "invalid limits",
        )
        require(
            all(v is None or (type(v) is int and v > 0) for v in limits.values()),
            "invalid token limit",
        )
        capabilities = metadata["capabilities"]
        require(set(capabilities) == CAPABILITIES, "invalid capability fields")
        require(
            all(v in {"supported", "unsupported", "unknown"} for v in capabilities.values()),
            "invalid capability",
        )
        provenance = metadata["provenance"]
        require(
            set(provenance) == {"kind", "source", "observed_at", "valid_until"},
            "invalid provenance",
        )
        require(
            provenance["kind"] in {"provider_api", "runtime", "reviewed_config"},
            "invalid provenance kind",
        )
        require(
            isinstance(provenance["source"], str) and provenance["source"],
            "missing metadata source",
        )
        require(
            timestamp(provenance["observed_at"]) <= now < timestamp(provenance["valid_until"]),
            "metadata is stale or future dated",
        )
        restrictions = metadata["gateway_restrictions"]
        require(
            set(restrictions) == {"blocked_capabilities", "evidence_refs"}, "invalid restrictions"
        )
        require(
            isinstance(restrictions["blocked_capabilities"], list)
            and set(restrictions["blocked_capabilities"]) <= CAPABILITIES,
            "invalid blocked capability",
        )
        require(
            isinstance(restrictions["evidence_refs"], list)
            and all(isinstance(v, str) and v for v in restrictions["evidence_refs"]),
            "invalid restriction evidence",
        )
    return projection


def credential_name(model: dict) -> str:
    identity = json.dumps([model["binding_id"], model["public_id"]], separators=(",", ":"))
    return "gateway-sync-" + hashlib.sha256(identity.encode()).hexdigest()[:48]


def credentials_for(model: dict, api_key: str) -> dict | None:
    metadata = model["metadata"]
    if metadata is None:
        return None
    limits = metadata["limits"]
    # Neither the plugin's 4096 default nor a guessed provider limit is evidence.
    if not limits["context_tokens"] or not limits["max_output_tokens"]:
        return None
    blocked = metadata["gateway_restrictions"]["blocked_capabilities"]

    def supports(capability):
        return metadata["capabilities"][capability] == "supported" and capability not in blocked

    # Plugin 0.0.56 has no switch that suppresses streaming globally. Unknown
    # streaming adds no schema feature; an explicit prohibition cannot be met.
    if metadata["capabilities"]["streaming"] == "unsupported" or "streaming" in blocked:
        return None
    context_size = min(
        limits["context_tokens"], limits["max_input_tokens"] or limits["context_tokens"]
    )
    result = {
        "api_key": api_key,
        "endpoint_url": f"http://127.0.0.1:8080/bindings/{model['binding_id']}/v1",
        "endpoint_model_name": model["public_id"],
        "mode": "chat",
        "context_size": str(context_size),
        "max_tokens_to_sample": str(min(limits["max_output_tokens"], context_size)),
        "function_calling_type": "tool_call" if supports("tool_calling") else "no_call",
        "stream_function_calling": "supported"
        if supports("tool_calling") and supports("streaming_tool_calling")
        else "not_supported",
        # The companion's generic structured_output flag does not prove native
        # strict JSON-schema support (the plugin also exposes prompt schemas).
        "structured_output_support": "not_supported",
        "agent_thought_support": "not_supported",
        "compatibility_mode": "strict",
        "token_param_name": "auto",
        "stream_mode_auth": "not_use",
        "stream_mode_delimiter": r"\n\n",
    }
    for capability, field in (
        ("image_input", "vision_support"),
        ("audio_input", "audio_support"),
        ("video_input", "video_support"),
        ("document_input", "document_support"),
    ):
        result[field] = "support" if supports(capability) else "no_support"
    return result


def plan(model: dict, desired: dict | None, state: tuple) -> tuple[str, str | None]:
    """Pure read-only decision; never seize an existing user's credential."""
    if desired is None:
        return "skip_metadata", None
    records, credentials, settings, defaults = state
    owned = [c for c in credentials if c.credential_name == credential_name(model)]
    require(len(owned) <= 1 and len(records) <= 1, "credential ownership collision")
    if defaults and not owned:
        return "skip_unmanaged_default", None
    if not owned:
        if credentials or records or settings:
            return "skip_unmanaged_conflict", None
        return "create", None
    credential = owned[0]
    # Ownership must agree with its stable identity, not just the display name.
    original = credential.decrypted
    require(
        original.get("endpoint_url") == desired["endpoint_url"]
        and original.get("endpoint_model_name") == desired["endpoint_model_name"],
        "credential ownership collision",
    )
    unchanged = all(original.get(key) == value for key, value in desired.items())
    if not records:
        require(
            len(credentials) == 1 and not settings and not defaults,
            "unsafe interrupted credential state",
        )
        return "recover" if unchanged else "recover_update", credential.id
    return ("noop" if unchanged else "update"), credential.id


def synchronize(projection, backend, mode, api_key, report):
    counts = Counter()
    for model in projection["models"]:
        if model["metadata"] is not None:
            require(
                datetime.now(timezone.utc)
                < timestamp(model["metadata"]["provenance"]["valid_until"]),
                "metadata expired during sync",
            )
        desired = credentials_for(model, api_key)
        action, credential_id = plan(model, desired, backend.read(model))
        counts[action] += 1
        report({"action": action, "index": sum(counts.values())})
        if mode == "preview" or action.startswith("skip_") or action == "noop":
            continue
        kwargs = dict(
            model=model["public_id"], credentials=desired, credential_name=credential_name(model)
        )
        if action == "create":
            backend.create(**kwargs)
        elif action in {"update", "recover_update"}:
            backend.update(**kwargs, credential_id=credential_id)
        # Native create already creates the model row. Only attach a credential
        # for a proven credential-only interruption; never switch existing rows.
        if action in {"recover", "recover_update"}:
            backend.attach(model["public_id"], credential_id)
        report({"completed": sum(counts.values()), "action": action})
    report({"mode": mode, "counts": dict(counts)})


@contextmanager
def bootstrap_lock(engine, timeout=30):
    from sqlalchemy import text

    deadline = time.monotonic() + timeout
    with engine.connect() as connection:
        acquired = False
        try:
            connection.execute(text("SET statement_timeout = '5000'"))
            connection.commit()
            while True:
                acquired = bool(
                    connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": LOCK_KEY})
                )
                connection.commit()
                if acquired:
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError("bootstrap lock unavailable")
                time.sleep(min(1, max(0, deadline - time.monotonic())))
            yield
        finally:
            if acquired:
                try:
                    released = connection.scalar(
                        text("SELECT pg_advisory_unlock(:key)"), {"key": LOCK_KEY}
                    )
                    connection.commit()
                    require(released, "bootstrap lock release failed")
                except Exception:
                    connection.invalidate()
                    raise


def existing_rsa_decoding(tenant):
    """Load existing RSA material read-only, without populating Dify's Redis cache."""
    from configs import dify_config
    from extensions.ext_storage import storage
    from libs import rsa

    require(dify_config.KEY_PROVIDER_TYPE == "local", "local RSA key provider required")
    key = rsa.RSA.import_key(storage.load(f"privkeys/{tenant.id}/private.pem"))
    public = rsa.RSA.import_key(tenant.encrypt_public_key)
    require(
        key.has_private() and key.n == public.n and key.e == public.e, "tenant RSA key mismatch"
    )
    return key, rsa.gmpy2_pkcs10aep_cipher.new(key)


class NativeBackend:
    def __init__(self, tenant_id: str, decoding):
        from inspect import signature

        from services.model_provider_service import ModelProviderService

        self.tenant_id = tenant_id
        self.decoding = decoding
        self.service = ModelProviderService()
        common = {"tenant_id", "provider", "model_type", "model", "credentials", "credential_name"}
        require(
            set(signature(self.service.create_model_credential).parameters) == common,
            "unsupported native create service signature",
        )
        require(
            set(signature(self.service.update_model_credential).parameters)
            == common | {"credential_id"},
            "unsupported native update service signature",
        )

    def read(self, model):
        from core.helper import encrypter
        from extensions.ext_database import db
        from models.provider import (
            ProviderModel,
            ProviderModelCredential,
            ProviderModelSetting,
            TenantDefaultModel,
        )
        from sqlalchemy import select
        from types import SimpleNamespace

        def rows(table):
            return db.session.scalars(
                select(table).where(
                    table.tenant_id == self.tenant_id,
                    table.provider_name.in_(PROVIDERS),
                    table.model_name == model["public_id"],
                    table.model_type == "llm",
                )
            ).all()

        db.session.expire_all()
        credentials = []
        for row in rows(ProviderModelCredential):
            decrypted = (
                json.loads(row.encrypted_config)
                if row.credential_name == credential_name(model)
                else {}
            )
            if decrypted.get("api_key"):
                decrypted["api_key"] = encrypter.decrypt_token_with_decoding(
                    decrypted["api_key"],
                    self.decoding,
                )
            credentials.append(
                SimpleNamespace(id=row.id, credential_name=row.credential_name, decrypted=decrypted)
            )
        return (
            rows(ProviderModel),
            credentials,
            rows(ProviderModelSetting),
            rows(TenantDefaultModel),
        )

    def create(self, **kwargs):
        self.service.create_model_credential(
            tenant_id=self.tenant_id, provider=PROVIDER, model_type="llm", **kwargs
        )

    def update(self, **kwargs):
        self.service.update_model_credential(
            tenant_id=self.tenant_id, provider=PROVIDER, model_type="llm", **kwargs
        )

    def attach(self, model, credential_id):
        self.service.add_model_credential_to_model_list(
            tenant_id=self.tenant_id,
            provider=PROVIDER,
            model_type="llm",
            model=model,
            credential_id=credential_id,
        )


def run() -> None:
    mode = os.environ.get("DIFY_MODEL_SYNC_MODE", "")
    require(mode in {"preview", "apply"}, "explicit preview or apply mode required")
    projection = validate_projection(
        json.loads(Path("/etc/dify-model-sync/projection.json").read_text()),
        os.environ.get("EXPECTED_PROJECTION_REVISION", ""),
        int(os.environ["MAX_COMPANY_MODELS"]),
    )
    api_key = os.environ.get("LLM_PROXY_API_KEY", "")
    require(bool(api_key), "gateway credential unavailable")
    require(bool(os.environ.get("SECRET_KEY")), "existing application encryption secret required")
    from app_factory import create_flask_app_with_configs, initialize_extensions
    from core.plugin.plugin_service import PluginService
    from extensions.ext_database import db
    from models.account import Tenant
    from sqlalchemy import select

    app = create_flask_app_with_configs()
    with app.app_context():
        initialize_extensions(app)
        with bootstrap_lock(db.engine):
            tenants = db.session.scalars(select(Tenant)).all()
            require(len(tenants) == 1, "exactly one existing tenant required")
            tenant = tenants[0]
            require(bool(tenant.encrypt_public_key), "tenant encryption prerequisite absent")
            decoding = existing_rsa_decoding(tenant)
            plugins = PluginService.list(tenant.id)
            require(
                any(
                    p.plugin_id == "langgenius/openai_api_compatible" and p.version == "0.0.56"
                    for p in plugins
                ),
                "installed plugin prerequisite absent",
            )
            synchronize(
                projection,
                NativeBackend(tenant.id, decoding),
                mode,
                api_key,
                lambda value: print(json.dumps(value, sort_keys=True), flush=True),
            )


if __name__ == "__main__":
    # Provider errors may contain credentials or inference bodies. Do not emit
    # upstream logging, exception text, chained exceptions, or tracebacks.
    logging.disable(logging.CRITICAL)
    try:
        run()
    except Exception:
        print(
            '{"status":"failed","detail":"model sync stopped; completed native commits are retained"}',
            flush=True,
        )
        sys.exit(1)
