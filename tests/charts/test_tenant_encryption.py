"""Protect bootstrap recovery boundaries with synthetic provider/storage state."""

import importlib.util
import sys
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "setup_model_provider", ROOT / "customizations/scripts/setup_model_provider.py"
)
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


class Key:
    """Key identities only: these tests exercise policy, not RSA mathematics."""

    def __init__(self, n="original", e=65537, private=True):
        self.n, self.e, self.private = n, e, private

    def has_private(self):
        return self.private

    def publickey(self):
        return Key(self.n, self.e, private=False)

    def export_key(self):
        return f"{'private' if self.private else 'public'}:{self.n}:{self.e}".encode()

    @staticmethod
    def import_key(value):
        if isinstance(value, bytes):
            value = value.decode()
        kind, n, e = value.split(":")
        return Key(n, int(e), kind == "private")


class NativeState:
    def __init__(self, public="public:original:65537", private=b"private:original:65537"):
        self.tenant = SimpleNamespace(id="synthetic-tenant", encrypt_public_key=public)
        self.committed_public = public
        self.private = private
        self.cached = None
        self.events = []
        self.config = SimpleNamespace(KEY_PROVIDER_TYPE="local")
        self.storage = SimpleNamespace(load=Mock(side_effect=self.load))
        self.generate = Mock(side_effect=self.generate_key_pair)
        self.encrypter = SimpleNamespace(
            encrypt_token=Mock(side_effect=self.encrypt),
            decrypt_token=Mock(side_effect=self.decrypt),
        )
        self.session = SimpleNamespace(commit=Mock(side_effect=self.commit))
        self.rsa = SimpleNamespace(
            RSA=SimpleNamespace(import_key=Mock(side_effect=Key.import_key)),
            get_decrypt_decoding=Mock(side_effect=self.decoding),
            generate_key_pair=self.generate,
            PrivkeyNotFoundError=FileNotFoundError,
        )
        self.modules = {
            "configs": SimpleNamespace(dify_config=self.config),
            "core.helper": SimpleNamespace(encrypter=self.encrypter),
            "extensions.ext_database": SimpleNamespace(db=SimpleNamespace(session=self.session)),
            "extensions.ext_storage": SimpleNamespace(storage=self.storage),
            "libs": SimpleNamespace(rsa=self.rsa),
            "libs.rsa": self.rsa,
            "libs.key_providers": SimpleNamespace(generate_key_pair=self.generate),
        }

    def load(self, path):
        assert path == f"privkeys/{self.tenant.id}/private.pem"
        if self.private is None:
            raise FileNotFoundError("synthetic missing private key")
        return self.private

    def decoding(self, tenant_id):
        assert tenant_id == self.tenant.id
        material = self.cached or self.storage.load(f"privkeys/{tenant_id}/private.pem")
        return Key.import_key(material), None

    def generate_key_pair(self, tenant_id):
        assert tenant_id == self.tenant.id
        self.events.append("generate")
        key = Key(f"generated-{self.generate.call_count}")
        self.private = key.export_key()
        return key.publickey().export_key().decode()

    def encrypt(self, tenant_id, text):
        assert tenant_id == self.tenant.id
        self.events.append("encrypt")
        if self.config.KEY_PROVIDER_TYPE != "local":
            return "native-version", text
        key = Key.import_key(self.tenant.encrypt_public_key)
        return (key.n, key.e), text

    def decrypt(self, tenant_id, ciphertext):
        assert tenant_id == self.tenant.id
        self.events.append("decrypt")
        identity, text = ciphertext
        if self.config.KEY_PROVIDER_TYPE == "local":
            key, _ = self.decoding(tenant_id)
            if not key.has_private() or identity != (key.n, key.e):
                raise ValueError("synthetic decryption mismatch")
        return text

    def commit(self):
        self.events.append("commit")
        self.committed_public = self.tenant.encrypt_public_key


