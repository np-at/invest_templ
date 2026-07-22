---
name: investigate
description: Run one investigation cycle over a topic, building a traceable, falsifiable claim corpus. Use when the user wants AI agents to investigate a question or topic and accumulate verifiable findings with provenance, confidence, conflict handling, and retraction. Orchestrates investigator -> verifier -> reconciler -> curator -> citation.
---

# Investigate

Drive the orchestrator-worker loop that turns a topic into a self-revising claim corpus. This is the entry point for "investigate X."

## When to use
The user asks to investigate/research a topic and wants results as a **traceable corpus** (claims with provenance, confidence, conflicts, and retraction) rather than a one-shot answer.

## Steps

1. **Ensure a topic is active.**
   ```
   python scripts/kb.py topic         # shows active topic, or errors
   python scripts/kb.py init --topic <slug>   # if none / new investigation
   ```
   If this is still the seeded template (topic `demo`), first convert it:
   `python scripts/fresh_start.py --topic <slug> --yes`.

2. **Run the loop via the `lead` agent** (preferred), or perform its steps yourself:
   - Decompose the topic into independent, checkable sub-questions.
   - Spawn read-only `investigator` sub-agents **in parallel** (cap the fan-out). Each writes candidate-claim JSON and runs `kb.py ingest`.
   - Send high-stakes candidates to the `verifier` (draft-blind Chain-of-Verification).
   - Run the `reconciler`: `kb.py conflict detect` + a semantic pass; link every disagreement.
   - Run the `curator`: apply promotion thresholds, minimal revision, invalidation/retraction with cascade.
   - Run the `citation` agent on anything promoted.

3. **Checkpoint every cycle.**
   ```
   python scripts/kb.py lint          # fix ALL errors before finishing
   python scripts/kb.py review-queue  # hand these to a human
   python scripts/kb.py report        # scannable belief state
   ```
   Save the plan/status to `kb/NOTES.md` so a fresh agent can resume.

## Rules that make this trustworthy
- **Gathering ≠ deciding.** Investigators only propose (`hypothesized`); the curator promotes/retracts.
- **No silent resolution.** Every conflict becomes a `conflict link` + open thread; both sides are retained.
- **Confidence from agreement, not vibes** — never store a raw self-reported number as truth.
- **History is append-only.** Nothing is deleted; belief change is a new event. Use `kb.py query as-of` to see what was believed at any past point.

See `corpus-ops` for the full CLI + JSON contract and `belief-revision` for conflict/retraction procedure.
