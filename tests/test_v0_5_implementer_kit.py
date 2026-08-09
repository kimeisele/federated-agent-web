"""Machine checks for the v0.5 implementer kit.

These tests prove the clean-room boundary is enforced by
`interop/v0.2/INPUT_MANIFEST.json` and `scripts/build_v0_5_implementer_kit.py`
without requiring network access or external subprocesses.

The builder is imported as a module (its import has no side effects) and
invoked in-process. Adversarial cases run against synthetic temporary kit
roots so the real repository is never modified.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import shutil
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "interop" / "v0.2" / "INPUT_MANIFEST.json"
BUILDER_PATH = ROOT / "scripts" / "build_v0_5_implementer_kit.py"
EXPECTED_REFERENCE_COMMIT = "2d3edbc49192fd5910389c17c1653d0913fa6434"
TEST_HEAD = "a" * 40

# Refreshed-kit provenance pinned after the deterministic build (never
# invented in advance): manifest files, archive members, archive size, and
# the two external digests.
EXPECTED_MANIFEST_FILES = 65
EXPECTED_ARCHIVE_MEMBERS = 66
EXPECTED_ARCHIVE_SIZE = 51895
EXPECTED_MANIFEST_SHA256 = "54fc3c143b7f8a3b6fa5acb27a8c2b18458ddcba25819a40028b4dc51cb5bfe9"
EXPECTED_ARCHIVE_SHA256 = "8b85eb211cba87308c402d27c404fb76fa13e3102c24eef97446f1413e18ef2e"

# Sanctioned TEST-ONLY public fixture key files (exactly these two).
TEST_ONLY_KEY_FILES = {
    "vectors/signatures/keypair.json",
    "conformance/v0.2/context/test-only-keys.json",
}

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
MANIFEST = json.loads(MANIFEST_PATH.read_text())


def _member_names(archive_bytes: bytes) -> list[str]:
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as tar:
        return [m.name for m in tar.getmembers() if m.isfile()]


def _synthetic_root(path: Path, *, with_git: bool = True) -> Path:
    """Copy every manifested file plus the manifest into an isolated root."""
    root = Path(path)
    if with_git:
        (root / ".git" / "refs" / "heads").mkdir(parents=True)
        (root / ".git" / "refs" / "heads" / "test").write_text(TEST_HEAD + "\n")
        (root / ".git" / "HEAD").write_text("ref: refs/heads/test\n")
    for entry in MANIFEST["files"]:
        dst = root / entry["path"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / entry["path"], dst)
    dst = root / "interop/v0.2/INPUT_MANIFEST.json"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(MANIFEST_PATH, dst)
    return root


@pytest.fixture(scope="module")
def archive_bytes(tmp_path_factory) -> bytes:
    out = tmp_path_factory.mktemp("kit-build")
    result = BUILDER.build(out)
    return Path(result["archive_path"]).read_bytes()


# ---------------------------------------------------------------------------
# Manifest shape and license
# ---------------------------------------------------------------------------

def test_manifest_paths_are_sorted_and_unique():
    paths = [e["path"] for e in MANIFEST["files"]]
    assert paths == sorted(paths)
    assert len(paths) == len(set(paths))


def test_manifest_top_level_shape_is_exact():
    assert set(MANIFEST) == {
        "format_version", "faw_spec_version", "source_repository",
        "reference_material_commit", "files", "forbidden_prefixes",
        "generated_archive_name",
    }
    assert MANIFEST["format_version"] == 1
    assert MANIFEST["faw_spec_version"] == "0.2"
    assert MANIFEST["source_repository"] == "kimeisele/federated-agent-web"
    assert MANIFEST["generated_archive_name"] == "faw-v0.2-implementer-kit.tar.gz"


def test_manifest_shape_rejects_unknown_member(tmp_path):
    root = _synthetic_root(tmp_path / "shape")
    manifest_path = root / "interop/v0.2/INPUT_MANIFEST.json"
    tampered = json.loads(manifest_path.read_text())
    tampered["extra"] = True
    manifest_path.write_text(json.dumps(tampered))
    with pytest.raises(BUILDER.KitBuildError):
        BUILDER.build(tmp_path / "out", root=root)


def test_license_exists_in_repository():
    assert (ROOT / "LICENSE").is_file()


def test_license_listed_exactly_once():
    listed = [e["path"] for e in MANIFEST["files"] if e["path"] == "LICENSE"]
    assert listed == ["LICENSE"]


def test_license_size_and_hash_match():
    entry = next(e for e in MANIFEST["files"] if e["path"] == "LICENSE")
    content = (ROOT / "LICENSE").read_bytes()
    assert len(content) == entry["size_bytes"]
    assert hashlib.sha256(content).hexdigest() == entry["sha256"]
    assert entry["classification"] == "license"


def test_archive_contains_license(archive_bytes):
    assert "LICENSE" in _member_names(archive_bytes)


def test_license_omission_fails_build(tmp_path):
    root = _synthetic_root(tmp_path / "no-license")
    (root / "LICENSE").unlink()
    with pytest.raises(BUILDER.KitBuildError) as exc:
        BUILDER.build(tmp_path / "out", root=root)
    assert "LICENSE" in str(exc.value)


# ---------------------------------------------------------------------------
# Content-hermetic boundary
# ---------------------------------------------------------------------------

def test_every_listed_file_exists():
    for entry in MANIFEST["files"]:
        assert (ROOT / entry["path"]).is_file(), entry["path"]


def test_listed_sizes_and_hashes_match():
    for entry in MANIFEST["files"]:
        content = (ROOT / entry["path"]).read_bytes()
        assert len(content) == entry["size_bytes"], entry["path"]
        assert hashlib.sha256(content).hexdigest() == entry["sha256"], entry["path"]


def test_no_forbidden_path_is_included():
    assert MANIFEST["forbidden_prefixes"] == FORBIDDEN_PREFIXES
    for entry in MANIFEST["files"]:
        for prefix in MANIFEST["forbidden_prefixes"]:
            assert not entry["path"].startswith(prefix), entry["path"]


def test_no_traversal_or_absolute_path():
    for entry in MANIFEST["files"]:
        p = entry["path"]
        assert not p.startswith("/")
        assert not p.startswith("./")
        assert "\\" not in p
        assert ".." not in Path(p).parts


def test_no_symlink_included_in_repository():
    for entry in MANIFEST["files"]:
        assert not (ROOT / entry["path"]).is_symlink(), entry["path"]


def test_changed_allowlisted_file_fails_with_hash_mismatch(tmp_path):
    root = _synthetic_root(tmp_path / "changed")
    target = root / "SPEC.md"
    data = bytearray(target.read_bytes())
    data[0] ^= 0x01
    target.write_bytes(bytes(data))
    with pytest.raises(BUILDER.KitBuildError) as exc:
        BUILDER.build(tmp_path / "out", root=root)
    assert "SHA-256 mismatch" in str(exc.value)


def test_extra_file_under_vectors_fails_build(tmp_path):
    root = _synthetic_root(tmp_path / "extra-vector")
    (root / "vectors" / "unmanifested.txt").write_text("noise\n")
    with pytest.raises(BUILDER.KitBuildError) as exc:
        BUILDER.build(tmp_path / "out", root=root)
    assert "unmanifested.txt" in str(exc.value)


def test_symlink_substitution_fails_build(tmp_path):
    root = _synthetic_root(tmp_path / "symlink")
    target = root / "LICENSE"
    target.unlink()
    target.symlink_to(root / "SPEC.md")
    with pytest.raises(BUILDER.KitBuildError) as exc:
        BUILDER.build(tmp_path / "out", root=root)
    assert "symlink" in str(exc.value)


def test_non_allowlisted_contamination_never_enters_archive(tmp_path):
    clean_root = _synthetic_root(tmp_path / "clean")
    dirty_root = _synthetic_root(tmp_path / "dirty")
    (dirty_root / "notes.txt").write_text("noise\n")
    (dirty_root / "src").mkdir(parents=True)
    (dirty_root / "src" / "copied_reference.py").write_text("print('x')\n")
    (dirty_root / "tests").mkdir(parents=True)
    (dirty_root / "tests" / "copied_test.py").write_text("def test_x():\n    pass\n")

    clean = BUILDER.build(tmp_path / "out-clean", root=clean_root)
    dirty = BUILDER.build(tmp_path / "out-dirty", root=dirty_root)
    clean_bytes = Path(clean["archive_path"]).read_bytes()
    dirty_bytes = Path(dirty["archive_path"]).read_bytes()
    assert clean["archive_sha256"] == dirty["archive_sha256"]
    assert clean_bytes == dirty_bytes
    for name in _member_names(dirty_bytes):
        assert not name.startswith(("src/", "tests/"))
        assert name != "notes.txt"


def test_archive_contains_exactly_manifest_files_plus_manifest(tmp_path):
    root = _synthetic_root(tmp_path / "exact")
    result = BUILDER.build(tmp_path / "out", root=root)
    names = set(_member_names(Path(result["archive_path"]).read_bytes()))
    expected = {e["path"] for e in MANIFEST["files"]} | {"interop/v0.2/INPUT_MANIFEST.json"}
    assert names == expected


def test_build_fails_when_head_cannot_be_determined(tmp_path):
    root = _synthetic_root(tmp_path / "nohead", with_git=False)
    with pytest.raises(BUILDER.KitBuildError) as exc:
        BUILDER.build(tmp_path / "out", root=root)
    assert "HEAD" in str(exc.value)


# ---------------------------------------------------------------------------
# Archive determinism and safety
# ---------------------------------------------------------------------------

def test_kit_contains_no_python_files(archive_bytes):
    for name in _member_names(archive_bytes):
        assert not name.endswith((".py", ".pyc", ".pyo")), name


def test_kit_contains_no_src_tests_git_or_implementation_report(archive_bytes):
    for name in _member_names(archive_bytes):
        assert not name.startswith(("src/", "tests/", ".git/")), name
        assert name != "docs/IMPLEMENTATION_REPORT.md", name


def test_two_builds_are_byte_identical(tmp_path):
    a = BUILDER.build(tmp_path / "a")
    b = BUILDER.build(tmp_path / "b")
    assert a["archive_path"] != b["archive_path"]
    assert Path(a["archive_path"]).read_bytes() == Path(b["archive_path"]).read_bytes()


def test_archive_sha256_identical_across_builds(tmp_path):
    a = BUILDER.build(tmp_path / "a")
    b = BUILDER.build(tmp_path / "b")
    assert a["archive_sha256"] == b["archive_sha256"]


def test_archive_extraction_stays_within_destination(archive_bytes, tmp_path):
    dest = tmp_path / "extracted"
    dest.mkdir()
    names = _member_names(archive_bytes)
    for name in names:
        assert ".." not in Path(name).parts
        assert not name.startswith("/")
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as tar:
        tar.extractall(dest)
    for name in names:
        assert (dest / name).is_file(), name
        assert (dest / name).resolve().is_relative_to(dest.resolve()), name


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------

def test_provenance_is_four_part_and_distinct(tmp_path):
    result = BUILDER.build(tmp_path / "real")
    assert result["reference_material_commit"] == EXPECTED_REFERENCE_COMMIT
    assert result["build_head_sha"] == BUILDER.read_head_sha(ROOT)
    assert len(result["manifest_sha256"]) == 64
    assert len(result["archive_sha256"]) == 64
    assert result["file_count"] == len(MANIFEST["files"]) + 1


def test_reference_material_commit_is_recorded():
    assert MANIFEST["reference_material_commit"] == EXPECTED_REFERENCE_COMMIT
    assert MANIFEST["reference_material_commit"] != "source_commit"  # field renamed


def test_every_schema_golden_vector_and_conformance_file_is_present():
    listed = {e["path"] for e in MANIFEST["files"]}
    expected = {str(p.relative_to(ROOT)) for p in sorted((ROOT / "schemas").rglob("*")) if p.is_file()}
    expected |= {str(p.relative_to(ROOT)) for p in sorted((ROOT / "vectors").rglob("*")) if p.is_file()}
    expected |= {str(p.relative_to(ROOT)) for p in sorted((ROOT / "conformance" / "v0.2").rglob("*")) if p.is_file()}
    assert expected <= listed
    assert not (expected - listed)


def test_conformance_package_manifest_is_listed_and_byte_exact():
    """The outer kit manifest byte-hashes conformance/v0.2/manifest.json."""
    rel = "conformance/v0.2/manifest.json"
    entry = next(e for e in MANIFEST["files"] if e["path"] == rel)
    content = (ROOT / rel).read_bytes()
    assert len(content) == entry["size_bytes"]
    assert hashlib.sha256(content).hexdigest() == entry["sha256"]


def test_missing_conformance_file_fails_build(tmp_path):
    root = _synthetic_root(tmp_path / "missing-conformance")
    (root / "conformance/v0.2/negative/N15.json").unlink()
    with pytest.raises(BUILDER.KitBuildError) as exc:
        BUILDER.build(tmp_path / "out", root=root)
    assert "N15.json" in str(exc.value)


def test_extra_file_under_conformance_fails_build(tmp_path):
    root = _synthetic_root(tmp_path / "extra-conformance")
    (root / "conformance" / "v0.2" / "context").mkdir(parents=True, exist_ok=True)
    (root / "conformance/v0.2/context/unmanifested.json").write_text("{}\n")
    with pytest.raises(BUILDER.KitBuildError) as exc:
        BUILDER.build(tmp_path / "out", root=root)
    assert "unmanifested.json" in str(exc.value)


def test_profile_is_a_required_fixed_kit_file(tmp_path):
    listed = {e["path"] for e in MANIFEST["files"]}
    assert "docs/FAW_V0_2_INTEROPERABILITY_PROFILE.md" in listed
    entry = next(e for e in MANIFEST["files"] if e["path"] == "docs/FAW_V0_2_INTEROPERABILITY_PROFILE.md")
    assert entry["classification"] == "normative"
    root = _synthetic_root(tmp_path / "no-profile")
    (root / "docs/FAW_V0_2_INTEROPERABILITY_PROFILE.md").unlink()
    with pytest.raises(BUILDER.KitBuildError) as exc:
        BUILDER.build(tmp_path / "out", root=root)
    assert "FAW_V0_2_INTEROPERABILITY_PROFILE.md" in str(exc.value)


# ---------------------------------------------------------------------------
# TEST-ONLY key hygiene
# ---------------------------------------------------------------------------

def test_sanctioned_test_only_key_files_are_listed():
    listed = {e["path"] for e in MANIFEST["files"]}
    assert TEST_ONLY_KEY_FILES <= listed
    for rel in TEST_ONLY_KEY_FILES:
        entry = next(e for e in MANIFEST["files"] if e["path"] == rel)
        assert entry["classification"] == "conformance-fixture", rel


def test_no_other_kit_member_contains_private_key_material(archive_bytes):
    """Only the two sanctioned TEST-ONLY files may carry private key data."""
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            if member.name in TEST_ONLY_KEY_FILES:
                continue
            content = tar.extractfile(member).read()
            assert b'"private_key"' not in content, f"private key material in {member.name}"
            assert b"BEGIN PRIVATE KEY" not in content, f"private key material in {member.name}"


def test_arbitrary_private_key_material_rejected(tmp_path):
    """A manifest entry carrying private key material outside the two
    sanctioned paths violates the allowlist and fails the build."""
    root = _synthetic_root(tmp_path / "bad-key")
    manifest_path = root / "interop/v0.2/INPUT_MANIFEST.json"
    tampered = json.loads(manifest_path.read_text())
    fake = {
        "path": "private/deploy-key.json",
        "sha256": hashlib.sha256(b'{"private_key": "x"}').hexdigest(),
        "size_bytes": 17,
        "classification": "conformance-fixture",
        "reason": "tampered",
    }
    tampered["files"] = sorted(tampered["files"] + [fake], key=lambda e: e["path"])
    manifest_path.write_text(json.dumps(tampered))
    with pytest.raises(BUILDER.KitBuildError) as exc:
        BUILDER.build(tmp_path / "out", root=root)
    assert "outside allowlist" in str(exc.value)


def test_extra_private_key_file_under_conformance_fails_build(tmp_path):
    """Adding an arbitrary private-key-bearing file inside the conformance
    directory is an unmanifested extra and fails completeness."""
    root = _synthetic_root(tmp_path / "extra-key")
    (root / "conformance/v0.2/context/deploy-keys.json").write_text(
        '{"private_key": "not-allowed"}\n'
    )
    with pytest.raises(BUILDER.KitBuildError) as exc:
        BUILDER.build(tmp_path / "out", root=root)
    assert "deploy-keys.json" in str(exc.value)


def test_manifest_does_not_list_itself(tmp_path):
    listed = {e["path"] for e in MANIFEST["files"]}
    assert "interop/v0.2/INPUT_MANIFEST.json" not in listed
    result = BUILDER.build(tmp_path / "self")
    assert "interop/v0.2/INPUT_MANIFEST.json" in _member_names(
        Path(result["archive_path"]).read_bytes()
    )


# ---------------------------------------------------------------------------
# Linked-worktree HEAD resolution
# ---------------------------------------------------------------------------

FEATURE_SHA = "0123456789abcdef0123456789abcdef01234567"


def _linked_worktree(tmp_path: Path, *, packed: bool) -> Path:
    common = tmp_path / "common.git"
    admin = common / "worktrees" / "example"
    root = tmp_path / "root"
    (common / "refs" / "heads").mkdir(parents=True)
    if packed:
        (common / "packed-refs").write_text(
            "# pack-refs with: peeled fully-peeled sorted \n"
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa refs/tags/v1\n"
            "^bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\n"
            f"{FEATURE_SHA} refs/heads/feature\n"
        )
    else:
        (common / "refs" / "heads" / "feature").write_text(FEATURE_SHA + "\n")
    admin.mkdir(parents=True)
    (admin / "HEAD").write_text("ref: refs/heads/feature\n")
    (admin / "commondir").write_text("../..\n")
    root.mkdir()
    (root / ".git").write_text(f"gitdir: {admin}\n")
    return root


def test_linked_worktree_loose_ref_resolution(tmp_path):
    root = _linked_worktree(tmp_path, packed=False)
    assert BUILDER.read_head_sha(root) == FEATURE_SHA


def test_linked_worktree_packed_ref_resolution(tmp_path):
    root = _linked_worktree(tmp_path, packed=True)
    assert BUILDER.read_head_sha(root) == FEATURE_SHA


@pytest.mark.parametrize("value", ["abc123", "A" * 40, "not-a-sha"])
def test_malformed_detached_head_rejected(tmp_path, value):
    root = _synthetic_root(tmp_path / f"bad-{len(value)}")
    (root / ".git" / "HEAD").write_text(value + "\n")
    with pytest.raises(BUILDER.KitBuildError):
        BUILDER.read_head_sha(root)


def test_empty_loose_ref_rejected(tmp_path):
    root = _synthetic_root(tmp_path / "empty-ref")
    (root / ".git" / "refs" / "heads" / "test").write_text("")
    with pytest.raises(BUILDER.KitBuildError):
        BUILDER.read_head_sha(root)


def test_malformed_packed_ref_row_rejected(tmp_path):
    root = _linked_worktree(tmp_path, packed=True)
    (root.parent / "common.git" / "packed-refs").write_text(
        "malformed-row-without-space\n"
        f"{FEATURE_SHA} refs/heads/feature\n"
    )
    with pytest.raises(BUILDER.KitBuildError):
        BUILDER.read_head_sha(root)


def test_no_network_or_subprocess_is_required(tmp_path):
    source = BUILDER_PATH.read_text()
    for forbidden in ("import subprocess", "from subprocess", "import socket",
                      "import urllib", "from urllib", "import http", "requests",
                      "import ftplib"):
        assert forbidden not in source, forbidden
    result = BUILDER.build(tmp_path / "offline")
    assert Path(result["archive_path"]).is_file()
    assert result["file_count"] == len(MANIFEST["files"]) + 1


# ---------------------------------------------------------------------------
# Refreshed-kit provenance (pinned after the deterministic build)
# ---------------------------------------------------------------------------

def test_refreshed_kit_counts_and_digests_are_pinned():
    assert len(MANIFEST["files"]) == EXPECTED_MANIFEST_FILES
    manifest_bytes = MANIFEST_PATH.read_bytes()
    assert hashlib.sha256(manifest_bytes).hexdigest() == EXPECTED_MANIFEST_SHA256


def test_refreshed_archive_measurements_are_pinned(tmp_path):
    result = BUILDER.build(tmp_path / "pinned")
    assert result["reference_material_commit"] == EXPECTED_REFERENCE_COMMIT
    assert result["manifest_sha256"] == EXPECTED_MANIFEST_SHA256
    assert result["archive_sha256"] == EXPECTED_ARCHIVE_SHA256
    assert result["archive_size_bytes"] == EXPECTED_ARCHIVE_SIZE
    assert result["file_count"] == EXPECTED_ARCHIVE_MEMBERS
    names = _member_names(Path(result["archive_path"]).read_bytes())
    assert len(names) == EXPECTED_ARCHIVE_MEMBERS
