"""`faw` command-line interface (§12)."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import canonical
from .crypto import b64url_encode, generate_keypair, kid_for
from .demo import run_demo
from .documents import (
    KIND_DELEGATION,
    KIND_MANIFEST,
    KIND_RECEIPT,
    content_digest_of,
    validate_document,
)
from .identity import NodeIdentity
from .pending import PendingDelegationStore
from .replay import ReplayStore
from .evidence import verify_evidence_bundle
from .runner import run_once as _runner_run_once
from .verify import PinnedManifestTrustContext, VerificationPolicy, verify

__all__ = ["main"]


def _load_document(path: Path) -> dict[str, Any]:
    return canonical.parse_strict(path.read_bytes())


def _load_trust(path: Path) -> PinnedManifestTrustContext:
    document = _load_document(path)
    kind = document.get("kind")
    if kind == KIND_MANIFEST:
        return PinnedManifestTrustContext.from_chain([document])
    raise ValueError(
        f"{path} is not a node manifest; cannot use as trust anchor (found {kind!r})"
    )


def _print_result(result: Any, label: str) -> int:
    print(f"{label}: {'OK' if result.ok else 'FAILED'}")
    if result.reason:
        print(f"  reason: {result.reason}")
    if result.step is not None:
        print(f"  step: {result.step}")
    if result.freshness:
        print(f"  freshness: {result.freshness}")
    if result.head_sequence is not None:
        print(f"  head_sequence: {result.head_sequence}")
        print(f"  head_digest: {result.head_digest}")
    if result.resolved_kid:
        print(f"  resolved_kid: {result.resolved_kid}")
    if result.delegation_digest:
        print(f"  content_digest: {result.delegation_digest}")
    if result.replay_state:
        print(f"  replay_state: {result.replay_state}")
    if result.deduplicated:
        print("  deduplicated: yes")
    if result.admitted:
        print("  admitted: yes")
    return 0 if result.ok else 1


def _cmd_keygen(args: argparse.Namespace) -> int:
    private_raw, public_raw = generate_keypair()
    kid = kid_for(public_raw)
    print(f"kid:          {kid}")
    print(f"public_key:   {b64url_encode(public_raw)}")
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(private_raw)
        out.chmod(0o600)
        print(f"private key:  {out} (mode 0600, TEST USE ONLY unless protected)")
    return 0


def _cmd_manifest_init(args: argparse.Namespace) -> int:
    identity = NodeIdentity.create(
        node_id=args.node_id,
        display_name=args.name,
        capabilities=args.capabilities.split(",") if args.capabilities else [],
        freshness_window_seconds=args.freshness_window,
    )
    out = Path(args.out)
    well_known = out / ".well-known"
    well_known.mkdir(parents=True, exist_ok=True)
    (well_known / "faw-node.json").write_text(
        json.dumps(identity.head_manifest, indent=2) + "\n"
    )
    identity.to_json(out)
    print(f"node_id:      {identity.node_id}")
    print(f"head_digest:  {identity.head_digest()}")
    print(f"manifest:     {well_known / 'faw-node.json'}")
    print(f"identity:     {out / 'node.json'} (private keys under {out / 'keys'}, mode 0600)")
    return 0


def _cmd_manifest_verify(args: argparse.Namespace) -> int:
    path = Path(args.file)
    document = _load_document(path)
    result = verify(
        path.read_bytes(),
        expected_kind=KIND_MANIFEST,
        local_node_id=None,
        trust_context=_load_trust(path),
        local_policy=VerificationPolicy(),
        now=datetime.now(timezone.utc),
    )
    return _print_result(result, f"manifest {path}")


def _cmd_delegation_verify(args: argparse.Namespace) -> int:
    path = Path(args.file)
    document = _load_document(path)
    policy = VerificationPolicy(
        can_enforce_tokens=args.enforce_tokens,
        can_enforce_cost=args.enforce_cost,
        allowed_actions=set(args.allowed_actions.split(",")) if args.allowed_actions else None,
    )
    result = verify(
        path.read_bytes(),
        expected_kind=KIND_DELEGATION,
        local_node_id=args.local_node_id,
        trust_context=_load_trust(args.trust),
        local_policy=policy,
        now=datetime.now(timezone.utc),
        replay_store=ReplayStore(Path(args.replay)) if args.replay else None,
    )
    return _print_result(result, f"delegation {path}")


def _cmd_receipt_verify(args: argparse.Namespace) -> int:
    path = Path(args.file)
    document = _load_document(path)
    pending = PendingDelegationStore(Path(args.pending)) if args.pending else None
    result = verify(
        path.read_bytes(),
        expected_kind=KIND_RECEIPT,
        local_node_id=None,
        trust_context=_load_trust(args.trust),
        local_policy=VerificationPolicy(),
        now=datetime.now(timezone.utc),
        pending_store=pending,
    )
    return _print_result(result, f"receipt {path}")


def _cmd_conformance(args: argparse.Namespace) -> int:
    target = args.node_path_or_url
    raw: bytes | None = None
    if target.startswith(("http://", "https://")):
        import urllib.request

        try:
            with urllib.request.urlopen(target, timeout=10) as response:  # noqa: S310 - explicit CLI use
                raw = response.read()
        except Exception as exc:  # noqa: BLE001
            print(f"conformance: FAILED: cannot fetch {target}: {exc}")
            return 1
    else:
        candidate = Path(target) / ".well-known" / "faw-node.json"
        if not candidate.is_file():
            print(f"conformance: FAILED: {candidate} not found")
            return 1
        raw = candidate.read_bytes()
    result = verify(
        raw,
        expected_kind=KIND_MANIFEST,
        local_node_id=None,
        trust_context=PinnedManifestTrustContext.from_chain([canonical.parse_strict(raw)]),
        local_policy=VerificationPolicy(),
        now=datetime.now(timezone.utc),
    )
    return _print_result(result, f"conformance {target}")



def _cmd_node_run_once(args: argparse.Namespace) -> int:
    """Run the one-shot node worker — process at most one envelope."""
    return _runner_run_once(
        identity_dir=Path(args.identity),
        trust_dir=Path(args.trust),
        transport_root=Path(args.transport_root),
        state_dir=Path(args.state_dir),
        work_dir=Path(args.work_dir),
        role=args.role,
    )


def _cmd_evidence_verify(args: argparse.Namespace) -> int:
    """Verify a committed evidence bundle (offline, public keys only)."""
    try:
        report = verify_evidence_bundle(Path(args.bundle_dir))
        print(report)
        return 0
    except Exception as exc:
        print(f"evidence: FAILED")
        print(f"reason: {exc}")
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="faw",
        description="Federated Agent Web — transport-agnostic delegation contract CLI (v0.2).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("keygen", help="generate an Ed25519 key pair and kid")
    p.add_argument("--out", help="write the private key (raw 32 bytes) to this file")
    p.set_defaults(func=_cmd_keygen)

    p = sub.add_parser("manifest", help="manifest operations")
    sub2 = p.add_subparsers(dest="subcommand", required=True)
    init = sub2.add_parser("init", help="generate a genesis manifest for a new node")
    init.add_argument("--node-id", help="stable node id (default: random urn:faw:...)")
    init.add_argument("--name", default="FAW node", help="display name")
    init.add_argument("--capabilities", default="", help="comma-separated capability names")
    init.add_argument("--freshness-window", type=int, default=3600, help="manifest freshness window (seconds)")
    init.add_argument("--out", default=".", help="output directory")
    init.set_defaults(func=_cmd_manifest_init)
    mv = sub2.add_parser("verify", help="verify a signed node manifest file")
    mv.add_argument("file")
    mv.set_defaults(func=_cmd_manifest_verify)

    p = sub.add_parser("delegation", help="delegation operations")
    sub2 = p.add_subparsers(dest="subcommand", required=True)
    dv = sub2.add_parser("verify", help="verify a signed delegation file")
    dv.add_argument("file")
    dv.add_argument("--local-node-id", required=True, help="this node's node_id (audience check)")
    dv.add_argument("--trust", required=True, help="trusted issuer manifest file (pin)")
    dv.add_argument("--replay", help="replay store directory (enables admission)")
    dv.add_argument("--allowed-actions", help="comma-separated actions this node may execute")
    dv.add_argument("--enforce-tokens", action="store_true", help="this node can measure token usage")
    dv.add_argument("--enforce-cost", action="store_true", help="this node can measure USD cost")
    dv.set_defaults(func=_cmd_delegation_verify)

    p = sub.add_parser("receipt", help="receipt operations")
    sub2 = p.add_subparsers(dest="subcommand", required=True)
    rv = sub2.add_parser("verify", help="verify a signed receipt file (issuer side)")
    rv.add_argument("file")
    rv.add_argument("--trust", required=True, help="executor manifest file (pin)")
    rv.add_argument("--pending", help="issuer pending-delegation store directory (required for binding)")
    rv.set_defaults(func=_cmd_receipt_verify)

    p = sub.add_parser("conformance", help="verify a node at a local path or URL")
    p.add_argument("node_path_or_url")
    p.set_defaults(func=_cmd_conformance)

    
    p = sub.add_parser("node", help="operational node commands")
    sub2 = p.add_subparsers(dest="subcommand", required=True)
    ro = sub2.add_parser("run-once", help="process at most one inbound envelope and exit")
    ro.add_argument("--identity", required=True, help="persisted node directory")
    ro.add_argument("--trust", required=True, help="trusted peer node directory")
    ro.add_argument("--transport-root", required=True, help="shared transport root directory")
    ro.add_argument("--state-dir", required=True, help="persistent state directory")
    ro.add_argument("--work-dir", required=True, help="scratch work directory")
    ro.add_argument("--role", required=True, choices=["executor", "issuer"], help="node role")
    ro.set_defaults(func=_cmd_node_run_once)

    p = sub.add_parser("evidence", help="offline evidence verification")
    sub2 = p.add_subparsers(dest="subcommand", required=True)
    ev = sub2.add_parser("verify", help="verify a committed evidence bundle")
    ev.add_argument("bundle_dir", help="path to evidence bundle directory")
    ev.set_defaults(func=_cmd_evidence_verify)

    p = sub.add_parser("demo", help="run the fully offline two-node demo (§12)")
    p.add_argument("--workdir", help="scratch directory (default: temp)")
    p.set_defaults(func=lambda args: run_demo(Path(args.workdir) if args.workdir else None))

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
