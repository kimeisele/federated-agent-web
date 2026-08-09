#!/usr/bin/env python3
"""Build the reproducible FAW v0.2 implementer kit (v0.5 preparation).

The kit is the bounded, hash-verified input set for a clean-room second
implementation (see docs/V0_5_CLEAN_ROOM_PROTOCOL.md and ADR 0002).

The build is content-hermetic with respect to the allowlist and the
byte-exact manifest: files outside the allowlist do not affect or enter the
archive. The builder does not attempt to prove that the complete Git
worktree is clean.

Guarantees:
- no network operation; no subprocess; pure Python standard library;
- every listed file verified by exact byte size and SHA-256 against
  interop/v0.2/INPUT_MANIFEST.json;
- manifest structure validated strictly (required keys, unknown members
  rejected, fixed allowlist, classification set, normalized paths);
- every existing file under conformance/v0.2/, schemas/ and vectors/ must
  be listed (including conformance/v0.2/manifest.json); every required
  fixed file (including LICENSE) must be listed; omission is a build
  failure;
- only listed files plus the manifest itself enter the archive;
- deterministic tar.gz: sorted members, POSIX separators, uid/gid zero,
  empty owner/group names, fixed permissions, mtime zero, gzip mtime zero;
- the completed archive is scanned again for forbidden, unsafe, or Python
  members.

Four-part provenance is printed by every build:

    reference_material_commit   the frozen FAW material commit (manifest)
    build_head_sha              the repository state the kit was assembled
                                from (resolved from .git, no subprocess)
    manifest_sha256             the exact external manifest bytes
    archive_sha256              the complete delivered archive

The manifest cannot hash itself recursively; its own digest and the archive
digest are defined externally by this build's printed output.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import re
import stat
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_RELPATH = "interop/v0.2/INPUT_MANIFEST.json"
ARCHIVE_NAME = "faw-v0.2-implementer-kit.tar.gz"
SOURCE_REPOSITORY = "kimeisele/federated-agent-web"

# Required fixed allowlisted files; each MUST be listed in the manifest.
FIXED_ALLOWED = {
    "SPEC.md",
    "docs/federated-agent-web-build-spec-v0.2.md",
    "docs/FAW_V0_2_INTEROPERABILITY_PROFILE.md",
    "SECURITY.md",
    "LICENSE",
    "docs/V0_5_IMPLEMENTER_BRIEF.md",
    MANIFEST_RELPATH,
}
# Every existing file under these directories must be listed in the
# manifest (conformance/v0.2/ completeness includes the package's own
# manifest.json — the package manifest's self-hash limitation applies only
# to its internal files map, never to this outer manifest).
# Directories whose every existing file must be listed in the manifest.
# Conformance is scoped to exactly conformance/v0.2/ — arbitrary
# conformance/** is NOT part of the kit allowlist. The package's own
# manifest.json is included (its self-hash limitation applies only to its
# internal files map, never to this outer manifest).
ALLOWED_DIR_PREFIXES = ("conformance/v0.2", "schemas", "vectors")
COMPLETENESS_ROOTS = (Path("conformance") / "v0.2", Path("schemas"), Path("vectors"))

# The only files that may carry TEST-ONLY private fixture key material.
# Both are public reproducibility fixtures only: no authority, never a
# deployment identity, never production credentials.
TEST_ONLY_KEY_PATHS = frozenset({
    "vectors/signatures/keypair.json",
    "conformance/v0.2/context/test-only-keys.json",
})
PRIVATE_KEY_MARKERS = (b'"private_key"', b'"private_key_b64url"', b"BEGIN PRIVATE KEY")
ALLOWED_CLASSIFICATIONS = {
    "normative",
    "normative-summary",
    "conformance-fixture",
    "non-normative-guidance",
    "license",
}
REQUIRED_TOP_LEVEL = {
    "format_version",
    "faw_spec_version",
    "source_repository",
    "reference_material_commit",
    "files",
    "forbidden_prefixes",
    "generated_archive_name",
}
ENTRY_KEYS = {"path", "sha256", "size_bytes", "classification", "reason"}

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


class KitBuildError(Exception):
    """Raised for any precondition or verification failure."""


# ---------------------------------------------------------------------------
# Provenance: HEAD resolution (no subprocess)
# ---------------------------------------------------------------------------

def _resolve_git_dirs(root: Path) -> tuple[Path, Path]:
    """Return (git_dir, common_dir) for the repository at root.

    git_dir is the worktree administration directory: `.git/` for ordinary
    clones, or the target of a `.git` indirection file for linked
    worktrees. common_dir is where shared refs live: it equals git_dir for
    ordinary clones and is read from the `commondir` file for linked
    worktrees. Relative paths are resolved against their containing
    directory.
    """
    git_dir = root / ".git"
    if git_dir.is_file():
        value = git_dir.read_text().strip()
        if not value.startswith("gitdir:"):
            raise KitBuildError("invalid .git indirection")
        git_dir = Path(value.split(":", 1)[1].strip())
        if not git_dir.is_absolute():
            git_dir = (root / git_dir).resolve()

    common_dir = git_dir
    commondir_file = git_dir / "commondir"
    if commondir_file.is_file():
        value = commondir_file.read_text().strip()
        if not value:
            raise KitBuildError("empty Git commondir")
        common_dir = Path(value)
        if not common_dir.is_absolute():
            common_dir = (git_dir / common_dir).resolve()
    return git_dir, common_dir


def _validated_sha(text: str, what: str) -> str:
    """Strip and validate a candidate SHA; reject anything that is not
    exactly 40 lowercase hexadecimal characters."""
    sha = text.strip()
    if not _COMMIT_RE.fullmatch(sha):
        raise KitBuildError(f"malformed {what}: {sha!r}")
    return sha


def read_head_sha(root: Path) -> str:
    """Return the current Git HEAD SHA.

    Resolves `.git` indirection (linked worktrees), the worktree-specific
    HEAD, loose shared refs (git_dir then common_dir), and packed shared
    refs from the common directory. Every resolved SHA is validated as 40
    lowercase hex characters; malformed provenance fails with
    KitBuildError. No subprocess, network, or Git-index parsing is used.
    """
    git_dir, common_dir = _resolve_git_dirs(root)
    try:
        head = (git_dir / "HEAD").read_text().strip()
    except OSError as exc:
        raise KitBuildError(f"cannot determine repository HEAD: {exc}") from exc

    if head.startswith("ref:"):
        ref = head.split(":", 1)[1].strip()
        for base in (git_dir, common_dir):
            try:
                return _validated_sha((base / ref).read_text(), f"loose ref {ref}")
            except OSError:
                continue
        packed = common_dir / "packed-refs"
        try:
            for line in packed.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("^"):
                    continue
                if " " not in line:
                    raise KitBuildError(f"malformed packed-ref row: {line!r}")
                sha, name = line.split(" ", 1)
                if name.strip() == ref:
                    return _validated_sha(sha, f"packed ref {ref}")
        except OSError:
            pass
        raise KitBuildError(f"cannot resolve repository HEAD ref: {ref}")

    return _validated_sha(head, "detached HEAD")


# ---------------------------------------------------------------------------
# Manifest validation
# ---------------------------------------------------------------------------

def _is_allowlisted(path: str) -> bool:
    if path in FIXED_ALLOWED:
        return True
    return any(path.startswith(prefix + "/") for prefix in ALLOWED_DIR_PREFIXES)


def load_and_validate_manifest(root: Path) -> dict:
    try:
        manifest = json.loads((root / MANIFEST_RELPATH).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise KitBuildError(f"cannot read manifest: {exc}") from exc
    if not isinstance(manifest, dict):
        raise KitBuildError("manifest must be a JSON object")

    unknown = set(manifest) - REQUIRED_TOP_LEVEL
    if unknown:
        raise KitBuildError(f"manifest contains unknown top-level members: {sorted(unknown)}")
    missing = REQUIRED_TOP_LEVEL - set(manifest)
    if missing:
        raise KitBuildError(f"manifest missing required members: {sorted(missing)}")

    if manifest["format_version"] != 1:
        raise KitBuildError("manifest format_version must be 1")
    if manifest["faw_spec_version"] != "0.2":
        raise KitBuildError("manifest faw_spec_version must be 0.2")
    if manifest["source_repository"] != SOURCE_REPOSITORY:
        raise KitBuildError(
            f"manifest source_repository must be {SOURCE_REPOSITORY!r}"
        )
    reference_commit = manifest["reference_material_commit"]
    if not _COMMIT_RE.fullmatch(reference_commit):
        raise KitBuildError(
            "manifest reference_material_commit must be 40 lowercase hex characters"
        )
    if manifest["generated_archive_name"] != ARCHIVE_NAME:
        raise KitBuildError(
            f"manifest generated_archive_name must be exactly {ARCHIVE_NAME!r}"
        )

    forbidden = manifest["forbidden_prefixes"]
    if not isinstance(forbidden, list) or not forbidden:
        raise KitBuildError("manifest forbidden_prefixes must be a non-empty list")
    for prefix in forbidden:
        if not isinstance(prefix, str) or not prefix:
            raise KitBuildError("forbidden prefix must be a non-empty string")
        if prefix != prefix.strip():
            raise KitBuildError(f"forbidden prefix not normalized: {prefix!r}")
        if prefix.startswith(("/", "./")) or "\\" in prefix or ".." in Path(prefix).parts:
            raise KitBuildError(f"forbidden prefix not normalized: {prefix!r}")
    if len(set(forbidden)) != len(forbidden):
        raise KitBuildError("forbidden_prefixes must be unique")

    files = manifest["files"]
    if not isinstance(files, list) or not files:
        raise KitBuildError("manifest files must be a non-empty list")
    paths: list[str] = []
    for entry in files:
        if not isinstance(entry, dict):
            raise KitBuildError(f"manifest file entry must be an object: {entry!r}")
        if set(entry) != ENTRY_KEYS:
            raise KitBuildError(
                f"manifest file entry keys must be exactly {sorted(ENTRY_KEYS)}: {entry!r}"
            )
        for field in ("path", "sha256", "classification", "reason"):
            if not isinstance(entry[field], str) or not entry[field]:
                raise KitBuildError(f"manifest entry field {field!r} invalid: {entry!r}")
        if not isinstance(entry["size_bytes"], int) or entry["size_bytes"] < 0:
            raise KitBuildError(f"manifest entry size_bytes invalid: {entry!r}")
        if not _SHA256_RE.fullmatch(entry["sha256"]):
            raise KitBuildError(f"manifest entry sha256 invalid: {entry!r}")
        if entry["classification"] not in ALLOWED_CLASSIFICATIONS:
            raise KitBuildError(
                f"manifest entry classification not in documented set: {entry!r}"
            )
        paths.append(entry["path"])

    if paths != sorted(paths):
        raise KitBuildError("manifest paths are not sorted")
    if len(paths) != len(set(paths)):
        raise KitBuildError("manifest contains duplicate paths")
    for p in paths:
        if p.startswith("/") or p.startswith("./") or "\\" in p:
            raise KitBuildError(f"non-normalized path: {p}")
        parts = Path(p).parts
        if ".." in parts or "." in parts:
            raise KitBuildError(f"path traversal in manifest: {p}")
        if not _is_allowlisted(p):
            raise KitBuildError(f"path outside allowlist: {p}")
        for prefix in forbidden:
            if p.startswith(prefix):
                raise KitBuildError(f"path matches forbidden prefix {prefix!r}: {p}")
        if p.endswith((".py", ".pyc", ".pyo")):
            raise KitBuildError(f"python filename in manifest: {p}")
    return manifest


def check_allowlist_complete(root: Path, manifest: dict) -> None:
    """Every required fixed file must be listed; every existing file under
    conformance/v0.2/, schemas/ and vectors/ must be listed (including the
    conformance package's own manifest.json). Nothing may be silently
    extra."""
    listed = {e["path"] for e in manifest["files"]}
    for fixed in sorted(FIXED_ALLOWED - {MANIFEST_RELPATH}):
        if fixed not in listed:
            raise KitBuildError(f"required fixed file missing from manifest: {fixed}")
    for rel_root in COMPLETENESS_ROOTS:
        for f in sorted((root / rel_root).rglob("*")):
            if f.is_file():
                rel = str(f.relative_to(root))
                if rel not in listed:
                    raise KitBuildError(f"allowlisted file missing from manifest: {rel}")


def check_key_hygiene(path: str, content: bytes) -> None:
    """Builder-level TEST-ONLY key hygiene.

    Private-key-bearing content is rejected everywhere except the two exact
    sanctioned TEST-ONLY fixture files; the check runs during verified-entry
    validation, independent of any test scanning the final archive.
    """
    if path in TEST_ONLY_KEY_PATHS:
        return
    for marker in PRIVATE_KEY_MARKERS:
        if marker in content:
            raise KitBuildError(f"private key material in non-sanctioned file: {path}")


def verify_entries(root: Path, manifest: dict) -> list[dict]:
    """Byte-exact size and SHA-256 verification; returns the verified list."""
    verified = []
    for entry in manifest["files"]:
        p = root / entry["path"]
        try:
            st = p.lstat()
        except FileNotFoundError:
            raise KitBuildError(
                f"listed file missing from repository: {entry['path']}"
            ) from None
        if stat.S_ISLNK(st.st_mode):
            raise KitBuildError(f"allowlisted file is a symlink: {entry['path']}")
        if not stat.S_ISREG(st.st_mode):
            raise KitBuildError(f"allowlisted file is not a regular file: {entry['path']}")
        content = p.read_bytes()
        check_key_hygiene(entry["path"], content)
        if len(content) != entry["size_bytes"]:
            raise KitBuildError(
                f"size mismatch for {entry['path']}: manifest {entry['size_bytes']} "
                f"got {len(content)}"
            )
        digest = hashlib.sha256(content).hexdigest()
        if digest != entry["sha256"]:
            raise KitBuildError(f"SHA-256 mismatch for {entry['path']}")
        verified.append({"path": entry["path"], "bytes": content})
    return verified


# ---------------------------------------------------------------------------
# Deterministic archive
# ---------------------------------------------------------------------------

def build_archive(root: Path, verified: list[dict], manifest: dict) -> bytes:
    """Build the deterministic tar.gz and return its bytes.

    Only the verified listed files plus the manifest itself enter the
    archive; nothing else is read from the repository.
    """
    members = list(verified)
    manifest_bytes = (root / MANIFEST_RELPATH).read_bytes()
    members.append({"path": MANIFEST_RELPATH, "bytes": manifest_bytes})
    members.sort(key=lambda m: m["path"])

    tar_buf = io.BytesIO()
    with tarfile.open(fileobj=tar_buf, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for member in members:
            info = tarfile.TarInfo(member["path"])
            info.size = len(member["bytes"])
            info.mode = 0o644
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mtime = 0
            info.type = tarfile.REGTYPE
            tar.addfile(info, io.BytesIO(member["bytes"]))

    gz_buf = io.BytesIO()
    with gzip.GzipFile(fileobj=gz_buf, mode="wb", mtime=0) as gz:
        gz.write(tar_buf.getvalue())
    return gz_buf.getvalue()


def verify_archive_members(archive_bytes: bytes, manifest: dict) -> int:
    """Re-read the archive; enforce forbidden prefixes and member safety.
    Returns the member file count."""
    names: list[str] = []
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                raise KitBuildError(f"non-regular archive member: {member.name}")
            names.append(member.name)
            for prefix in manifest["forbidden_prefixes"]:
                if member.name.startswith(prefix):
                    raise KitBuildError(
                        f"archive member matches forbidden prefix {prefix!r}: {member.name}"
                    )
            if member.name.endswith((".py", ".pyc", ".pyo")):
                raise KitBuildError(f"python filename in archive: {member.name}")
            if member.name.startswith("/") or ".." in Path(member.name).parts:
                raise KitBuildError(f"unsafe archive member name: {member.name}")
    if len(names) != len(set(names)):
        raise KitBuildError("archive contains duplicate member names")
    return len(names)


# ---------------------------------------------------------------------------
# Public build boundary
# ---------------------------------------------------------------------------

def build(output_dir: Path, *, root: Path = ROOT) -> dict:
    """Build the implementer kit into output_dir from the kit root.

    Every path is derived from the passed `root`, so isolated synthetic kit
    roots can be tested without touching the real repository.
    """
    manifest = load_and_validate_manifest(root)
    reference_material_commit = manifest["reference_material_commit"]
    build_head_sha = read_head_sha(root)
    check_allowlist_complete(root, manifest)
    verified = verify_entries(root, manifest)

    archive_bytes = build_archive(root, verified, manifest)
    file_count = verify_archive_members(archive_bytes, manifest)

    archive_name = manifest["generated_archive_name"]
    output_dir = Path(output_dir)
    archive_path = output_dir / archive_name
    if archive_path.parent.resolve() != output_dir.resolve():
        raise KitBuildError("archive output path would escape output_dir")
    output_dir.mkdir(parents=True, exist_ok=True)
    archive_path.write_bytes(archive_bytes)

    manifest_bytes = (root / MANIFEST_RELPATH).read_bytes()
    return {
        "reference_material_commit": reference_material_commit,
        "build_head_sha": build_head_sha,
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "archive_sha256": hashlib.sha256(archive_bytes).hexdigest(),
        "archive_path": str(archive_path),
        "archive_size_bytes": len(archive_bytes),
        "file_count": file_count,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="directory that will receive the generated archive",
    )
    args = parser.parse_args(argv)
    try:
        result = build(args.output)
    except KitBuildError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"archive path:               {result['archive_path']}")
    print(f"reference_material_commit:  {result['reference_material_commit']}")
    print(f"build_head_sha:             {result['build_head_sha']}")
    print(f"manifest_sha256:            {result['manifest_sha256']}")
    print(f"archive_sha256:             {result['archive_sha256']}")
    print(f"archive size bytes:         {result['archive_size_bytes']}")
    print(f"file count:                 {result['file_count']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
