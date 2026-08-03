# examples/node_a

Placeholder for a node A working directory.

In the offline demo (`faw demo`, `examples/run_demo.py`) both nodes are
ephemeral: identities, keys, manifests, replay state, and pending state live
in a temporary directory and are discarded at exit.

To create a persistent node directory, use:

```bash
faw manifest init --name "Node A" --capabilities hash_file --out node_a
```

which writes `node_a/.well-known/faw-node.json` (the FAW node manifest),
`node_a/node.json` (public identity state), and `node_a/keys/` (private key
material, mode 0600, gitignored).

The demo capability is `hash_file` (deterministic SHA-256 of an input file);
no LLM is used anywhere in the conformance path.
