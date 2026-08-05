#!/usr/bin/env python3
"""Build the reproducible FAW v0.2 implementer kit (v0.5 preparation).

The kit is the bounded, hash-verified input set for a clean-room second
implementation (see docs/V0_5_CLEAN_ROOM_PROTOCOL.md and ADR 0002).

Guarantees:
- no network operation;
- no subprocess; pure Python standard library;
- every listed file verified by exact byte size and SHA-256 against
  interop/v0.2/INPUT_MANIFEST.json;
- missing, extra, changed, or symlinked allowlisted files are rejected;
- deterministic tar.gz: sorted members, POSIX separators, uid/gid zero,
  empty owner/group names, fixed permissions, mtime zero, gzip mtime zero;
- forbidden prefixes and Python filenames are rejected both from the
  manifest paths and from the produced archive members.

The manifest cannot hash itself recursively; the manifest's own digest and
the archive digest are defined externally by this build's printed output.

The repository-state check requires a clean worktree. Allowlisted content
must be byte-identical to the manifest (which pins the recorded source
commit). The current HEAD SHA is printed; when HEAD differs from the
recorded source commit the build still succeeds only because the checked-out
allowlisted bytes are verified identical to the pinned ones.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import stat
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_RELPATH = "interop/v0.2/INPUT_MANIFEST.json"

# Fixed allowlisted files (protocol §2). Directories schemas/** and vectors/**
# are matched by listing. LICENSE is reserved in the protocol allowlist but
# the repository ships none at the recorded source commit (documented
# exception); the manifest does not list it.
FIXED_ALLOWED = {
    "SPEC.md",
    "docs/federated-agent-web-build-spec-v0.2.md",
    "SECURITY.md",
    "docs/V0_5_IMPLEMENTER_BRIEF.md",
    MANIFEST_RELPATH,
}
ALLOWED_DIRS = ("schemas", "vectors")

# Always-ignored platform metadata that is not repository content.
_ALWAYS_IGNORED = {".DS_Store"}


class KitBuildError(Exception):
    """Raised for any precondition or verification failure."""


def _blob_sha(content: bytes) -> str:
    """Git blob object SHA-1 (what the index stores), for a regular file."""
    return hashlib.sha1(b"blob %d\x00" % len(content) + content).hexdigest()


# ---------------------------------------------------------------------------
# Repository state (no subprocess; reads .git directly)
# ---------------------------------------------------------------------------

def _read_git_head(root: Path) -> str | None:
    git_dir = root / ".git"
    if git_dir.is_file():  # worktree indirection: "gitdir: <path>"
        try:
            target = git_dir.read_text().strip()
            if target.startswith("gitdir:"):
                git_dir = Path(target.split(":", 1)[1].strip())
                if not git_dir.is_absolute():
                    git_dir = root / git_dir
        except OSError:
            return None
    head_file = git_dir / "HEAD"
    try:
        head = head_file.read_text().strip()
    except OSError:
        return None
    if head.startswith("ref:"):
        ref = head.split(":", 1)[1].strip()
        loose = git_dir / ref
        try:
            return loose.read_text().strip()
        except OSError:
            pass
        packed = git_dir / "packed-refs"
        try:
            for line in packed.read_text().splitlines():
                if line.startswith("#") or line.startswith("^"):
                    continue
                sha, name = line.split(" ", 1)
                if name.strip() == ref:
                    return sha
        except OSError:
            pass
        return None
    return head


def _parse_index(root: Path) -> list[tuple[str, int, str]]:
    """Parse .git/index (versions 2/3/4) into (path, mode, blob_sha) tuples.

    Only stage-0 entries are returned; entries at other stages mean an
    unresolved merge and are reported as a dirty state by the caller.
    """
    index = root / ".git" / "index"
    try:
        data = index.read_bytes()
    except OSError:
        return []
    if data[:4] != b"DIRC":
        raise KitBuildError("unrecognized git index header")
    version = int.from_bytes(data[4:8], "big")
    if version not in (2, 3, 4):
        raise KitBuildError(f"unsupported git index version {version}")
    count = int.from_bytes(data[8:12], "big")
    pos = 12
    entries: list[tuple[str, str, int]] = []  # (path, sha, mode)
    prev_path = ""
    for _ in range(count):
        if pos + 62 > len(data):
            raise KitBuildError("truncated git index")
        mode = int.from_bytes(data[pos + 24: pos + 28], "big")
        sha = data[pos + 40: pos + 60].hex()
        flags = int.from_bytes(data[pos + 60: pos + 62], "big")
        if version == 4:
            # prefix-compressed path: varint prefix length, NUL-terminated suffix
            cur = pos + 62
            prefix_len = 0
            shift = 0
            while True:
                b = data[cur]
                cur += 1
                prefix_len |= (b & 0x7F) << shift
                shift += 7
                if not (b & 0x80):
                    break
            end = data.index(b"\x00", cur)
            path = prev_path[:prefix_len] + data[cur:end].decode("utf-8", "replace")
            pos = end + 1  # index v4 entries carry no padding
        else:
            end = data.index(b"\x00", pos + 62)
            path = data[pos + 62: end].decode("utf-8", "replace")
            pos = end + 1
            pos += (8 - ((pos - 12) % 8)) % 8
        if (flags & 0x4000) and version >= 3:
            pos += 2  # extended flags
        prev_path = path
        stage = (flags >> 12) & 0x3
        if stage == 0:
            entries.append((path, sha, mode))
    return entries


def _load_gitignore(root: Path) -> list[str]:
    try:
        return (root / ".gitignore").read_text().splitlines()
    except OSError:
        return []


def _is_ignored(relpath: str, is_dir: bool, patterns: list[str]) -> bool:
    """Apply a compact gitignore subset (last match wins, '!' negation).

    Supports: blank/comment lines, trailing '/' dir-only patterns, leading
    '/' anchoring, no-slash patterns matching any depth, fnmatch globs on
    basename or full relative path (no-slash patterns never cross '/'),
    and '!' negation. This covers the patterns used by this repository.
    """
    ignored = False
    parts = relpath.split("/")
    for raw in patterns:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negate = line.startswith("!")
        if negate:
            line = line[1:].lstrip()
        dir_only = line.endswith("/")
        if dir_only:
            line = line.rstrip("/")
        anchored = line.startswith("/")
        if anchored:
            line = line.lstrip("/")
        matched = False
        if "/" in line:
            if not anchored and not line.startswith("**/"):
                # slash pattern without anchor matches from the ignore-file dir
                pass
            matched = re.match(_glob_to_regex(line), relpath) is not None
        else:
            if dir_only:
                matched = any(re.match(_glob_to_regex(line), p) is not None for p in parts)
            else:
                matched = re.match(_glob_to_regex(line), parts[-1]) is not None
        if matched:
            ignored = not negate
    return ignored


def _glob_to_regex(pattern: str) -> str:
    parts = []
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "*":
            if i + 1 < len(pattern) and pattern[i + 1] == "*":
                parts.append(".*")
                i += 2
                if i < len(pattern) and pattern[i] == "/":
                    i += 1
                continue
            parts.append("[^/]*")
        elif c == "?":
            parts.append("[^/]")
        elif c in "[]":
            j = i + 1
            if j < len(pattern) and pattern[j] in "!^":
                j += 1
            if j < len(pattern) and pattern[j] == "]":
                j += 1
            while j < len(pattern) and pattern[j] != "]":
                j += 1
            if j < len(pattern):
                cls = pattern[i + 1:j]
                if cls.startswith("!"):
                    cls = "^" + cls[1:]
                parts.append("[" + cls + "]")
                i = j
            else:
                parts.append(re.escape(c))
        else:
            parts.append(re.escape(c))
        i += 1
    return "^" + "".join(parts) + "$"


def _scan_untracked(root: Path, tracked: set[str], patterns: list[str]) -> list[str]:
    untracked: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        rel_dir = os.path.relpath(dirpath, root)
        if rel_dir == ".":
            rel_dir = ""
        for dname in list(dirnames):
            rel = f"{rel_dir}/{dname}" if rel_dir else dname
            if rel == ".git":
                dirnames.remove(dname)
                continue
            if dname in _ALWAYS_IGNORED or _is_ignored(rel, True, patterns):
                dirnames.remove(dname)
        for fname in filenames:
            rel = f"{rel_dir}/{fname}" if rel_dir else fname
            if fname in _ALWAYS_IGNORED or _is_ignored(rel, False, patterns):
                continue
            if rel not in tracked:
                untracked.append(rel)
    return untracked


def check_repository_state(root: Path, manifest: dict) -> str:
    """Return the current HEAD SHA after asserting a clean worktree."""
    head = _read_git_head(root)
    if head is None:
        raise KitBuildError("could not determine repository HEAD")

    entries = _parse_index(root)
    tracked = {p for p, _sha, _mode in entries}
    for path, sha, mode in entries:
        if mode & 0o170000 not in (0o100000, 0o120000):
            raise KitBuildError(f"tracked file has unsupported mode: {path}")
        f = root / path
        if not f.exists():
            raise KitBuildError(f"tracked file missing from worktree: {path}")
        if f.is_symlink():
            raise KitBuildError(f"tracked file is a symlink: {path}")
        content = f.read_bytes()
        if _blob_sha(content) != sha:
            raise KitBuildError(f"tracked file modified in worktree: {path}")

    untracked = _scan_untracked(root, tracked, _load_gitignore(root))
    if untracked:
        raise KitBuildError(
            "worktree not clean; untracked files: " + ", ".join(sorted(untracked)[:10])
        )
    return head


# ---------------------------------------------------------------------------
# Manifest validation
# ---------------------------------------------------------------------------

def _is_allowlisted(path: str) -> bool:
    if path in FIXED_ALLOWED:
        return True
    return any(path.startswith(prefix + "/") for prefix in ALLOWED_DIRS)


def load_and_validate_manifest(root: Path) -> dict:
    try:
        manifest = json.loads((root / MANIFEST_RELPATH).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise KitBuildError(f"cannot read manifest: {exc}") from exc

    if manifest.get("format_version") != 1:
        raise KitBuildError("manifest format_version must be 1")
    if manifest.get("faw_spec_version") != "0.2":
        raise KitBuildError("manifest faw_spec_version must be 0.2")
    source_commit = manifest.get("source_commit")
    if not isinstance(source_commit, str) or len(source_commit) != 40:
        raise KitBuildError("manifest source_commit must be a 40-char SHA")
    if not isinstance(manifest.get("forbidden_prefixes"), list):
        raise KitBuildError("manifest forbidden_prefixes must be a list")
    if not isinstance(manifest.get("files"), list):
        raise KitBuildError("manifest files must be a list")

    paths: list[str] = []
    for entry in manifest["files"]:
        for field in ("path", "sha256", "classification", "reason"):
            if not isinstance(entry.get(field), str) or not entry[field]:
                raise KitBuildError(f"manifest entry missing field {field!r}: {entry!r}")
        size_bytes = entry.get("size_bytes")
        if not isinstance(size_bytes, int) or size_bytes < 0:
            raise KitBuildError(f"manifest entry size_bytes invalid: {entry!r}")
        if not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]):
            raise KitBuildError(f"manifest entry sha256 invalid: {entry!r}")
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
        for prefix in manifest["forbidden_prefixes"]:
            if p.startswith(prefix):
                raise KitBuildError(f"path matches forbidden prefix {prefix!r}: {p}")
        if p.endswith((".py", ".pyc", ".pyo")):
            raise KitBuildError(f"python filename in manifest: {p}")
    return manifest


def check_allowlist_complete(root: Path, manifest: dict) -> None:
    """Every allowlisted file that exists must be accounted for (manifest or
    the manifest itself); nothing may be silently extra."""
    listed = {e["path"] for e in manifest["files"]} | {MANIFEST_RELPATH}
    expected = set(FIXED_ALLOWED)
    for prefix in ALLOWED_DIRS:
        for f in sorted((root / prefix).rglob("*")):
            if f.is_file():
                expected.add(str(f.relative_to(root)))
    for p in sorted(expected):
        if p in listed:
            continue
        if p == "LICENSE":
            continue  # documented exception: repository ships no LICENSE
        raise KitBuildError(f"allowlisted file missing from manifest: {p}")
    for p in listed:
        if not (root / p).exists():
            raise KitBuildError(f"listed file missing from repository: {p}")


def verify_entries(root: Path, manifest: dict) -> list[dict]:
    """Byte-exact size and SHA-256 verification; returns the verified list."""
    verified = []
    for entry in manifest["files"]:
        p = root / entry["path"]
        st = p.lstat()
        if stat.S_ISLNK(st.st_mode):
            raise KitBuildError(f"allowlisted file is a symlink: {entry['path']}")
        if not stat.S_ISREG(st.st_mode):
            raise KitBuildError(f"allowlisted file is not a regular file: {entry['path']}")
        content = p.read_bytes()
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
    """Build the deterministic tar.gz and return its bytes."""
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
    """Re-read the archive; enforce forbidden prefixes and safety. Returns
    the member file count."""
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


def build(output_dir: Path) -> dict:
    root = ROOT
    manifest = load_and_validate_manifest(root)
    source_commit = manifest["source_commit"]
    head = check_repository_state(root, manifest)
    check_allowlist_complete(root, manifest)
    verified = verify_entries(root, manifest)

    archive_bytes = build_archive(root, verified, manifest)
    file_count = verify_archive_members(archive_bytes, manifest)

    archive_name = manifest["generated_archive_name"]
    if not isinstance(archive_name, str) or not archive_name.endswith(".tar.gz"):
        raise KitBuildError("manifest generated_archive_name must end in .tar.gz")
    output_dir.mkdir(parents=True, exist_ok=True)
    archive_path = output_dir / archive_name
    archive_path.write_bytes(archive_bytes)

    manifest_digest = hashlib.sha256((root / MANIFEST_RELPATH).read_bytes()).hexdigest()
    return {
        "archive_path": str(archive_path),
        "archive_sha256": hashlib.sha256(archive_bytes).hexdigest(),
        "archive_size_bytes": len(archive_bytes),
        "file_count": file_count,
        "source_commit": source_commit,
        "head_sha": head,
        "manifest_sha256": manifest_digest,
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
    print(f"archive path:       {result['archive_path']}")
    print(f"archive SHA-256:    {result['archive_sha256']}")
    print(f"archive size bytes: {result['archive_size_bytes']}")
    print(f"file count:         {result['file_count']}")
    print(f"source commit:      {result['source_commit']}")
    print(f"HEAD:               {result['head_sha']}")
    print(f"manifest SHA-256:   {result['manifest_sha256']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
