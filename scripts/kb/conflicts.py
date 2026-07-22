"""Conflict detection & linking (report §D).

Two-tier by design:
  - STRUCTURED (here, in code): same normalized subject+predicate, different
    object. For FUNCTIONAL predicates this is a genuine contradiction and is
    hard-reported; for multi-valued predicates it is only a *suggestion* (a
    subject can have offices in two cities) — the reconciler agent judges it.
  - SEMANTIC (the reconciler agent's job): paraphrase / entailment conflicts
    that exact-match cannot see. stdlib does no embeddings — stated plainly.

Linking a conflict is mandatory once found: the report's top failure mode is
*silent resolution*. `conflict_linked` opens a resolution thread and retains
BOTH claims (multi-perspective retention) rather than overwriting either.
"""
from __future__ import annotations

from . import events as E
from . import projection as P

# statuses that still "count" as live claims for conflict purposes
_LIVE = {"hypothesized", "probable", "corroborated", "confirmed", "disputed", "needs_review"}


def detect(topic: str | None = None) -> list[dict]:
    """Return candidate conflict pairs, each tagged functional vs multivalued
    and whether a conflicts_with edge already links them."""
    con = P.build_index(topic)
    claims = [dict(r) for r in con.execute(
        "SELECT claim_id, subj, pred, obj, epistemic_status FROM claims")]
    linked = set()
    for r in con.execute("SELECT from_id, to_id FROM edges WHERE kind='conflicts_with'"):
        linked.add(frozenset((r["from_id"], r["to_id"])))
    con.close()

    by_sp: dict[tuple, list[dict]] = {}
    for c in claims:
        if c["epistemic_status"] not in _LIVE:
            continue
        key = (E.normalize(c["subj"]), E.normalize(c["pred"]))
        by_sp.setdefault(key, []).append(c)

    pairs = []
    for (subj_n, pred_n), group in by_sp.items():
        objs = {}
        for c in group:
            objs.setdefault(E.normalize(c["obj"]), c)
        if len(objs) < 2:
            continue
        members = list(objs.values())
        functional = pred_n in E.FUNCTIONAL_PREDICATES
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = members[i], members[j]
                pairs.append({
                    "claim_a": a["claim_id"], "claim_b": b["claim_id"],
                    "subject": a["subj"], "predicate": a["pred"],
                    "object_a": a["obj"], "object_b": b["obj"],
                    "functional": functional,
                    "severity": "conflict" if functional else "suggestion",
                    "already_linked": frozenset((a["claim_id"], b["claim_id"])) in linked,
                })
    # contradictions first, unlinked first
    pairs.sort(key=lambda p: (p["already_linked"], not p["functional"]))
    return pairs


def link(claim_a: str, claim_b: str, *, conflict_type: str = "factual",
         note: str | None = None, agent_run: str | None = None,
         topic: str | None = None) -> dict:
    if conflict_type not in E.CONFLICT_TYPES:
        raise SystemExit(f"Unknown conflict_type {conflict_type!r}; allowed {E.CONFLICT_TYPES}")
    a, b = sorted((claim_a, claim_b))          # canonical order -> stable ids
    edge_id = "edg_cfl_" + a[4:12] + "_" + b[4:12]
    thread_id = "thr_" + a[4:12] + "_" + b[4:12]
    E.append_event("conflict_linked", {
        "edge_id": edge_id, "thread_id": thread_id,
        "claim_a": a, "claim_b": b, "conflict_type": conflict_type, "note": note,
    }, topic=topic, agent_run=agent_run)
    # both sides move to disputed (retain both — no overwrite)
    from . import claims as C
    for cid in (a, b):
        cur = C.get_claim(cid, topic)
        if cur and cur["epistemic_status"] in E.TRANSITIONS and \
                "disputed" in E.TRANSITIONS[cur["epistemic_status"]]:
            C.change_status(cid, "disputed", reason=f"conflict {thread_id}",
                            agent_run=agent_run, topic=topic)
    P.build_index(topic).close()
    return {"ok": True, "thread_id": thread_id, "edge_id": edge_id}


def resolve_thread(thread_id: str, status: str, *, note: str | None = None,
                   agent_run: str | None = None, topic: str | None = None) -> dict:
    E.append_event("resolution_updated", {"thread_id": thread_id, "status": status, "note": note},
                   topic=topic, agent_run=agent_run)
    P.build_index(topic).close()
    return {"ok": True, "thread_id": thread_id, "status": status}
