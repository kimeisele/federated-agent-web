"""Machine checks for the v0.5 implementer kit.

These tests prove the clean-room boundary is enforced by
`interop/v0.2/INPUT_MANIFEST.json` and `scripts/build_v0_5_implementer_kit.py`
without requiring network access or external subprocesses.

The builder is imported as a module (its import has no side effects) and
invoked in-process; builds go to pytest tmp directories.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "interop" / "v0.2" / "INPUT_MANIFEST.json"
BUILDER_PATH = ROOT / "scripts" / "build_v0_5_implementer_kit.py"
EXPECTED_SOURCE_COMMIT = "bb85221c894473adfd17dceb2c7d3685d9e266ea"

FORBIDDEN_PREFIXES = [
    "src/", "tests/", "examples/", ".github/", "scripts/", "pyproject.toml",
    "docs/IMPLEMENTATION_REPORT.md", "docs/REUSE_REPORT.md",
    "docs/TRACEABILITY_V0_2.json", "docs/TRANSPORT_CONFORMANCE.md",
    "docs/NADI_LIVE_REHEARSAL.md", "docs/ADAPTER_NADI.md",
]


def _load_builder():
    spec = importlib.util.spec_from_file_location("faw_kit_builder", BUILDER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


BUILDER = _load_builder()


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text())


@pytest.fixture(scope="module")
def archive_bytes(tmp_path_factory) -> bytes:
    out = tmp_path_factory.mktemp("kit-build")
    result = BUILDER.build(out)
    return (Path(result["archive_path"])).read_bytes()


def _member_names(archive_bytes: bytes) -> list[str]:
    with tarfile.open(fileobj=__import__("io").BytesIO(archive_bytes), mode="r:gz") as tar:
        return [m.name for m in tar.getmembers() if m.isfile()]


def test_manifest_paths_are_sorted_and_unique(manifest):
    paths = [e["path"] for e in manifest["files"]]
    assert paths == sorted(paths)
    assert len(paths) == len(set(paths))


def test_every_listed_file_exists(manifest):
    for entry in manifest["files"]:
        assert (ROOT / entry["path"]).is_file(), entry["path"]


def test_listed_sizes_and_hashes_match(manifest):
    for entry in manifest["files"]:
        content = (ROOT / entry["path"]).read_bytes()
        assert len(content) == entry["size_bytes"], entry["path"]
        assert hashlib.sha256(content).hexdigest() == entry["sha256"], entry["path"]


def test_no_forbidden_path_is_included(manifest):
    assert manifest["forbidden_prefixes"] == FORBIDDEN_PREFIXES
    for entry in manifest["files"]:
        for prefix in manifest["forbidden_prefixes"]:
            assert not entry["path"].startswith(prefix), entry["path"]


def test_no_traversal_or_absolute_path(manifest):
    for entry in manifest["files"]:
        p = entry["path"]
        assert not p.startswith("/")
        assert not p.startswith("./")
        assert "\\" not in p
        assert ".." not in Path(p).parts


def test_no_symlink_included(manifest):
    for entry in manifest["files"]:
        assert not (ROOT / entry["path"]).is_symlink(), entry["path"]


def test_kit_contains_no_python_files(archive_bytes):
    for name in _member_names(archive_bytes):
        assert not name.endswith((".py", ".pyc", ".pyo")), name


def test_kit_contains_no_src_tests_git_or_implementation_report(archive_bytes):
    for name in _member_names(archive_bytes):
        assert not name.startswith(("src/", "tests/", ".git/")), name
        assert name != "docs/IMPLEMENTATION_REPORT.md", name


def test_two_builds_are_byte_identical(tmp_path):
    a = Path(tmp_path / "a")
    b = Path(tmp_path / "b")
    ra = BUILDER.build(a)
    rb = BUILDER.build(b)
    assert ra["archive_path"] != rb["archive_path"]
    assert (Path(ra["archive_path"])).read_bytes() == (Path(rb["archive_path"])).read_bytes()


def test_archive_sha256_identical_across_builds(tmp_path):
    ra = BUILDER.build(tmp_path / "a")
    rb = BUILDER.build(tmp_path / "b")
    assert ra["archive_sha256"] == rb["archive_sha256"]


def test_archive_extraction_stays_within_destination(archive_bytes, tmp_path):
    dest = tmp_path / "extracted"
    dest.mkdir()
    names = _member_names(archive_bytes)
    for name in names:
        assert ".." not in Path(name).parts
        assert not name.startswith("/")
    with tarfile.open(fileobj=__import__("io").BytesIO(archive_bytes), mode="r:gz") as tar:
        tar.extractall(dest)
    for name in names:
        assert (dest / name).is_file(), name
        assert (dest / name).resolve().is_relative_to(dest.resolve()), name


def test_every_schema_and_golden_vector_file_is_present(manifest):
    listed = {e["path"] for e in manifest["files"]}
    expected = {str(p.relative_to(ROOT)) for p in sorted((ROOT / "schemas").rglob("*")) if p.is_file()}
    expected |= {str(p.relative_to(ROOT)) for p in sorted((ROOT / "vectors").rglob("*")) if p.is_file()}
    assert expected <= listed
    missing = expected - listed
    assert not missing


def test_exact_source_commit_is_recorded(manifest):
    assert manifest["source_commit"] == EXPECTED_SOURCE_COMMIT
    assert manifest["source_repository"] == "kimeisele/federated-agent-web"
    assert manifest["generated_archive_name"] == "faw-v0.2-implementer-kit.tar.gz"


def test_manifest_does_not_list_itself(manifest, tmp_path):
    listed = {e["path"] for e in manifest["files"]}
    assert "interop/v0.2/INPUT_MANIFEST.json" not in listed
    # but the archive still carries the manifest as a self-describing member
    archive = BUILDER.build(tmp_path / "self")
    assert "interop/v0.2/INPUT_MANIFEST.json" in _member_names(
        Path(archive["archive_path"]).read_bytes()
    )


def test_no_network_or_subprocess_is_required(manifest, tmp_path):
    source = BUILDER_PATH.read_text()
    for forbidden in ("import subprocess", "from subprocess", "import socket",
                      "import urllib", "from urllib", "import http", "requests",
                      "import ftplib"):
        assert forbidden not in source, forbidden
    # building must succeed offline with no external commands
    result = BUILDER.build(tmp_path / "offline")
    assert (Path(result["archive_path"])).is_file()
    assert result["file_count"] == len(manifest["files"]) + 1  # files + manifest itself
