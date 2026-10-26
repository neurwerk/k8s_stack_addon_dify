# SPDX-License-Identifier: MIT
"""Manual, additive Dify model registration using the installed native services."""

import hashlib
import json
import logging
import os
import re
import sys
import time
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
MAX_SAFE_INTEGER = 9_007_199_254_740_991
MAX_DOCUMENT_BYTES = 1_048_576
MAX_COMPANY_MODELS = 27
BINDING_PATTERN = r"[a-z0-9](?:[-a-z0-9]{0,30}[a-z0-9])?"
API_PATH_PATTERN = r"(?:/[a-z0-9][a-z0-9-]*)+"
DIGEST_PATTERN = r"[a-f0-9]{64}"
ROLE_PATTERN = r"(?:llm:invoke|model:[A-Za-z0-9][A-Za-z0-9._/-]{0,127}:invoke|provider:[a-z0-9][a-z0-9-]{0,47}:company-use)"
TIMESTAMP_PATTERN = r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?Z"


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


def load_projection(text: str) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "duplicate JSON key")
            result[key] = value
        return result

    def reject_number(value):
        raise ValueError("contract numbers must be safe integers")

    def safe_integer(text):
        value = int(text)
        require(abs(value) <= MAX_SAFE_INTEGER, "contract numbers must be safe integers")
        return value

    require(len(text.encode("utf-8")) <= MAX_DOCUMENT_BYTES, "projection exceeds size limit")
    value = json.loads(
        text,
        object_pairs_hook=unique,
        parse_float=reject_number,
        parse_constant=reject_number,
        parse_int=safe_integer,
    )
    require(isinstance(value, dict), "projection must be an object")
    return value


def bounded_text(value, maximum):
    return (
        isinstance(value, str)
        and 1 <= len(value) <= maximum
        and not re.search(r"[\x00-\x1f\x7f]", value)
    )


def require(condition, message):
    if not condition:
        raise ValueError(message)


