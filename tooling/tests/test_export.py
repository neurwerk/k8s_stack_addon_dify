"""The product stages exactly its declared inert resource through the shared helper."""

from importlib.resources import files

from dify_tooling.cli import stage


def test_packaged_payload_is_staged_read_only_without_native_imports(tmp_path):
    assert stage(["--destination", str(tmp_path)]) == 0
    payload = tmp_path / "sync_models.py"
    assert (
        payload.read_bytes()
        == files("dify_tooling.payloads").joinpath("sync_models.py").read_bytes()
    )
    assert payload.stat().st_mode & 0o222 == 0
    before = payload.stat().st_mtime_ns
    assert stage(["--destination", str(tmp_path)]) == 0
    assert payload.stat().st_mtime_ns == before
