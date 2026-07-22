# Investigative Knowledge Base — template

A reusable scaffold for having **AI agents investigate one or more topics and
build a traceable, falsifiable, self-revising corpus of claims** — with explicit
provenance, calibrated confidence, conflict reconciliation, and fact
invalidation (superseding earlier "facts" proven wrong).

Built from *The Investigative Knowledge Base Playbook* research report. Pure
Python **standard library** — no `pip install`, cross-platform.

## The idea

Every finding is a **first-class claim object**, not a sentence: a subject–
predicate–object assertion plus how it was derived (sources, evidence, agent
run), an explicit epistemic status, two separate certainty axes, an explicit
**rebuttal condition** (how it could be disproven), and a bi-temporal validity
window. Claims are stored as an **append-only event log** — nothing is ever
deleted; belief change is a new event — so you can always ask *"what did we
believe on date D, and why?"*

Read-only **investigator** agents gather evidence and propose candidate claims;
a **verifier** tries to falsify them; a **reconciler** surfaces every conflict;
a **curator** promotes, disputes, retracts, and supersedes against explicit
thresholds; a **citation** agent checks source integrity — all coordinated by a
**lead**. Gathering is kept strictly separate from deciding.

## Quickstart

```bash
# 1. See the worked example run end-to-end (this is also the smoke test):
python examples/seed_example.py

# 2. Explore what it built:
python scripts/kb.py report
python scripts/kb.py conflict detect
python scripts/kb.py query as-of 2019-01-01T00:00:00+00:00 --world   # world-time
python scripts/kb.py lint

# 3. When ready for a real investigation, wipe the seed and configure:
python scripts/fresh_start.py --topic my-investigation --yes
```

Then point Claude Code at the **`/investigate`** skill (or the `lead` agent) with
your topic.

## Layout

```
scripts/kb.py            CLI entry point           scripts/kb/       core modules (stdlib)
scripts/fresh_start.py   seed cleanup (single-use) templates/        claim JSON contract + event schema
.claude/agents/          6 role definitions        .claude/skills/   investigate · corpus-ops · belief-revision
kb/events/<topic>.jsonl  canonical append-only log kb/index.db       disposable SQLite projection (gitignored)
examples/seed_example.py worked example == smoke test
docs/methodology.md      design ↔ report mapping, and what is deliberately NOT built
```

## Core guarantees

- **Traceable** — every claim links to its sources, evidence, and the agent run that created it; `query downstream <source>` shows everything resting on a source.
- **Falsifiable** — a claim with no `rebuttal_conditions` is rejected at ingest; `lint` enforces it.
- **Variable certainty** — ordinal status × ICD-203 likelihood band × analytic confidence × a calibrated numeric score, with agreement-derived confidence preferred over self-report.
- **Conflict reconciliation** — structured detection in code + semantic detection by the reconciler; every disagreement becomes an explicit, retained conflict (no silent resolution).
- **Fact invalidation** — discredit a source or retract a claim and the dependency **cascade** re-flags everything downstream; nothing is deleted, and history stays queryable.

See `docs/methodology.md` for how each piece maps to the research report, and
`templates/event_schema.md` for the event contract.