def timestamp(value: str) -> datetime:
    require(
        isinstance(value, str) and len(value) <= 40 and re.fullmatch(TIMESTAMP_PATTERN, value),
        "timestamps must be UTC RFC3339",
    )
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
    require(len(canonical_json(projection)) <= MAX_DOCUMENT_BYTES, "projection exceeds size limit")
    require(
        all(
            isinstance(value, str) and re.fullmatch(DIGEST_PATTERN, value)
            for value in (
                expected,
                projection["projection_revision"],
                projection["catalog_revision"],
                projection["metadata_revision"],
            )
        ),
        "invalid revision digest",
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
    roles = projection["roles"]
    require(
        isinstance(roles, list)
        and len(roles) <= 128
        and roles == sorted(set(roles))
        and "llm:invoke" in roles,
        "invalid projection roles",
    )
    require(
        all(
            re.fullmatch(
                ROLE_PATTERN,
                role,
            )
            for role in roles
        ),
        "invalid projection role",
    )
    bindings = {}
    require(
        isinstance(projection["bindings"], list) and len(projection["bindings"]) <= 256,
        "too many bindings",
    )
    require(
        isinstance(projection["models"], list) and len(projection["models"]) <= 256,
        "too many models",
    )
    for binding in projection["bindings"]:
        require(set(binding) == {"id", "api_path"}, "invalid binding fields")
        require(re.fullmatch(BINDING_PATTERN, binding["id"]), "invalid binding id")
        require(binding["id"] not in bindings, "duplicate binding")
        require(
            len(binding["api_path"]) <= 128 and re.fullmatch(API_PATH_PATTERN, binding["api_path"]),
            "invalid binding API path",
        )
        require(binding["api_path"] not in bindings.values(), "duplicate binding API path")
        bindings[binding["id"]] = binding["api_path"]
    require(type(cap) is int and 0 <= cap <= MAX_COMPANY_MODELS, "invalid company model cap")
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
            all(
                v is None or (type(v) is int and 0 < v <= MAX_SAFE_INTEGER) for v in limits.values()
            ),
            "invalid token limit",
        )
        require(
            limits["context_tokens"] is None
            or all(
                v is None or v <= limits["context_tokens"]
                for v in (limits["max_input_tokens"], limits["max_output_tokens"])
            ),
            "individual token limit exceeds context",
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
            bounded_text(provenance["source"], 512),
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
            and len(restrictions["blocked_capabilities"]) <= 8
            and len(set(restrictions["blocked_capabilities"]))
            == len(restrictions["blocked_capabilities"])
            and set(restrictions["blocked_capabilities"]) <= CAPABILITIES,
            "invalid blocked capability",
        )
        require(
            isinstance(restrictions["evidence_refs"], list)
            and len(restrictions["evidence_refs"]) <= 32
            and len(set(restrictions["evidence_refs"])) == len(restrictions["evidence_refs"])
            and all(bounded_text(v, 2048) for v in restrictions["evidence_refs"]),
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
    if records and records[0].credential_id != credential.id:
        return "skip_active_credential_conflict", None
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


def empty_result(mode):
    return {
        "mode": mode,
        "counts": dict.fromkeys(("created", "updated", "unchanged", "skipped"), 0),
        "planned_counts": dict.fromkeys(("created", "updated", "unchanged", "skipped"), 0),
    }


def synchronize(projection, backend, mode, api_key, report):
    result = empty_result(mode)
    plans = []
    reasons = {}
    try:
        require(mode in {"preview", "apply"}, "invalid sync mode")
        # Classify the complete intent before any native operation. This remains
        # fixed when a later operation fails after earlier native commits.
        for model in projection["models"]:
            desired = credentials_for(model, api_key)
            action, credential_id = plan(model, desired, backend.read(model))
            category = (
                "skipped"
                if action.startswith("skip_")
                else "unchanged"
                if action == "noop"
                else "updated"
                if action == "update"
                else "created"
            )
            result["planned_counts"][category] += 1
            if category in {"unchanged", "skipped"}:
                result["counts"][category] += 1
            if action.startswith("skip_"):
                reasons[action] = reasons.get(action, 0) + 1
            plans.append((model, desired, action, credential_id, category))
        for index, (model, desired, action, credential_id, category) in enumerate(plans, 1):
            report({"action": action, "index": index})
            if mode == "preview" or category in {"unchanged", "skipped"}:
                continue
            require(
                datetime.now(timezone.utc)
                < timestamp(model["metadata"]["provenance"]["valid_until"]),
                "metadata expired during sync",
            )
            # Recheck ownership immediately before validation, including active
            # selection: native updates also propagate load-balancing secrets.
            require(
                plan(model, desired, backend.read(model)) == (action, credential_id),
                "model state changed during sync",
            )
            kwargs = dict(
                model=model["public_id"],
                credentials=desired,
                credential_name=credential_name(model),
            )
            if action == "create":
                backend.create(**kwargs)
            elif action in {"update", "recover_update"}:
                backend.update(**kwargs, credential_id=credential_id)
            if action in {"recover", "recover_update"}:
                backend.attach(model["public_id"], credential_id)
            result["counts"][category] += 1
        report({**result, "status": "complete", "reasons": reasons})
    except Exception:
        report({**result, "status": "failed", "reasons": reasons})
        raise


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


def run(report) -> None:
    mode = os.environ.get("DIFY_MODEL_SYNC_MODE", "")
    require(mode in {"preview", "apply"}, "explicit preview or apply mode required")
    projection = validate_projection(
        load_projection(Path("/etc/dify-model-sync/projection.json").read_text()),
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
                report,
            )


if __name__ == "__main__":
    # Provider errors may contain credentials or inference bodies. Do not emit
    # upstream logging, exception text, chained exceptions, or tracebacks.
    logging.disable(logging.CRITICAL)
    summary_reported = False

    def emit(value):
        global summary_reported
        summary_reported = summary_reported or "counts" in value
        print(json.dumps(value, sort_keys=True), flush=True)

    try:
        run(emit)
    except Exception:
        if not summary_reported:
            mode = os.environ.get("DIFY_MODEL_SYNC_MODE", "")
            emit(
                {
                    **empty_result(mode if mode in {"preview", "apply"} else "preview"),
                    "status": "failed",
                }
            )
        sys.exit(1)