class TenantEncryptionTests(unittest.TestCase):
    def state(self, **kwargs):
        state = NativeState(**kwargs)
        self.enterContext(patch.dict(sys.modules, state.modules))
        return state

    def assert_not_replaced(self, state):
        state.generate.assert_not_called()
        state.session.commit.assert_not_called()
        self.assertEqual(state.tenant.encrypt_public_key, state.committed_public)

    def test_existing_pair_preserves_retained_ciphertext(self):
        state = self.state()
        retained = state.encrypt(state.tenant.id, "synthetic unmanaged credential")
        bootstrap._ensure_tenant_rsa(state.tenant)
        self.assertEqual(state.decrypt(state.tenant.id, retained), "synthetic unmanaged credential")
        self.assertEqual(state.private, b"private:original:65537")
        self.assert_not_replaced(state)

    def test_keyless_legacy_recovery_verifies_before_commit_and_is_repeatable(self):
        state = self.state(public=None, private=None)
        bootstrap._ensure_tenant_rsa(state.tenant)
        self.assertEqual(state.events, ["generate", "encrypt", "decrypt", "commit"])
        private, public = state.private, state.committed_public
        self.assertIsNotNone(public)
        bootstrap._ensure_tenant_rsa(state.tenant)
        self.assertEqual((state.private, state.committed_public), (private, public))
        state.generate.assert_called_once_with(state.tenant.id)
        state.session.commit.assert_called_once_with()

    def test_private_key_without_reference_is_reused(self):
        state = self.state(public=None)
        bootstrap._ensure_tenant_rsa(state.tenant)
        self.assertEqual(state.committed_public, "public:original:65537")
        self.assertEqual(state.private, b"private:original:65537")
        state.generate.assert_not_called()
        self.assertEqual(state.events, ["encrypt", "decrypt", "commit"])

    def test_cached_key_cannot_hide_missing_durable_key(self):
        for cached in (None, b"private:original:65537"):
            with self.subTest(cached=cached):
                state = self.state(private=None)
                state.cached = cached
                with self.assertRaisesRegex(RuntimeError, "restore the matching key"):
                    bootstrap._ensure_tenant_rsa(state.tenant)
                self.assert_not_replaced(state)
                self.assertIsNone(state.private)
                state.encrypter.encrypt_token.assert_not_called()

    def test_cached_key_cannot_hide_durable_public_private_mismatch(self):
        for private in (b"private:other:65537", b"private:original:3"):
            with self.subTest(private=private):
                state = self.state(private=private)
                state.cached = b"private:original:65537"
                with self.assertRaisesRegex(RuntimeError, "key mismatch"):
                    bootstrap._ensure_tenant_rsa(state.tenant)
                self.assert_not_replaced(state)
                self.assertEqual(state.private, private)
                state.encrypter.encrypt_token.assert_not_called()

    def test_native_roundtrip_rejects_stale_cached_key(self):
        state = self.state()
        state.cached = b"private:other:65537"
        with self.assertRaisesRegex(ValueError, "decryption mismatch"):
            bootstrap._ensure_tenant_rsa(state.tenant)
        self.assert_not_replaced(state)
        self.assertEqual(state.private, b"private:original:65537")
        self.assertEqual(state.cached, b"private:other:65537")

    def test_invalid_material_and_storage_errors_never_generate_keys(self):
        for public in (None, "public:original:65537"):
            for failure in (b"malformed", b"public:original:65537", PermissionError(), OSError()):
                with self.subTest(public=public, failure=failure):
                    state = self.state(public=public)
                    if isinstance(failure, Exception):
                        state.storage.load.side_effect = failure
                    else:
                        state.private = failure
                    with self.assertRaises((ValueError, RuntimeError, OSError)):
                        bootstrap._ensure_tenant_rsa(state.tenant)
                    self.assert_not_replaced(state)

    def test_nonlocal_provider_uses_native_verification_without_local_repair(self):
        for public in (None, "opaque-native-reference"):
            with self.subTest(public=public):
                state = self.state(public=public, private=None)
                state.config.KEY_PROVIDER_TYPE = "azure-keyvault"
                bootstrap._ensure_tenant_rsa(state.tenant)
                state.encrypter.decrypt_token.side_effect = ValueError("native key unavailable")
                with self.assertRaisesRegex(ValueError, "native key unavailable"):
                    bootstrap._ensure_tenant_rsa(state.tenant)
                self.assert_not_replaced(state)
                state.storage.load.assert_not_called()
                state.rsa.RSA.import_key.assert_not_called()
                state.rsa.get_decrypt_decoding.assert_not_called()

    def test_failed_initialization_reuses_persisted_private_key_on_retry(self):
        for stage in ("generation", "probe", "commit"):
            with self.subTest(stage=stage):
                state = self.state(public=None, private=None)

                def generate_then_fail(tenant_id):
                    state.generate_key_pair(tenant_id)
                    raise OSError("interrupted after storage write")

                if stage == "generation":
                    state.generate.side_effect = generate_then_fail
                elif stage == "probe":
                    state.encrypter.decrypt_token.side_effect = ValueError("probe failed")
                else:
                    state.session.commit.side_effect = OSError("commit failed")
                with self.assertRaises((ValueError, OSError)):
                    bootstrap._ensure_tenant_rsa(state.tenant)
                self.assertIsNone(state.committed_public)
                private = state.private
                self.assertIsNotNone(private)
                state.tenant.encrypt_public_key = state.committed_public
                state.generate.side_effect = state.generate_key_pair
                state.encrypter.decrypt_token.side_effect = state.decrypt
                state.session.commit.side_effect = state.commit
                bootstrap._ensure_tenant_rsa(state.tenant)
                self.assertEqual(state.private, private)
                self.assertEqual(state.committed_public, "public:generated-1:65537")
                state.generate.assert_called_once_with(state.tenant.id)

    def test_key_failure_stops_bootstrap_before_plugins_or_credentials(self):
        state = self.state(private=None)
        app = SimpleNamespace(app_context=nullcontext)
        self.enterContext(
            patch.dict(
                sys.modules,
                {
                    "app_factory": SimpleNamespace(
                        create_flask_app_with_configs=lambda: app,
                        initialize_extensions=lambda _: None,
                    )
                },
            )
        )
        with patch.multiple(
            bootstrap,
            _bootstrap_lock=nullcontext,
            _auto_setup=lambda: None,
            _resolve_tenant=lambda: state.tenant,
            _require_llm_proxy_api_key=lambda: "synthetic-managed-key",
            _wait_for_daemon=Mock(),
            _ensure_plugins=Mock(),
            _provision=Mock(),
        ):
            with self.assertRaisesRegex(RuntimeError, "restore the matching key"):
                bootstrap._run()
            bootstrap._wait_for_daemon.assert_not_called()
            bootstrap._ensure_plugins.assert_not_called()
            bootstrap._provision.assert_not_called()
        self.assert_not_replaced(state)
