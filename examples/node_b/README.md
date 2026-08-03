# examples/node_b

Placeholder for a node B working directory — the receiving/executing node in
the two-node demo.

See `examples/node_a/README.md` for the relationship to `faw demo` and
`faw manifest init`. Node B verifies the delegation (audience = itself,
trust = locally pinned Node A manifest chain), admits the attempt at most
once into its replay store, executes the deterministic `hash_file`
capability, and signs the terminal receipt that Node A accepts against its
pending-delegation store.
