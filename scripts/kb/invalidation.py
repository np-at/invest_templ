"""Fact invalidation & belief revision (report §E).

Key decision (per review S1): discrediting a source or retiring a claim does
NOT auto-falsify dependents — a dependent may stand on independent legs.
Instead the cascade marks every downstream claim `needs_review` and puts it on
the review-queue; actual retirement is a separate curator decision. This keeps
us out of a literal TMS (bucket 3) while still guaranteeing no orphaned
downstream belief (the report's cascade-on-retraction failure mode).

The cascade uses a visited-set, so diamonds and cycles terminate.
"""
from __future__ import annotations

from . import claims as C
from . import events as E
from . import projection as P


def _dependents(con, claim_id):
    """claims that depend_on claim_id (reverse edge)."""
    return [r["from_id"] for r in con.execute(
        "SELECT from_id FROM edges WHERE kind='depends_on' AND to_id=?", (claim_id,))]


def _flag_needs_review(cid, reason, agent_run, topic):
    claim = C.get_claim(cid, topic)
    if not claim:
        return False
    st = claim["epistemic_status"]
    if st in ("retracted", "superseded", "needs_review"):
        return False
    if "needs_review" not in E.TRANSITIONS.get(st, set()):
        return False
    E.append_event("status_changed",
                   {"claim_id": cid, "from": st, "to": "needs_review", "reason": reason},
                   topic=topic, agent_run=agent_run)
    return True


def cascade(seed_claim_ids, reason, *, agent_run=None, topic=None) -> list[str]:
    """BFS over reverse depends_on edges, flagging dependents needs_review."""
    con = P.build_index(topic)
    visited = set(seed_claim_ids)
    queue = list(seed_claim_ids)
    flagged = []
    while queue:
        cur = queue.pop(0)
        for dep in _dependents(con, cur):
            if dep in visited:
                continue
            visited.add(dep)
            queue.append(dep)
            if _flag_needs_review(dep, f"{reason} (upstream {cur[:12]})", agent_run, topic):
                flagged.append(dep)
    con.close()
    P.build_index(topic).close()
    return flagged


def discredit_source(uri_or_id, *, reason, agent_run=None, topic=None) -> dict:
    sid = uri_or_id if str(uri_or_id).startswith("src_") else E.source_id(uri_or_id)
    E.append_event("source_discredited", {"source_id": sid, "reason": reason},
                   topic=topic, agent_run=agent_run)
    con = P.build_index(topic)
    affected = [r["claim_id"] for r in con.execute(
        "SELECT DISTINCT claim_id FROM evidence WHERE source_id=?", (sid,))]
    con.close()
    directly_flagged = []
    for cid in affected:
        if _flag_needs_review(cid, f"cites discredited source {sid[:12]}", agent_run, topic):
            directly_flagged.append(cid)
    downstream = cascade(affected, f"depends on claim citing discredited source {sid[:12]}",
                         agent_run=agent_run, topic=topic)
    return {"ok": True, "source_id": sid, "directly_flagged": directly_flagged,
            "downstream_flagged": downstream}


def recredit_source(uri_or_id, *, agent_run=None, topic=None) -> dict:
    sid = uri_or_id if str(uri_or_id).startswith("src_") else E.source_id(uri_or_id)
    E.append_event("source_recredited", {"source_id": sid}, topic=topic, agent_run=agent_run)
    P.build_index(topic).close()
    # Claims are left on the review-queue for a curator to re-affirm — recrediting
    # a source does not auto-restore belief (multiple-justification problem, S2).
    return {"ok": True, "source_id": sid,
            "note": "dependent claims remain needs_review; curator re-affirms explicitly"}


def invalidate_claim(cid, *, cause, new_status="retracted", agent_run=None, topic=None) -> dict:
    if new_status not in E.STATUSES:
        raise SystemExit(f"Unknown status {new_status!r}")
    claim = C.get_claim(cid, topic)
    if not claim:
        raise SystemExit(f"No such claim: {cid}")
    E.append_event("claim_invalidated",
                   {"claim_id": cid, "cause": cause, "new_status": new_status,
                    "invalidated_by": agent_run},
                   topic=topic, agent_run=agent_run)
    downstream = cascade([cid], f"depends on invalidated claim {cid[:12]}",
                         agent_run=agent_run, topic=topic)
    return {"ok": True, "claim_id": cid, "new_status": new_status, "downstream_flagged": downstream}


def retract_claim(cid, *, reason, retracted_by=None, agent_run=None, topic=None) -> dict:
    claim = C.get_claim(cid, topic)
    if not claim:
        raise SystemExit(f"No such claim: {cid}")
    E.append_event("claim_retracted",
                   {"claim_id": cid, "reason": reason, "retracted_by": retracted_by},
                   topic=topic, agent_run=agent_run)
    downstream = cascade([cid], f"depends on retracted claim {cid[:12]}",
                         agent_run=agent_run, topic=topic)
    return {"ok": True, "claim_id": cid, "downstream_flagged": downstream}


def supersede_claim(old_cid, new_cid, *, agent_run=None, topic=None) -> dict:
    if not C.get_claim(old_cid, topic):
        raise SystemExit(f"No such claim: {old_cid}")
    if not C.get_claim(new_cid, topic):
        raise SystemExit(f"No such (new) claim: {new_cid}")
    E.append_event("claim_superseded", {"old_claim_id": old_cid, "new_claim_id": new_cid},
                   topic=topic, agent_run=agent_run)
    downstream = cascade([old_cid], f"depends on superseded claim {old_cid[:12]}",
                         agent_run=agent_run, topic=topic)
    return {"ok": True, "old": old_cid, "new": new_cid, "downstream_flagged": downstream}
