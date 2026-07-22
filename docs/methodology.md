# Methodology

How this template maps to *The Investigative Knowledge Base Playbook*, and —
importantly — **what is deliberately NOT built in code**. The research report is
a maximalist survey; per its own Caveats, its formal frameworks are best used as
*design scaffolding*, not as a literal logic engine over noisy LLM-extracted
claims. Every concept below sits in one of three buckets.

## Bucket 1 — enforced by code (`scripts/kb/`)

| Report concept | Where |
|---|---|
| Claim as a first-class structured object (nanopublication triad × Toulmin) | claim schema in `projection.py`; contract in `templates/candidate_claim.json` |
| Append-only, never-mutate, "what was believed when" | JSONL event log (`events.py`); `query as-of` |
| Bi-temporal validity (world vs system time) | `t_valid`/`t_invalid` (world) + `t_created`/`t_expired` (system); `query as-of` / `--world` |
| Provenance chain (source → run → evidence → claim) | `sources`, `agent_runs`, `evidence`, `edges` tables |
| Dual certainty: ordinal status + separate likelihood/analytic-confidence + numeric score | `claims` columns; `ingest` keeps likelihood and analytic-confidence as two fields |
| Epistemic-status vocabulary + legal transitions | `events.STATUSES` / `TRANSITIONS`; `claims.change_status` |
| Falsifiability by construction | `rebuttal_conditions` required at ingest; `lint` non-falsifiable check |
| Structured conflict detection + mandatory `conflictsWith` | `conflicts.py`; `lint` silent-resolution check |
| Source-authority field + discredit/recredit | `sources.authority`, `source_discredited`/`source_recredited` |
| Invalidation without deletion; supersession/retraction chains | `invalidation.py`; `claim_invalidated`/`_superseded`/`_retracted` |
| Dependency cascade ("if this source dies, what's affected?") | `invalidation.cascade` (reverse `depends_on`, visited-set); `query downstream` |
| Content-addressed IDs + dedup | `events.claim_id`/`source_id` |
| Guardrails against the report's failure modes | `lint.py`, `review_queue` |

## Bucket 2 — encoded in agent prompts / skills (`.claude/`)

These need judgment an LLM supplies, not a deterministic script:

- **Chain-of-Verification in a fresh, draft-blind context** — `verifier.md`.
- **Classify-conflict-type before resolving; multi-perspective retention** — `reconciler.md`, `belief-revision` skill.
- **Agreement-derived, calibrated confidence** (self-consistency / multi-agent agreement over self-report) — `investigator.md`, `verifier.md`.
- **Adversarial red-team on high-stakes claims** — `verifier.md`.
- **Semantic / paraphrase conflict detection** (the tier the script can't do) — `reconciler.md`.
- **Source-credibility judgment, promotion decisions, minimal (AGM-style) revision** — `curator.md`.
- **Orchestration, stopping criteria, checkpointing/compaction** — `lead.md`, `investigate` skill.

## Bucket 3 — documented rationale, intentionally NOT built

These justify the design and are the **upgrade path**; the stdlib template does
not implement them as engines:

- **AGM belief revision** (expansion/contraction/revision, epistemic entrenchment, Levi identity) — the *normative* theory behind "minimal, consistent change." We approximate it as a curator behavior (surrender the least-entrenched claim), not a logic engine.
- **TMS / ATMS** (Doyle 1979; de Kleer 1986) — justification-tracking and multi-context labeling. Our cascade is a lightweight stand-in: reverse-`depends_on` traversal that *re-flags for review* rather than automatically re-deriving IN/OUT labels. A full ATMS (multiple justification sets per node) is the upgrade.
- **Dung / ASPIC argumentation semantics** (grounded/preferred/stable extensions; undermine/rebut/undercut) — formal "which claims can be jointly held." We keep both sides as `disputed` claims linked by an edge; we do not compute extensions.
- **RDF-star / named graphs / PROV-O / nanopublications** — the standards-based serialization. Our event log + relational projection captures the same statement-level provenance and per-claim metadata; export to RDF-star is a possible adapter, not a dependency.
- **Graphiti / Zep bi-temporal knowledge graph, Neo4j** — the production substrate the report recommends. We replicate the *semantics* (invalidate-not-delete, four timestamps, as-of queries) in SQLite/JSONL; swapping in Graphiti is the scale path.
- **Truth-discovery (TruthFinder / CRH)** — unsupervised joint estimation of source reliability and truth. We use a static, human/agent-set `authority` field instead; CRH-style dynamic reweighting is future work.
- **Wald SPRT debate governor; embedding-based semantic dedup** — compute-optimal stopping and fuzzy matching. Out of scope for a zero-dependency template; noted for teams that add ML deps.

## A note on citations (carry this forward)

The source report explicitly flags that **several very recent arXiv IDs it cites
are anomalous / apparently unrefereed** (e.g. the "ConflictRAG" preprint
arXiv:2605.17301, and other 2025–2026-numbered IDs). Treat those as *concept*
sources only — the pipeline ideas are sound and mirrored by peer-reviewed work
(FaithfulRAG, RAG-with-Conflicting-Evidence, TruthfulRAG), but **do not cite
their specific metrics as established** without verifying the primary source.
The durable, well-established anchors are: nanopublications (Groth et al. 2010),
PROV-O (W3C), Toulmin's argument model, Dung 1995, AGM 1985, Doyle 1979 /
de Kleer 1986, ICD-203 (ODNI), Wikidata ranking, and Zep/Graphiti
(arXiv:2501.13956).
