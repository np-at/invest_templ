---
name: corpus-ops
description: Reference for reading and writing the investigation claim corpus via scripts/kb.py — the candidate-claim JSON contract, all CLI commands, and how content-addressed IDs and the append-only event log work. Use whenever adding claims, querying belief state, or operating the knowledge base tooling.
---

# Corpus operations

The corpus is an **append-only JSONL event log** (`kb/events/<topic>.jsonl`, canonical) plus a rebuildable **SQLite index** (`kb/index.db`, disposable). Everything runs through `python scripts/kb.py`. Pure stdlib — no install.

## Writing claims — the JSON-first path (primary)
1. Write a batch file following `templates/candidate_claim.json`. You provide the assertion triple, warrant, **rebuttal_conditions** (required — a claim you can't disprove is rejected), evidence, and confidence. You do **not** write ids — the script computes them from the assertion.
2. Ingest:
   ```
   python scripts/kb.py ingest mybatch.json --run <run-id>
   ```
3. If rejected, it prints **field-level errors** (`field: got X, allowed [...]`). Fix exactly those and re-run. Validation is atomic — the whole batch passes or none of it lands.

Dependencies: in `depends_on`, reference a parent by `@alias` (same batch), a `{s,p,o}` triple, or a known `clm_` id.

## Key commands
| Purpose | Command |
|---|---|
| Start / switch topic | `kb.py init --topic <slug>` · `kb.py topic` |
| Add claims | `kb.py ingest <file> --run <id>` |
| Register / discredit source | `kb.py source add <uri> --authority 0.9` · `kb.py source discredit <uri> --reason "…"` |
| Inspect a claim (full provenance) | `kb.py claim show <id>` |
| Promotion eligibility / promote | `kb.py claim promote <id>` · `kb.py claim promote <id> --to corroborated` |
| Recalibrate confidence | `kb.py claim recalibrate <id> --score 0.8 --method multi_agent_agreement` |
| Detect / link conflicts | `kb.py conflict detect` · `kb.py conflict link <a> <b> --type factual` |
| Retract / supersede / invalidate | `kb.py claim retract <id> --reason "…"` · `kb.py claim supersede <old> <new>` · `kb.py invalidate <id> --cause "…"` |
| Bi-temporal queries | `kb.py query as-of <ISO-date>` (system) · `... --world` (world time) |
| Source lineage | `kb.py query downstream <uri\|id>` |
| History of a claim | `kb.py query revisions <id>` |
| Guardrails & status | `kb.py lint` · `kb.py review-queue` · `kb.py report` |
| Rebuild index from log | `kb.py reindex` |

## Two things that trip people up
- **Two confidence axes, kept separate**: `likelihood_band` (ICD-203: how probable the claim is) vs `analytic_confidence` (high/mod/low: how good the evidence is). Never combine them.
- **Two time axes, kept separate**: `t_valid`/`t_invalid` = when the fact was true *in the world*; system time (`t_created`/`t_expired`, script-set) = when we *learned/retired* the belief. Discredit/retract/supersede are system-time only — never write them into `t_valid`/`t_invalid`.

Full event reference: `templates/event_schema.md`. Design rationale: `docs/methodology.md`.
