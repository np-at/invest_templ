# Operating rules for agents in this repo

This is an **investigative knowledge base**. Your job is to help build a
traceable, falsifiable, self-revising corpus of claims — not to give one-shot
answers. Read this before acting.

## The corpus
- Canonical store: append-only JSONL event log at `kb/events/<topic>.jsonl`. **Never edit or delete log lines.** Belief change is always a new event.
- All operations go through `python scripts/kb.py` (pure stdlib). The SQLite index is disposable — rebuild with `kb.py reindex` if in doubt.
- The write path for claims is JSON-first: write a batch per `templates/candidate_claim.json`, then `kb.py ingest <file>`.

## Non-negotiable rules
1. **Gathering ≠ deciding.** Investigators only propose claims at `hypothesized`. Promotion, dispute, retraction, and supersession are the curator's decisions.
2. **Every claim needs a `rebuttal_conditions[]`** — how it could be disproven. Ingest rejects claims without one.
3. **No silent resolution.** When claims disagree, you must `conflict link` them and retain both sides. Never quietly pick one and drop the other.
4. **Confidence from agreement, not vibes.** Prefer `self_consistency` / `multi_agent_agreement` / `source_corroboration`; `self_report` alone is flagged as uncalibrated.
5. **Keep the two axes separate**: likelihood band (how probable) vs analytic confidence (how good the evidence); world time (`t_valid`/`t_invalid`) vs system time (script-set). Discredit/retract/supersede are system-time events — never write them into world-time fields.
6. **Cascade on invalidation.** After discrediting a source or retracting a claim, clear the `review-queue`: every `needs_review` claim must be re-affirmed or retired.
7. **End every cycle with `kb.py lint` reporting zero errors.**

## Roles & skills
- Skills: `/investigate` (run a cycle), `corpus-ops` (CLI + JSON contract), `belief-revision` (conflicts + invalidation).
- Agents: `lead`, `investigator`, `verifier`, `reconciler`, `curator`, `citation` (see `.claude/agents/`).

## Configuring a real investigation
If the active topic is still `demo`, this is the untouched template. Convert it:
`python scripts/fresh_start.py --topic <slug> --yes` (removes the seed and itself).
Then set this file's scope/authority policy for your domain, and extend
`FUNCTIONAL_PREDICATES` in `scripts/kb/events.py` with your domain's
single-valued relations (drives contradiction detection).

<!-- INVESTIGATION SCOPE: describe the topic, in-scope questions, source
     authority policy, and any domain rules here once you start a real one. -->
