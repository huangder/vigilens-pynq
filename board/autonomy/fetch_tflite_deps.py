"""Cache dependencies pinned by TensorFlow Lite's CMake modules for offline builds.

Run with host Python, then pass -C preload-wsl.cmake to WSL CMake (or
preload-native.cmake to native CMake). Downloads only official archives/mirrors.
Hashes specified by the modules, plus Eigen's workspace lock, are enforced.
Other archives have their observed SHA256 recorded, not claimed as upstream pins.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path, PurePosixPath
import posixpath
import re
import shutil
import stat
import tarfile
import tempfile
import urllib.request
import zipfile


DEPENDENCIES = ("abseil-cpp", "eigen", "farmhash", "fft2d", "flatbuffers",
                "gemmlowp", "neon2sse", "cpuinfo", "ml_dtypes", "ruy", "pthreadpool", "fxdiv", "fp16_headers", "xnnpack", "psimd")


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def module_lock(tf_source: Path, name: str) -> dict:
    if name == "psimd":
        # TF 2.16.1 removed its psimd workspace lock, but the locked FP16
        # release still fetches moving master. Use TF's last verified legacy
        # lock, also matching the local build's observed psimd checkout.
        revision = "072586a71b55b7f8c584153d223e95687148a900"
        return {"name": name, "revision": revision,
                "module_sha256": "fb298f29d2c8a82ae1efed583e065e3babe334996d30522238b535b7e11b8317",
                "lock_source": "https://raw.githubusercontent.com/tensorflow/tensorflow/v2.10.0/third_party/psimd/workspace.bzl",
                "expected_sha256": "dc615342bcbe51ca885323e51b68b90ed9bb9fa7df0f4419dbfa0297d5e837b7",
                "urls": [f"https://codeload.github.com/Maratyszcza/psimd/zip/{revision}",
                         f"https://storage.googleapis.com/mirror.tensorflow.org/github.com/Maratyszcza/psimd/archive/{revision}.zip"]}
    if name in ("fxdiv", "xnnpack"):
        # pthreadpool's DownloadFXdiv.cmake uses moving master. Prefer the
        # TensorFlow release's own immutable FXdiv workspace lock instead.
        workspace = tf_source / "tensorflow/workspace2.bzl"
        workspace_name = {"fxdiv": "FXdiv", "xnnpack": "XNNPACK"}[name]
        block = re.search(r'name = "' + workspace_name + r'",(.*?)\n    \)', workspace.read_text(), re.DOTALL).group(1)
        url = re.search(r'https://github.com/[^\"]+\.zip', block).group(0)
        codeload = re.sub(r"https://github.com/([^/]+/[^/]+)/archive/(.+)\.zip$", r"https://codeload.github.com/\1/zip/\2", url)
        result = {"name": name, "module_sha256": digest(workspace),
                "expected_sha256": re.search(r'sha256 = "([0-9a-f]{64})"', block).group(1),
                "revision": url.rsplit("/", 1)[1][:-4],
                "urls": [codeload, "https://storage.googleapis.com/mirror.tensorflow.org/" + url.removeprefix("https://")]}
        if name == "xnnpack":
            module = tf_source / "tensorflow/lite/tools/cmake/modules/xnnpack.cmake"
            tag = re.search(r"\bGIT_TAG\s+([^\s)]+)", module.read_text()).group(1)
            if tag != result["revision"]:
                raise ValueError("XNNPACK CMake tag and workspace commit disagree")
            result["cmake_module_sha256"] = digest(module)
        return result
    path = (tf_source / "tensorflow/lite/cmake/DownloadPThreadPool.cmake" if name == "pthreadpool"
            else tf_source / "tensorflow/lite/tools/cmake/modules" / (name + ".cmake"))
    text = "\n".join(line.split("#", 1)[0] for line in path.read_text().splitlines())
    repo = re.search(r"\bGIT_REPOSITORY\s+([^\s)]+)", text)
    tag = re.search(r"\bGIT_TAG\s+([^\s)]+)", text)
    expected = re.search(r"\bURL_HASH\s+SHA256=([0-9a-fA-F]{64})", text)
    result = {"name": name, "module_sha256": digest(path),
              "expected_sha256": expected.group(1).lower() if expected else None}
    if repo and tag:
        repository, revision = repo.group(1), tag.group(1)
        result.update(repository=repository, revision=revision)
        github = re.fullmatch(r"https://github.com/(.+?)(?:\.git)?", repository)
        if github:
            project = github.group(1)
            result["urls"] = [f"https://codeload.github.com/{project}/tar.gz/{revision}",
                              f"https://storage.googleapis.com/mirror.tensorflow.org/github.com/{project}/archive/{revision}.tar.gz"]
        elif repository == "https://gitlab.com/libeigen/eigen.git":
            workspace = (tf_source / "third_party/eigen3/workspace.bzl").read_text()
            commit = re.search(r'EIGEN_COMMIT\s*=\s*"([^\"]+)"', workspace).group(1)
            if revision != commit:
                raise ValueError("Eigen CMake tag and workspace commit disagree")
            result["expected_sha256"] = re.search(r'EIGEN_SHA256\s*=\s*"([0-9a-f]{64})"', workspace).group(1)
            result["urls"] = [f"https://storage.googleapis.com/mirror.tensorflow.org/gitlab.com/libeigen/eigen/-/archive/{revision}/eigen-{revision}.tar.gz"]
        else:
            raise ValueError(f"Unsupported official repository: {repository}")
    else:
        # Includes URL values defined in a CMake set() such as NEON2SSE_URL.
        urls = re.findall(r"https://[^\s\")]+", text)
        urls = [re.sub(r"https://github.com/([^/]+/[^/]+)/archive/(.+)\.zip$", r"https://codeload.github.com/\1/zip/\2", u) for u in urls]
        result["urls"] = list(dict.fromkeys(urls))
        if not result["urls"]:
            raise ValueError(f"No pinned archive URL: {name}")
    return result


def relative_member(name: str) -> Path | None:
    """Validate archive paths, then remove the common repository root."""
    if "\\" in name or ":" in name:
        raise ValueError(f"Unsafe archive path: {name}")
    member = PurePosixPath(name)
    if member.is_absolute() or any(p == ".." for p in member.parts):
        raise ValueError(f"Unsafe archive path: {name}")
    if any(p.endswith((".", " ")) for p in member.parts):
        raise ValueError(f"Ambiguous Windows path: {name}")
    return Path(*member.parts[1:]) if len(member.parts) > 1 else None


def extract_archive(archive: Path, destination: Path):
    """Extract without filesystem links; materialize safe internal tar links."""
    destination.mkdir(parents=True, exist_ok=True)
    roots = set()
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as source:
            members = source.infolist()
            for item in members:
                relative_member(item.filename)
                roots.add(PurePosixPath(item.filename).parts[0])
                mode = item.external_attr >> 16
                if stat.S_ISLNK(mode):
                    link = source.read(item).decode("utf-8")
                    if "\\" in link or ":" in link:
                        raise ValueError(f"Unsafe ZIP symlink: {link}")
                    target_name = posixpath.normpath(posixpath.join(posixpath.dirname(item.filename), link))
                    relative_member(target_name)
                    target_member = source.getinfo(target_name)
                    if (PurePosixPath(target_name).parts[0] != PurePosixPath(item.filename).parts[0]
                            or target_member.is_dir() or stat.S_ISLNK(target_member.external_attr >> 16)):
                        raise ValueError("ZIP symlink must target an internal regular file")
            if len(roots) != 1:
                raise ValueError("Archive must contain one repository root")
            for item in members:
                relative = relative_member(item.filename)
                if relative is None:
                    continue
                target = destination / relative
                if item.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    content = item
                    if stat.S_ISLNK(item.external_attr >> 16):
                        link = source.read(item).decode("utf-8")
                        content = source.getinfo(posixpath.normpath(posixpath.join(posixpath.dirname(item.filename), link)))
                    with source.open(content) as src, target.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
    else:
        with tarfile.open(archive) as source:
            members = source.getmembers()
            for item in members:
                relative_member(item.name)
                roots.add(PurePosixPath(item.name).parts[0])
                if item.issym():
                    # GitHub FlatBuffers archives contain documentation links.
                    # Materialize only a regular-file target inside this archive;
                    # never create a filesystem symlink or follow an external link.
                    if "\\" in item.linkname or ":" in item.linkname:
                        raise ValueError(f"Unsafe symlink target: {item.linkname}")
                    target_name = posixpath.normpath(posixpath.join(posixpath.dirname(item.name), item.linkname))
                    relative_member(target_name)
                    target_member = source.getmember(target_name)
                    if (PurePosixPath(target_name).parts[0] != PurePosixPath(item.name).parts[0]
                            or not (target_member.isfile() or target_member.isdir())):
                        raise ValueError(f"Symlink must target an internal file/directory: {item.name}")
                    if target_member.isdir() and PurePosixPath(item.name).is_relative_to(PurePosixPath(target_name)):
                        raise ValueError(f"Recursive directory symlink: {item.name}")
                elif not (item.isfile() or item.isdir()):
                    raise ValueError(f"Archive hardlinks/special files are not allowed: {item.name}")
            if len(roots) != 1:
                raise ValueError("Archive must contain one repository root")
            for item in members:
                relative = relative_member(item.name)
                if relative is None:
                    continue
                target = destination / relative
                if item.issym():
                    continue
                if item.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with source.extractfile(item) as src, target.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
                    target.chmod(item.mode & 0o777)
            for item in members:
                if item.issym():
                    target_name = posixpath.normpath(posixpath.join(posixpath.dirname(item.name), item.linkname))
                    origin = destination / relative_member(target_name)
                    target = destination / relative_member(item.name)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if origin.is_dir():
                        shutil.copytree(origin, target)
                    else:
                        shutil.copyfile(origin, target)


def fetch_one(lock: dict, cache: Path, timeout: float = 45) -> dict:
    name = lock["name"]
    archive = cache / "archives" / (name + ".archive")
    source = cache / name
    archive.parent.mkdir(parents=True, exist_ok=True)
    marker = source / ".vg-dependency.json"
    previous = json.loads(marker.read_text()) if marker.exists() else None
    download_marker = archive.with_suffix(".json")
    download_info = json.loads(download_marker.read_text()) if download_marker.exists() else previous
    actual = digest(archive) if archive.exists() else None
    expected = lock["expected_sha256"]
    valid_archive = bool(actual and download_info and download_info.get("download_url")
                         and download_info.get("archive_sha256") == actual
                         and (not expected or actual == expected)
                         and (not previous or previous.get("archive_sha256") == actual))
    selected_url = download_info.get("download_url") if download_info else None
    if not valid_archive:
        errors = []
        for url in lock["urls"]:
            temporary = archive.with_suffix(".part")
            try:
                with urllib.request.urlopen(url, timeout=timeout) as response, temporary.open("wb") as output:
                    shutil.copyfileobj(response, output)
                actual = digest(temporary)
                if expected and actual != expected:
                    raise ValueError(f"SHA256 mismatch: expected {expected}, got {actual}")
                temporary.replace(archive)
                selected_url = url
                download_marker.write_text(json.dumps({"download_url": url, "archive_sha256": actual}) + "\n", encoding="utf-8")
                break
            except Exception as error:
                errors.append(f"{url}: {error}")
                temporary.unlink(missing_ok=True)
        else:
            raise RuntimeError(f"{name} download failed: " + "; ".join(errors))
    result = {**lock, "archive_sha256": actual, "download_url": selected_url,
              "archive_path": archive.resolve().as_posix(), "source_dir": source.resolve().as_posix()}
    if previous:
        if previous.get("archive_sha256") != actual:
            raise ValueError(f"Archive no longer matches extracted source for {name}")
        for field in ("module_sha256", "expected_sha256", "revision", "repository"):
            if previous.get(field) != result.get(field):
                raise ValueError(f"Existing cache lock changed for {name}: {field}")
        # Existing source trees may have been adjusted by CMake (Eigen's own
        # module does this). Never overwrite them when refreshing provenance.
        marker.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    elif source.exists():
        raise ValueError(f"Refusing to overwrite unmarked source directory: {source}")
    else:
        with tempfile.TemporaryDirectory(prefix="extract-", dir=cache) as temporary:
            staged = Path(temporary) / "source"
            extract_archive(archive, staged)
            (staged / marker.name).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            staged.rename(source)
    print(f"{name}: {actual}", flush=True)
    return result


def wsl_path(path: str) -> str:
    match = re.match(r"^([A-Za-z]):/(.*)$", path)
    return f"/mnt/{match.group(1).lower()}/{match.group(2)}" if match else path


def preload(entries: list[dict], *, wsl=False) -> str:
    lines = ["# Generated local dependency cache; no network fetch is needed."]
    for entry in entries:
        path = wsl_path(entry["source_dir"]) if wsl else entry["source_dir"]
        if any(c in path for c in ('"', ';', '\n', '\r', '$')):
            raise ValueError("Path cannot be represented safely in CMake")
        special = {"pthreadpool": "PTHREADPOOL_SOURCE_DIR", "fxdiv": "FXDIV_SOURCE_DIR", "psimd": "PSIMD_SOURCE_DIR"}
        variable = special.get(entry["name"], f'FETCHCONTENT_SOURCE_DIR_{entry["name"].upper()}')
        lines.append(f'set({variable} "{path}" CACHE PATH "Pinned local dependency" FORCE)')
        # XNNPACK's ExternalProject downloads do not consult FetchContent's
        # override variables. Supply its explicit dependency aliases as well.
        alias = {"cpuinfo": "CPUINFO_SOURCE_DIR", "fp16_headers": "FP16_SOURCE_DIR",
                 "xnnpack": "XNNPACK_SOURCE_DIR"}.get(entry["name"])
        if alias:
            lines.append(f'set({alias} "{path}" CACHE PATH "Pinned local dependency" FORCE)')
    lines.append('set(FETCHCONTENT_FULLY_DISCONNECTED ON CACHE BOOL "Use populated dependencies" FORCE)')
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tensorflow-source", required=True, type=Path)
    parser.add_argument("--cache", type=Path, default=Path(".tmp/autonomy/deps"))
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args(argv)
    cache = args.cache.resolve()
    cache.mkdir(parents=True, exist_ok=True)
    locks = [module_lock(args.tensorflow_source, name) for name in DEPENDENCIES]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(lambda lock: fetch_one(lock, cache), locks))
    (cache / "manifest.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    for wsl, name in ((False, "preload-native.cmake"), (True, "preload-wsl.cmake")):
        (cache / name).write_text(preload(results, wsl=wsl), encoding="utf-8")
    print(f"Cached {len(results)} dependencies; preload: {cache / 'preload-wsl.cmake'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
