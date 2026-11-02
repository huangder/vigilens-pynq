import hashlib
import importlib.util
import io
import json
from pathlib import Path
import stat
import tarfile
import zipfile

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "fetch_tflite_deps.py"
spec = importlib.util.spec_from_file_location("fetch_tflite_deps", SCRIPT)
fetch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetch)


@pytest.mark.parametrize("name", ["../escape", "/absolute", "root/../../escape", "C:/root/file", "root\\escape", "root/file:stream"])
def test_unsafe_archive_path_rejected(name):
    with pytest.raises(ValueError):
        fetch.relative_member(name)


def archive_bytes(*, link=False, escaped=False):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as output:
        member = tarfile.TarInfo("root/header.h")
        member.size = 5
        output.addfile(member, io.BytesIO(b"hello"))
        if link:
            member = tarfile.TarInfo("root/sub/link.h")
            member.type = tarfile.SYMTYPE
            member.linkname = "../../escape" if escaped else "../header.h"
            output.addfile(member)
    return buffer.getvalue()


def test_tar_internal_link_materialized_and_external_rejected(tmp_path):
    archive = tmp_path / "source.tar.gz"
    archive.write_bytes(archive_bytes(link=True))
    destination = tmp_path / "good"
    fetch.extract_archive(archive, destination)
    assert (destination / "sub/link.h").read_bytes() == b"hello"
    assert not (destination / "sub/link.h").is_symlink()
    archive.write_bytes(archive_bytes(link=True, escaped=True))
    with pytest.raises((ValueError, KeyError)):
        fetch.extract_archive(archive, tmp_path / "bad")
    assert not (tmp_path / "escape").exists()


def test_zip_traversal_rejected(tmp_path):
    archive = tmp_path / "source.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("root/../../escaped", "bad")
    with pytest.raises(ValueError):
        fetch.extract_archive(archive, tmp_path / "dest")
    assert not (tmp_path / "escaped").exists()


@pytest.mark.parametrize("escape", [False, True])
def test_zip_internal_link_materialized_and_external_rejected(tmp_path, escape):
    archive = tmp_path / "source.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("root/tools/xngen.py", "hello")
        link = zipfile.ZipInfo("root/tools/xngen")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        output.writestr(link, "../../../outside" if escape else "xngen.py")
    if escape:
        with pytest.raises((ValueError, KeyError)):
            fetch.extract_archive(archive, tmp_path / "dest")
    else:
        fetch.extract_archive(archive, tmp_path / "dest")
        assert (tmp_path / "dest/tools/xngen").read_text() == "hello"
        assert not (tmp_path / "dest/tools/xngen").is_symlink()


def test_hash_mismatch_rejected_before_extraction(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch.urllib.request, "urlopen", lambda *a, **kw: io.BytesIO(b"bad archive"))
    lock = {"name": "fixture", "expected_sha256": "0" * 64, "urls": ["https://example.invalid/source"], "module_sha256": "fixture"}
    with pytest.raises(RuntimeError, match="SHA256 mismatch"):
        fetch.fetch_one(lock, tmp_path)
    assert not (tmp_path / "fixture").exists()


def test_cached_dependency_reused_offline_and_lock_drift_rejected(tmp_path, monkeypatch):
    data = archive_bytes()
    monkeypatch.setattr(fetch.urllib.request, "urlopen", lambda *a, **kw: io.BytesIO(data))
    lock = {"name": "fixture", "expected_sha256": hashlib.sha256(data).hexdigest(),
            "urls": ["https://example.invalid/source"], "module_sha256": "fixture"}
    first = fetch.fetch_one(lock, tmp_path)
    def network_forbidden(*a, **kw):
        pytest.fail("A valid cache must not use network")
    monkeypatch.setattr(fetch.urllib.request, "urlopen", network_forbidden)
    assert fetch.fetch_one(lock, tmp_path) == first
    with pytest.raises(ValueError, match="lock changed"):
        fetch.fetch_one({**lock, "module_sha256": "changed"}, tmp_path)


def test_preload_wsl_conversion_and_name():
    entries = [{"name": "abseil-cpp", "source_dir": "D:/Project/deps/abseil-cpp"}]
    result = fetch.preload(entries, wsl=True)
    assert "FETCHCONTENT_SOURCE_DIR_ABSEIL-CPP" in result
    assert "/mnt/d/Project/deps/abseil-cpp" in result
    assert "FETCHCONTENT_FULLY_DISCONNECTED ON" in result
    xnn_result = fetch.preload([{"name": "fp16_headers", "source_dir": "D:/deps/fp16"},
                               {"name": "cpuinfo", "source_dir": "D:/deps/cpuinfo"}], wsl=True)
    assert 'set(FP16_SOURCE_DIR "/mnt/d/deps/fp16"' in xnn_result
    assert 'set(CPUINFO_SOURCE_DIR "/mnt/d/deps/cpuinfo"' in xnn_result


def test_interrupted_cache_download_hash_is_checked_before_reuse(tmp_path, monkeypatch):
    archives = tmp_path / "archives"
    archives.mkdir()
    (archives / "fixture.archive").write_bytes(b"tampered")
    (archives / "fixture.json").write_text(json.dumps({
        "download_url": "https://example.invalid/pinned",
        "archive_sha256": hashlib.sha256(archive_bytes()).hexdigest()}))
    attempts = []
    def unavailable(url, **kwargs):
        attempts.append(url)
        raise OSError("offline")
    monkeypatch.setattr(fetch.urllib.request, "urlopen", unavailable)
    lock = {"name": "fixture", "expected_sha256": None,
            "urls": ["https://example.invalid/pinned"], "module_sha256": "fixture"}
    with pytest.raises(RuntimeError, match="download failed"):
        fetch.fetch_one(lock, tmp_path)
    assert attempts == lock["urls"]
    assert not (tmp_path / "fixture").exists()


def test_actual_tf_modules_have_exact_pins():
    root = Path(__file__).resolve().parents[3]
    tf = root / ".tmp/autonomy/src/tensorflow-2.16.1"
    if not tf.is_dir():
        pytest.skip("Official TensorFlow source not installed locally")
    locks = [fetch.module_lock(tf, name) for name in fetch.DEPENDENCIES]
    assert len(locks) == 15
    assert next(x for x in locks if x["name"] == "eigen")["expected_sha256"] == "35ba771e30c735a4215ed784d7e032086cf89fe6622dce4d793c45dd74373362"
    assert next(x for x in locks if x["name"] == "flatbuffers")["revision"] == "v23.5.26"
    assert next(x for x in locks if x["name"] == "xnnpack")["expected_sha256"] == "434fe914cb52da3e66ba920082af969f527f23729fff182aecd87ac5324e9f90"
    psimd = next(x for x in locks if x["name"] == "psimd")
    assert psimd["revision"] == "072586a71b55b7f8c584153d223e95687148a900"
    assert "/v2.10.0/" in psimd["lock_source"]
    assert "PSIMD_SOURCE_DIR" in fetch.preload([psimd | {"source_dir": "D:/deps/psimd"}], wsl=True)
