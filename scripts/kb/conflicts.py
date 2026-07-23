"""Conflict detection & linking (report §D).

Two-tier by design:
  - STRUCTURED (here, in code): same normalized subject+predicate, different
    object. For FUNCTIONAL predicates this is a genuine contradiction and is
    hard-reported; for multi-valued predicates it is only a *suggestion* (a
    subject can have offices in two cities) — the reconciler agent judges it.
  - SEMANTIC (`detect_semantic` + the reconciler agent): paraphrase conflicts
    exact-match cannot see. A stdlib LEXICAL prefilter surfaces candidate pairs
    (shared-vocabulary paraphrase, morphology, reordering); the reconciler makes
    the final semantic call and links. Disjoint-vocabulary synonymy needs that
    LLM judgment or the optional `--embed` (model2vec) backend. Detection only
    SURFACES — it never auto-links (gathering != deciding).

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


def detect_semantic(topic: str | None = None, *, threshold: float | None = None,
                    backend: str = "lexical") -> list[dict]:
    """Surface CANDIDATE near-duplicate / near-conflict pairs that exact
    `detect` misses because subject/predicate differ only on the surface.

    Read-only. Never links: the reconciler agent judges each candidate, then
    `conflict link`s real conflicts (or recommends `claim supersede` for real
    duplicates). `backend='lexical'` is stdlib (tier a); `backend='embed'` uses
    the optional model2vec embeddings (tier c). `threshold` overrides both the
    subject and predicate bars when given.
    """
    from . import similarity as S
    subj_bar = S.SUBJ_SIM if threshold is None else threshold
    pred_bar = S.PRED_SIM if threshold is None else threshold

    con = P.build_index(topic)
    claims = [dict(r) for r in con.execute(
        "SELECT claim_id, subj, pred, obj, epistemic_status FROM claims")]
    linked = set()
    for r in con.execute("SELECT from_id, to_id FROM edges WHERE kind='conflicts_with'"):
        linked.add(frozenset((r["from_id"], r["to_id"])))
    # Per-claim source set — the judge needs this to tell INDEPENDENT
    # attestation (disjoint sources => corroboration, must NOT be superseded)
    # from same-source RESTATEMENT (redundant => supersede is safe).
    src_by_claim: dict[str, set] = {}
    for r in con.execute("SELECT DISTINCT claim_id, source_id FROM evidence"):
        src_by_claim.setdefault(r["claim_id"], set()).add(r["source_id"])
    con.close()

    live = [c for c in claims if c["epistemic_status"] in _LIVE]

    if backend == "embed":
        fields = ([c["subj"] for c in live] + [c["pred"] for c in live]
                  + [c["obj"] for c in live])
        vecs = S.embed_texts(fields)
        def sim(a: str, b: str) -> float:
            return 1.0 if E.normalize(a) == E.normalize(b) else S.cosine(vecs[a], vecs[b])
    elif backend == "lexical":
        sim = S.similarity
    else:
        raise SystemExit(f"Unknown backend {backend!r}; use 'lexical' or 'embed'.")

    pairs = []
    for i in range(len(live)):
        for j in range(i + 1, len(live)):
            a, b = live[i], live[j]
            # skip pairs the EXACT detector already groups (same normalized s+p)
            if (E.normalize(a["subj"]) == E.normalize(b["subj"])
                    and E.normalize(a["pred"]) == E.normalize(b["pred"])):
                continue
            ssim = sim(a["subj"], b["subj"])
            if ssim < subj_bar:
                continue
            psim = sim(a["pred"], b["pred"])
            if psim < pred_bar:
                continue
            osim = sim(a["obj"], b["obj"])
            src_a = src_by_claim.get(a["claim_id"], set())
            src_b = src_by_claim.get(b["claim_id"], set())
            # The field that decides supersede-vs-keep for a SAME_OBJECT pair.
            # Three states — never collapse "unknown" into "shared": recommending
            # supersede on the ABSENCE of evidence cuts against confidence-from-
            # agreement, so an evidence-less side is called out explicitly.
            if not src_a or not src_b:
                source_relation = "unknown"          # a side has no evidence
            elif src_a & src_b:
                source_relation = "shared"           # same source => restatement
            else:
                source_relation = "independent"      # disjoint => corroboration
            # NB: `relation`/`severity` describe the STRING relation only, not an
            # epistemic verdict — the reconciler judges. `same_object` means the
            # objects match; the differing-object case is a contradiction only
            # for a FUNCTIONAL (single-valued) predicate (same gate detect uses).
            if osim >= S.OBJ_DUP_SIM:
                relation, severity = "same_object", "same_object"
            else:
                functional = (E.normalize(a["pred"]) in E.FUNCTIONAL_PREDICATES
                              or E.normalize(b["pred"]) in E.FUNCTIONAL_PREDICATES)
                relation = "diff_object"
                severity = "diff_object_functional" if functional else "diff_object_multivalued"
            pairs.append({
                "claim_a": a["claim_id"], "claim_b": b["claim_id"],
                "subject_a": a["subj"], "subject_b": b["subj"],
                "predicate_a": a["pred"], "predicate_b": b["pred"],
                "object_a": a["obj"], "object_b": b["obj"],
                "subj_sim": round(ssim, 3), "pred_sim": round(psim, 3),
                "obj_sim": round(osim, 3),
                "relation": relation, "severity": severity,
                "sources_a": sorted(src_a), "sources_b": sorted(src_b),
                "source_relation": source_relation,
                "already_linked": frozenset((a["claim_id"], b["claim_id"])) in linked,
            })
    # unlinked first, functional-contradiction candidates before the rest,
    # then strongest surface match
    sev_rank = {"diff_object_functional": 0, "same_object": 1, "diff_object_multivalued": 2}
    pairs.sort(key=lambda p: (p["already_linked"], sev_rank.get(p["severity"], 3),
                              -(p["subj_sim"] + p["pred_sim"])))
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
