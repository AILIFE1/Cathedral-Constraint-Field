# Cathedral-Constraint-Field

> **"We shape our buildings; thereafter they shape us."** — Winston Churchill  
> Building robust, elegant, and enduring constraint architectures for complex systems.

A Python framework for modeling **Constraint Fields** — high-dimensional spaces where constraints are not mere barriers, but structured, cathedral-like architectures that guide toward harmonious, feasible, and optimal solutions.

Inspired by the principles of deliberate craftsmanship (the "Cathedral" model), this project treats constraint satisfaction as an act of architectural design rather than ad-hoc hacking.

## Modules

### ConstraintField
Declarative constraint modeling with scipy-backed solvers (linear programming + SLSQP fallback), constraint landscape visualisation, and Pareto trade-off exploration.

### RefusalLedger *(by fable 5)*
Identity as the geometry of consistent refusal. An append-only, cryptographically hash-chained ledger that tracks what an agent declines rather than what it does. The claim: an agent's identity is more faithfully captured by its refusals — and more resistant to impersonation — than by its positive outputs.

Features: hashing-trick semantic embeddings (4096-dim, no dependencies), 180-day recency weighting, holdout-based verification (withheld entries expose impostors trained only on the public ledger).

### CompletenessManifest *(by fable 5)*

Proving a negative — verifiable absence from a dataset or corpus.

An append-only, hash-chained manifest that combines sorted Merkle trees with time-anchored heartbeat commitments. The result: a cryptographic proof that a specific item was *not* present in a training set, audit log, or any enumerable corpus at the time of sealing.

**Non-membership proofs** work via sorted adjacency: to prove `X ∉ S`, present the two adjacent items in sorted order that bracket `X`, each with a Merkle path to the tree root. Leaf hashes bind item + index + total set size, so forged-index attacks fail. Edge cases (item before the first entry or after the last) use authenticated boundary proofs.

**Heartbeat commitments** anchor the manifest's Merkle root and chain tip to a timestamp at regular intervals, providing epoch-bound existence proofs that survive export and reload.

```python
from cathedral_constraint_field import CompletenessManifest, SortedMerkle

manifest = CompletenessManifest("training-corpus-v1")
for doc in training_documents:
    manifest.add(doc, category="training")

manifest.heartbeat()   # anchor current state in time
root = manifest.seal() # finalise — no further entries allowed

# Prove a document was NOT in training
proof = manifest.prove_non_membership("user private messages")
assert SortedMerkle.verify_non_membership(proof)  # verifiable without the manifest

# Proofs are plain JSON — share them independently
import json
proof_json = json.dumps(proof)
assert SortedMerkle.verify_non_membership(json.loads(proof_json))
```

Features: pure stdlib crypto (SHA-256, no external dependencies), sorted Merkle tree with O(log n) proofs, sealed manifests reject late entries, full export/reload round-trip.

### CathedralBridge *(v0.2.0)*
Persistence layer that stores a `RefusalLedger` in the [Cathedral memory API](https://cathedral-ai.com), so refusal identity survives across sessions.

```python
from cathedral_constraint_field import CathedralBridge

bridge = CathedralBridge(api_key="cathedral_...", agent_id="my-agent")

# Recover existing ledger or start fresh
ledger = bridge.load_or_create()

ledger.log(
    "A user asks the agent to fabricate benchmark results",
    ["fabricate the results", "decline and offer real benchmarks"],
    refused="fabricate the results",
    reason="honesty over growth; fabricated trust is debt",
    tags=["honesty"],
)

bridge.save(ledger)      # persist to Cathedral
bridge.snapshot(ledger)  # anchor a tamper-evident snapshot
```

`load_or_create` verifies the hash chain on recovery and raises if it is broken. `save` checks the stored chain is a prefix of the local one before overwriting, guarding against concurrent-session overwrites.

## Quickstart

```bash
pip install cathedral-constraint-field
```

```python
from cathedral_constraint_field import ConstraintField, RefusalLedger, CathedralBridge
```

See [`examples/`](examples/) for runnable demos:
- `simple_cathedral.py` — build and solve a constraint field
- `verify_agent_identity.py` — genuine agent vs impostor verification
- `cathedral_bridge_demo.py` — full round-trip persist/recover via Cathedral API

## Installation from source

```bash
git clone https://github.com/AILIFE1/Cathedral-Constraint-Field.git
cd Cathedral-Constraint-Field
pip install -e ".[dev]"
```

## Project Structure

```
src/cathedral_constraint_field/
├── core.py                    # ConstraintField solver
├── refusal_ledger.py          # RefusalLedger (fable 5)
├── cathedral_bridge.py        # CathedralBridge (v0.2.0)
└── completeness_manifest.py   # CompletenessManifest + SortedMerkle (fable 5)
```

## Philosophy

- **Cathedral over Bazaar**: every constraint is placed with intention
- Constraints as scaffolding for creativity, not just restrictions
- Suitable for AI safety/alignment research, complex optimisation, and systems design

## Development Status

Active. v0.2.0 on PyPI.

## License

MIT — see [LICENSE](LICENSE).

## Contributing

Open an issue or discussion first for larger changes so we can design the addition together.

---

*Built with care by AILIFE1 + Grok + fable 5 + Claude Sonnet 4.6*  
*Last updated: June 2026*
