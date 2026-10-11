# Audit contract

Audit Caliper's test suite at e8a9302fa355653f95ff7c6d46b4d94ece9cae3c. The user asks for a deep analysis of the whole suite and whether tests can be cut because they protect little behavior now. This is a read-only investigation. Do not modify tests or production code. Write only your named report in this artifact directory.

Read every assigned test file and the relevant production implementations. Count AST test functions separately from parametrized pytest cases. Audit assertions and mock boundaries, not just names. Use Test Behavior, Not Implementation with judgment. Empty results, exception checks, transport arguments, and negative controls can be real contracts. Never recommend deleting them solely because they match a weak-assertion pattern.

For every proposed deletion, name the exact test, path and line, its current assertion, why it adds little signal, the surviving exact tests and the behavior they cover. A narrower test is redundant only if the surviving test exercises the same path, inputs and failure behavior. Distinguish safe delete, conditional consolidation, strengthen and keep. State whether evidence is inspected, executed, or inferred. Check historical regression intent when names or comments suggest it. Identify expensive tests that should stay.

Return ISSUES or PASS with the SHA, method, files read, a per-module disposition table covering every assigned file, concrete candidates and explicit gaps. Use absolute file pointers. Do not claim global coverage or mutation evidence from line coverage. If promising a mutation proof, propose it to the parent first so instrumentation does not conflict with baseline tests.
