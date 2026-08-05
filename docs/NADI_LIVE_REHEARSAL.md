# Nadi/GitHub controlled live rehearsal (v0.4)

Controlled cross-account rehearsal of the experimental Nadi/GitHub transport
adapter against the real GitHub Contents API, using two separately
authenticated accounts (`kimeisele`, `federation-operator`) and an
adopter-owned public relay repository.

This is controlled evidence for the v0.4 transport-independence claim. It is
not an independent second implementation, an external pilot, evidence of
production readiness, or the v0.5/v0.6 milestone.

## Source under test

- Source repository: `kimeisele/federated-agent-web`
- Exact source commit: `9bea391` (`v0.4: experimental Nadi/GitHub transport adapter (#20)`)
- Installation: `python -m pip install .` in two isolated role environments
  (issuer, executor), each a clean clone at `9bea391`
- Runtime classes exercised: `GhCliMailboxClient`, `GitHubNadiRelayBackend`,
  `NadiTransport`, plus the existing FAW verification core (`verify`),
  `PendingDelegationStore`, `ReplayStore`, and `CapabilityExecutor`
- Relay repository: `federation-operator/faw-nadi-live-relay` (public,
  rehearsal-only, dedicated to this rehearsal)

No runtime code, protocol, schema, canonicalization, signature scheme,
verification order, or `spec_version` was modified for this rehearsal.

## Two attempts

### Attempt A — blocked before relay publication by credential scope

The first live publish attempt (2026-08-04) was blocked by a credential
permission error before any live write occurred:

```text
gh: Resource not accessible by personal access token (HTTP 403)
send_ok: false
```

The then-available `kimeisele` credentials were scoped to kimeisele-owned
repositories and had no API write access to
`federation-operator/faw-nadi-live-relay`. Granting collaborator push access
and accepting the invitation does not extend a fine-grained PAT repository
scope.

Observed fail-closed behavior (preserved, sanitized):

- the failed send retained the complete staged record in the issuer outbox
  (`.msg` / `.meta` / `.ready` commit-marker set) — retained atomic outbox
  state, exactly as designed;
- the issuer pending record had been registered before the send attempt;
- the relay repository contained only the rehearsal `README.md`;
- no remote mailbox entry was created.

Attempt A identifiers (kept separate; not reused by Attempt B):

- task ID `c66d3bb5-feb1-4c18-88a3-8206fcb7cb0a`
- attempt ID `8830467c-b57c-47f3-a934-9e6a40963fbe`
- transport message ID `683b85e6-aed8-4f20-b22f-d9321be4fd52`

No adapter defect was exposed; the failure was a credential-permission
blocker. After the operator supplied a `public_repo`-scoped classic PAT,
the rehearsal was re-run as a clean attempt with new state, new identifiers,
and fresh transport message IDs.

### Attempt B — successful controlled cross-account rehearsal

Clean success-state directories were created for both roles; the blocked
attempt's state was never loaded. Role identities (and their private keys)
remained isolated per role and were reused as-is; no key was copied between
roles.

Identities and relay addressing:

- issuer FAW node ID `urn:faw:cc124e2a91a01552`, relay address `kim-live`
- executor FAW node ID `urn:faw:66a62f2bab6f92ec`, relay address
  `federation-operator-live`
- FAW node IDs and relay addresses are distinct, per ADR 0001

Identifiers (Attempt B):

- task ID `efe83b8a-6c12-4046-b193-8e2b82f5f4ae`
- attempt ID `715ddedf-2e51-44b6-b5ec-a069a330fc5f`
- delegation transport message ID `1fb66236-13cf-4f35-af5c-382a7c8b4872`
- receipt transport message ID `10f2383c-a217-46a6-afba-1aa03d295e58`

#### Sequence and results

1. **Build delegation.** One signed `hash_file` delegation over a
   non-sensitive deterministic fixture (32 bytes). Delegation digest
   `sha256:a1bdeaa43ab2b02795947838f2e217e44385cd5de9c9c1407e2efd27f7c6358a`;
   document bytes SHA-256 `sha256:38745f4a8115ed08c5f270167b9b059c2cdc9e9cfc7a278d6605fa9f63c02226` (1262 bytes).
2. **Register pending.** Issuer pending record registered as `outstanding`
   before the transport was used.
3. **Publish as `kimeisele`** (authenticated login confirmed immediately
   before the write). `send_ok: true`, transport message
   `1fb66236-13cf-4f35-af5c-382a7c8b4872`.
4. **Mailbox confirmed:** `nadi/kim-live_to_federation-operator-live.json`
   (blob SHA `f7fbd107d91c4252877f13d9a9f52b323e3dfaa1`).
