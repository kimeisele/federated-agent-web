# Roadmap: v0.2 → v1.0

**What 1.0 means here:** the wire format is frozen and we promise not to break implementations built against it. That promise is only credible once someone other than us has implemented it. So this roadmap is organized around *external evidence*, not feature count.

**Current state:** spec v0.2 frozen, reference core on `main`, CI green;
v0.3 hardening complete; the v0.4 transport-independence gate is complete
(evidence in `docs/NADI_LIVE_REHEARSAL.md`). v0.5 clean-room preparation
is complete: ADR 0002, the clean-room protocol
(`docs/V0_5_CLEAN_ROOM_PROTOCOL.md`), the implementer brief, and the hashed
implementer kit (`interop/v0.2/INPUT_MANIFEST.json`) define the boundary
for a second implementation. Cross-language semantics are pinned: ADR 0003
and the interoperability profile
(`docs/FAW_V0_2_INTEROPERABILITY_PROFILE.md`) define the verification
semantics, and the language-neutral conformance package
(`conformance/v0.2/**`) implements the N01–N15 / P01–P05 fixture matrix;
the reference verifier is aligned to the profile. The implementer kit has
been refreshed to include the profile and the complete conformance package
and is pinned to the new reference material; kit review is pending. No
second implementation exists yet, and the v0.5 gate is not complete.

## Decisions already taken

These are defaults, chosen so that no milestone stalls waiting for a judgment call. Each is reversible by pull request like anything else in this repository.

- **Order:** v0.5 (second implementation) precedes v0.6 (external pilot), so the pilot does not trip over ambiguities that a second implementer would have caught first. **Override rule:** if a willing external operator appears earlier, take them immediately regardless of where the roadmap stands. A pilot in hand beats any ordering.
- **1.0 criteria stay strict.** All four gates, no exceptions. Rationale: a self-declared 1.0 without external implementers is a version number rather than a standard, and the audience this project needs will read it that way. If no second implementer is ever found, the honest outcome is a long-lived 0.x, not a promoted 1.0.
- **No dates.** For a solo maintainer working through agents, scheduled dates are fiction and generate guilt rather than progress. Milestones close on evidence. If external pressure helps, apply it to pilot recruitment, never to code.
- **Nadi adapter stays in v0.4, decided at v0.4.** It exists already and proves the transport-independence claim cheaply. If, when v0.4 begins, nobody needs GitHub transport, substitute an HTTP adapter and prove the same claim. Do not decide this now.

---

## How work happens

One rule, because it is the thing that decides whether this project survives a solo maintainer working through AI agents:

> **Every unit of work is a GitHub issue with written acceptance criteria, implemented in one PR, merged by a human after CI passes.**

Not a chat message. Not a copy-pasted prompt. The issue is the durable delegation; the chat is disposable runtime. This is the project's own contract applied to its own development — and practically, it is what lets any agent (or any future collaborator) pick up work without you re-explaining the project.

Supporting habits:

- **One slice per PR.** Never mix hardening, packaging, and roadmap work in the same PR. The current sequencing (PR #1 → packaging hygiene, nothing else) is correct; keep it.
- **Decisions go in `docs/adr/`.** If a decision is not written down, agents will relitigate it every session, and you will pay for that in budget and in drift.
- **Agents propose, humans merge.** No unattended writes to `main`, no agent-opened PRs against foreign repositories. This is Section VI.5 of the manifesto; violating it in our own repo would be the loudest possible signal that the principles are decorative.
- **Milestones close on evidence, not on feeling.** Each gate below has a check a non-technical operator can verify personally.

---

## Milestones

### v0.3 — Hygiene and hardening
*No protocol change. Nothing in this milestone may alter signed bytes or golden vectors.*

- packaging: `pyproject.toml` as the single source of truth, test dependencies separated, misleading `requirements.txt` removed
- error taxonomy: every rejection returns a distinct, documented reason code (needed later so an external implementer can tell *why* their document failed)
- coverage on the core paths: canonicalization, verification order, replay, pending store
- `README.md` that a stranger can follow to a green `faw demo` in under ten minutes
- MUST → test traceability table in the implementation report

**Gate:** a fresh clone on a clean machine reaches a passing `faw demo` using only the README. If you have to explain a step verbally, the README is not done.

### v0.4 — Transport independence, proven
*The spec claims transport is swappable. Right now that is an assertion.*

- implement the deferred Nadi/GitHub adapter behind the existing `Transport` interface
- run the identical conformance suite against both loopback and Nadi transports
- fix the two known defects during extraction, not after: inbound documents must pass the core verification gate before dispatch, and a partial multi-target push must never clear undelivered messages

**Gate:** the same test suite passes unchanged against two transports. If adapting the suite was necessary, the abstraction leaked and the spec needs an erratum.

### v0.5 — Second implementation
*The real test of a specification is whether someone can implement it from the text alone.*

- a **verifier only** (not a full node) in a second language — TypeScript or Go
- written **from the spec document**, not ported from the Python source
- consumes the published golden vectors and reproduces canonical bytes, digests, and signature verification
- every ambiguity found becomes a spec erratum issue — expect several, that is the point

**Gate:** the second implementation verifies the reference implementation's documents, and vice versa, with no shared code. This is the first moment the project is a protocol rather than a program.

### v0.6 — External pilot
*This is §17's milestone and the hardest one. It is a social problem, not a technical one — no agent can do it for you.*

- recruit one operator you do not control who is willing to run a node
- they publish their own manifest, under their own keys, in their own repository
- they issue one small real bounded delegation to the reference node, or accept one
- the reference implementation verifies their signed receipt, and the evidence is in Git

**Gate:** a receipt signed by a key you have never held, verifying against a manifest chain you do not control.

**Start recruiting during v0.3.** Do not wait until the code is polished — finding a willing operator takes longer than writing the adapter, and their first questions will tell you what to fix.

### v0.7–v0.9 — Feedback and breaking changes
*Breaking changes are permitted here and nowhere after.*

- fold in everything the second implementation and the pilot exposed
- spec errata, field renames, verification-order corrections — whatever the contact with reality demands
- resist scope growth: registry, governance, reputation, payments, and hosting remain out of scope

**Gate:** two consecutive minor versions with no BLOCKER-class finding from an independent review.

### v1.0 — Freeze
*Only administrative work remains. If a technical surprise appears here, it belongs in 0.9.*

- compatibility policy: what may change in a minor version, what requires 2.0
- security disclosure policy and a monitored contact address
- deprecation policy for `spec_version`
- final independent review of the implementation, not just the spec

**Gate for declaring 1.0 — all four, no exceptions:**

1. two independent implementations interoperate
2. at least one node not controlled by the maintainer has completed a real delegation and returned a verified receipt
3. no open BLOCKER-class finding
4. a written compatibility promise, published

If any one is missing, it is 0.x. A self-declared 1.0 without external implementers is a version number, not a standard, and the people you most want to attract will read it exactly that way.

---

## What only the maintainer can do

Agents can write the code. They cannot do these, and this is where your time actually goes:

- recruiting the pilot operator (v0.6) — start now, in parallel
- deciding trust-anchor and pinning policy for the reference deployment
- approving every irreversible publication step
- cutting scope when something threatens to become its own project
- deciding when contact with reality means the spec was wrong

## Explicitly not on this roadmap

Registry service, governance framework, reputation scoring, payments, production A2A hosting, Agent City / Agent World integration, LLM routing, automated pull requests against foreign repositories. Each may be a good project. None is a prerequisite for 1.0, and each one added before the external pilot delays the only milestone that actually proves the thesis.
