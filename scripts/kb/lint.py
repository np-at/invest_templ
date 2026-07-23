"""Guardrails: `lint` maps the report's 'Failure Modes to Engineer Against'
onto mechanical checks; `review_queue` surfaces the 'escalate to human'
triggers. These are the teeth behind robust operation.

lint returns {errors, warnings}. errors are hard failures (exit non-zero);
warnings are advisory (surfaced, but don't fail CI).
"""
from __future__ import annotations

import json

from . import conflicts as CF
from . import events as E
from . import projection as P

_BELIEVED = {"probable", "corroborated", "confirmed"}
_RETIRED = {"retracted", "superseded"}


def lint(topic: str | None = None) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    con = P.build_index(topic)
    claims = {r["claim_id"]: dict(r) for r in con.execute("SELECT * FROM claims")}

    # 1. Silent resolution — functional-predicate contradictions with no edge.
    for pair in CF.detect(topic):
        if pair["functional"] and not pair["already_linked"]:
            errors.append(
                f"[silent-resolution] {pair['claim_a'][:12]} vs {pair['claim_b'][:12]}: "
                f"'{pair['subject']}' / '{pair['predicate']}' => '{pair['object_a']}' vs "
                f"'{pair['object_b']}' — functional predicate, no conflicts_with edge. "
                f"Run: conflict link {pair['claim_a']} {pair['claim_b']}")
        elif not pair["functional"] and not pair["already_linked"]:
            warnings.append(
                f"[multivalued-collision] {pair['claim_a'][:12]}/{pair['claim_b'][:12]} share "
                f"subject+predicate with different objects (may be legitimate); reconciler should judge.")

    # 2. Non-falsifiable — empty rebuttal conditions.
    for cid, c in claims.items():
        if c["epistemic_status"] in _RETIRED:
            continue
        if not json.loads(c["rebuttal"] or "[]"):
            errors.append(f"[non-falsifiable] {cid[:12]} ('{c['subj']} {c['pred']} {c['obj']}') "
                          f"has no rebuttal_conditions.")

    # 3. Overconfidence — score present but only self-reported.
    for cid, c in claims.items():
        if c["confidence_score"] is not None and c["confidence_method"] == "self_report":
            warnings.append(f"[overconfidence] {cid[:12]} confidence {c['confidence_score']} is "
                            f"self_report only — recalibrate against agreement before promotion.")

    # 4. Uncertainty propagation — purely-derived claim more certain than its
    #    weakest parent (likelihood band). Warning only (review S3).
    band_ix = {b: i for i, b in enumerate(E.LIKELIHOOD_BANDS)}
    for cid, c in claims.items():
        deps = json.loads(c["depends_on"] or "[]")
        grounds = json.loads(c["grounds"] or "[]")
        if deps and not grounds and c["likelihood_band"] in band_ix:
            parent_ix = [band_ix[claims[d]["likelihood_band"]] for d in deps
                         if d in claims and claims[d]["likelihood_band"] in band_ix]
            if parent_ix and band_ix[c["likelihood_band"]] > min(parent_ix):
                warnings.append(f"[uncertainty-propagation] {cid[:12]} (derived, no own grounds) is "
                                f"more likely than its weakest premise.")

    # 5. Orphaned cascade — a believed claim resting on a discredited source or
    #    a retired parent, yet not flagged needs_review.
    discredited = {r["source_id"] for r in con.execute("SELECT source_id FROM sources WHERE discredited=1")}
    for cid, c in claims.items():
        if c["epistemic_status"] not in _BELIEVED:
            continue
        srcs = {r["source_id"] for r in con.execute(
            "SELECT source_id FROM evidence WHERE claim_id=?", (cid,))}
        if srcs & discredited:
            errors.append(f"[orphaned-cascade] {cid[:12]} is believed but cites a discredited source; "
                          f"should be needs_review.")
        deps = json.loads(c["depends_on"] or "[]")
        if any(d in claims and claims[d]["epistemic_status"] in _RETIRED for d in deps):
            errors.append(f"[orphaned-cascade] {cid[:12]} is believed but depends on a retired claim; "
                          f"should be needs_review.")
    con.close()
    return {"errors": errors, "warnings": warnings}


def review_queue(topic: str | None = None) -> list[dict]:
    """Escalate-to-human triggers (report §Recommendations)."""
    con = P.build_index(topic)
    items = []
    claims = {r["claim_id"]: dict(r) for r in con.execute("SELECT * FROM claims")}

    for cid, c in claims.items():
        if c["needs_review"]:
            items.append({"claim_id": cid, "trigger": "needs_review",
                          "detail": c["review_reason"], "assertion": f"{c['subj']} {c['pred']} {c['obj']}"})
        # high confidence + low corroboration (agreement proxy)
        n_auth = con.execute(
            "SELECT COUNT(DISTINCT e.source_id) n FROM evidence e JOIN sources s ON s.source_id=e.source_id "
            "WHERE e.claim_id=? AND s.authority>=0.5 AND s.discredited=0", (cid,)).fetchone()["n"]
        if (c["confidence_score"] or 0) >= 0.8 and n_auth < 2 and c["epistemic_status"] in _BELIEVED:
            items.append({"claim_id": cid, "trigger": "high_confidence_low_corroboration",
                          "detail": f"score {c['confidence_score']} on {n_auth} authoritative source(s)",
                          "assertion": f"{c['subj']} {c['pred']} {c['obj']}"})

    for r in con.execute("SELECT * FROM threads WHERE conflict_type='misinformation' AND status='open'"):
        items.append({"thread_id": r["thread_id"], "trigger": "suspected_misinformation",
                      "detail": r["note"]})

    # high-authority source on one side of an open functional conflict
    for pair in CF.detect(topic):
        if pair["functional"] and pair["already_linked"]:
            items.append({"claim_a": pair["claim_a"], "claim_b": pair["claim_b"],
                          "trigger": "authority_contradiction",
                          "detail": f"{pair['subject']} {pair['predicate']}: "
                                    f"{pair['object_a']} vs {pair['object_b']}"})

    # NOTE: semantic (fuzzy) candidates deliberately do NOT enter this queue.
    # Every trigger here reflects corpus STATE or a recorded decision that the
    # curator can drain (link, resolve, re-affirm). A rejected fuzzy match has
    # no "judged, not a conflict" event, so it would reappear forever and break
    # the drain-the-queue discipline. Fuzzy candidates live on their own,
    # stateless surface: `kb.py conflict candidates`.
    con.close()
    return items
