# Reviewed v0.5 implementer-kit delivery record

This is the machine-addressable self-service delivery record for the exact
reviewed clean-room implementer kit (control issue
[kimeisele/federated-agent-web#39](https://github.com/kimeisele/federated-agent-web/issues/39)).
The archive is committed byte-identical; nothing was rebuilt or refreshed.

## Artifact

| Field | Value |
|---|---|
| Archive path (this repository) | `interop/v0.2/faw-v0.2-implementer-kit.tar.gz` |
| Stable machine-addressable URL (after merge to `main`) | `https://raw.githubusercontent.com/kimeisele/federated-agent-web/main/interop/v0.2/faw-v0.2-implementer-kit.tar.gz` |
| Archive SHA-256 | `7a03a38dc2da4687bf4c9e74e699c9bbf3a43a7950cf1b6df75190a76c227511` |
| Archive size | `54256` bytes |
| Archive members | `67` |
| Outer manifest path | `interop/v0.2/INPUT_MANIFEST.json` |
| Outer manifest SHA-256 | `aa41dc991b3858a1cc401ffcc992e1faeb5a964351b1f9340250c5cdfc272778` |
| Manifest-listed files | `66` |
| `reference_material_commit` | `2d3edbc49192fd5910389c17c1653d0913fa6434` |
| `kit_build_head_sha` | `582760aad05db9d487e5482563409048201635cd` (out-of-band provenance; not derivable from archive bytes) |

## How an external implementer obtains and verifies the kit (without cloning or browsing either implementation repository)

1. Download the archive from the stable URL above (or copy it from this
   repository path — the artifact is content-addressed by its hash).
2. Verify the archive bytes: `shasum -a 256` must equal the archive SHA-256
   above, and the size must be `54256` bytes.
3. Extract the archive and verify the outer manifest at
   `interop/v0.2/INPUT_MANIFEST.json`: its SHA-256 must equal the manifest
   SHA-256 above.
4. Verify `reference_material_commit` inside the outer manifest equals the
   value above.
5. Verify every manifest-listed file byte-for-byte by size and SHA-256
   (66 files), and confirm the archive has exactly 67 members (66 listed
   files + the outer manifest).
6. `kit_build_head_sha` is out-of-band provenance recorded above; it is not
   derivable from the archive bytes.

## Identity

The expected reviewed identity is the already-approved v0.5 kit identity
recorded in [kimeisele/federated-agent-web#37](https://github.com/kimeisele/federated-agent-web/issues/37)
(clean-room Go verifier control issue). Any mismatch is a rejection: do not
implement against a kit that fails these checks.