5. **Fetch as `federation-operator`** (GH_TOKEN removed; keyring account
   confirmed before the write).
6. **Exact byte digest equality:** fetched delegation bytes SHA-256 equals
   the published bytes SHA-256 (`sha256:38745f4a…`); equality confirmed.
7. **Verify and admit through the FAW core:** `verify()` with expected kind
   delegation, pinned issuer manifest chain, `allowed_actions={hash_file}`.
   Result `ok`, `admitted`, replay state `pending`.
8. **Exactly one replay admission:** replay store contains exactly one record
   for `(issuer, attempt)`.
9. **Capability executed:** deterministic `hash_file` on the fixture;
   result artifact `result.json`, digest
   `sha256:c82fe3009ddaf495ce04b8d0295527f61cceb72b12aa74b3ae619b81129c70b9`
   (343 bytes), on-disk digest verified.
10. **Terminal receipt created and signed** (status `succeeded`); receipt
    bytes SHA-256 `sha256:a4388741657600faa6cb9b2ba46bf0e761c171cae03deb51439cc7f60205f13d` (1148 bytes).
11. **Delegation acknowledged:** durable tombstone
    `1fb66236-…4872.ack` written.
12. **Reread + suppression:** mailbox reread yields **0 envelopes**; the
    remote entry was not deleted.
13. **Publish receipt as `federation-operator`** (authenticated login
    confirmed before the write). `send_ok: true`, transport message
    `10f2383c-a217-46a6-afba-1aa03d295e58`.
14. **Mailbox confirmed:** `nadi/federation-operator-live_to_kim-live.json`
    (blob SHA `aba866e6ad0308c317257c02eb88c716e4a6baa0`).
15. **Fetch receipt as `kimeisele`.**
16. **Exact byte digest equality:** fetched receipt bytes SHA-256 equals the
    sent bytes SHA-256 (`sha256:a4388741…`); equality confirmed.
17. **Receipt verified through explicit public trust material** (pinned
    executor manifest chain; resolved key `sha256:8aaa62ac…`,
    head sequence 1).
18. **Pending transition:** matching pending record is now `terminal` with
    the terminal receipt attached.
19. **Receipt acknowledged:** durable tombstone
    `10f2383c-…295e58.ack` written.
20. **Reread + suppression:** mailbox reread yields **0 envelopes**; the
    remote entry was not deleted.

#### Relay evidence

Relay repository commits (newest first):

| Commit | Author | Message |
|---|---|---|
| `33218c87d83de23241cf8d4a3a373a27f52cfb0f` | federation-operator | faw.document relay append (1 message(s)) — receipt |
| `a800e1d569415fad93ee7ab7c1dd307cc921283a` | kimeisele | faw.document relay append (1 message(s)) — delegation |
| `3e6a8da7023948b3ee34080ce35c9ba5a0622567` | federation-operator | Controlled v0.4 Nadi live relay (rehearsal only) |

Mailbox paths: `nadi/kim-live_to_federation-operator-live.json`,
`nadi/federation-operator-live_to_kim-live.json`.

Relay hygiene scan (recursive tree, blob type): exactly `README.md` and the
two `nadi/*.json` mailbox files; no stray files, no credentials, no private
material.

#### Local durable state summary (Attempt B)

- Issuer transport: outbox empty (delivered messages removed), inbox empty,
  acknowledged `10f2383c-…295e58.ack` present, failed empty.
- Issuer pending: `terminal`, receipt status `succeeded`.
- Executor transport: outbox empty, inbox empty (acked),
  acknowledged `1fb66236-…4872.ack` present, failed empty.
- Executor replay: exactly one admission record, state `terminal`, terminal
  receipt attached (byte-identical to the transported receipt).

#### Timing

Round-trip driver wall time ≈ 8.5 s (publish 1.9 s; executor flow 4.0 s;
issuer receipt flow 2.5 s), excluding evidence capture API calls.

## Safety confirmations

- No token value, private key, or credential path appears in this document
  or in the relay repository.
- No complete Base64 document bodies are reproduced; only digests and IDs.
- No mailbox entry was deleted to simulate acknowledgement; suppression was
  proven by durable tombstones and empty rereads.
- Relay metadata never granted authority: the embedded signed FAW documents
  are the sole authority-bearing objects (ADR 0001).
- No runtime code, protocol, schema, or version change was made.

## Scope statement

This is controlled evidence for the v0.4 transport-boundary claim. It is not
independent adoption, not an external pilot, and does not satisfy the v0.6
gate.
