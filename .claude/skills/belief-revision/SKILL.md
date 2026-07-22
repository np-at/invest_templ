---
name: belief-revision
description: Procedures for conflict reconciliation and fact invalidation in the investigation corpus — detecting/classifying conflicts, retaining multiple perspectives, discrediting sources, retracting/superseding claims, and cascading re-evaluation to dependents. Use when claims disagree or an earlier finding is proven wrong.
---

# Belief revision: conflicts & invalidation

Conflicts and retractions are **normal operations, not errors**. The two failures to avoid: *silent resolution* (dropping a disagreement) and *orphaned cascade* (a discredited source leaving downstream claims still believed).

## When claims disagree
1. **Detect** — `python scripts/kb.py conflict detect` (structured), then a semantic pass by hand for paraphrase/entailment conflicts the exact-match misses.
2. **Classify the type before resolving** (this measurably improves handling): `complementary` (consolidate), `conflicting_opinions` (retain both, attributed), `outdated` (prefer newer, keep old with its window), `factual` (weight by authority/recency/specificity), `misinformation` (flag for human, don't average).
3. **Link — always.** `kb.py conflict link <a> <b> --type <type> --note "<why>"`. This opens a resolution thread and moves both to `disputed`. **Retain both sides** — never overwrite or delete. Collapse to one answer only when an attack has decisively succeeded.
4. **Resolve** only when evidence settles it: `kb.py conflict resolve <thread> resolved --note "<basis>"`, then have the curator retract/supersede the losing claim.

## When an earlier "fact" is proven wrong
Choose the mechanism by what actually happened:
- **A source is discredited** → `kb.py source discredit <uri> --reason "…"`. Every claim citing it, and their dependents, are flagged `needs_review` (a re-evaluation request — **not** automatic falsehood; a claim may stand on independent legs). Then clear the `review-queue`: re-affirm or retire each.
- **A stated rebuttal condition fires** → `kb.py claim rebuttal <id> --condition "…" --evidence "…"`. Records it and moves the claim to `needs_review` for the curator.
- **A claim is decisively falsified** → `kb.py claim retract <id> --reason "…"`. Cascades to dependents.
- **A better replacement exists** → ingest the new claim, then `kb.py claim supersede <old> <new>`.
- **The fact was true then, false now** (real-world change) → this is *world-time*: set `t_valid`/`t_invalid` on the claim, do **not** retract. "True 2015–2020" is not "never true."

## Cascade & recovery
- After any invalidation, run `kb.py review-queue` and resolve every entry. `kb.py lint` will flag any believed claim still resting on a discredited source or retired parent (orphaned cascade) as an **error** — fix before finishing.
- Discrediting is reversible: `kb.py source recredit <uri>`. Note this does **not** auto-restore dependent beliefs — a curator must re-affirm them, because a claim may have been flagged for several reasons.
- Nothing is ever deleted. `kb.py query revisions <id>` shows why belief changed; `kb.py query as-of <date>` shows what was believed at any past point.
