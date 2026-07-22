"""Corpus status report: a scannable summary of the current belief state."""
from __future__ import annotations

from . import events as E
from . import lint as L
from . import projection as P


def report(topic: str | None = None) -> str:
    topic = E.active_topic(topic)
    con = P.build_index(topic)
    lines = [f"# Investigation corpus: {topic}", ""]

    counts = {r["epistemic_status"]: r["n"] for r in con.execute(
        "SELECT epistemic_status, COUNT(*) n FROM claims GROUP BY epistemic_status")}
    total = sum(counts.values())
    n_sources = con.execute("SELECT COUNT(*) n FROM sources").fetchone()["n"]
    n_discredited = con.execute("SELECT COUNT(*) n FROM sources WHERE discredited=1").fetchone()["n"]
    n_threads = con.execute("SELECT COUNT(*) n FROM threads").fetchone()["n"]
    n_open = con.execute("SELECT COUNT(*) n FROM threads WHERE status='open'").fetchone()["n"]

    lines.append(f"Claims: {total}   Sources: {n_sources} ({n_discredited} discredited)   "
                 f"Conflict threads: {n_threads} ({n_open} open)")
    lines.append("")
    lines.append("## Claims by epistemic status")
    for st in E.STATUSES:
        if counts.get(st):
            lines.append(f"  {st:<14} {counts[st]}")
    lines.append("")

    believed = con.execute(
        "SELECT subj,pred,obj,epistemic_status,confidence_score FROM claims "
        "WHERE epistemic_status IN ('probable','corroborated','confirmed') ORDER BY confidence_score DESC")
    lines.append("## Currently believed")
    any_b = False
    for r in believed:
        any_b = True
        cs = f"{r['confidence_score']:.2f}" if r["confidence_score"] is not None else "  ? "
        lines.append(f"  [{r['epistemic_status']:<11} {cs}] {r['subj']} — {r['pred']} — {r['obj']}")
    if not any_b:
        lines.append("  (none yet)")
    lines.append("")

    con.close()
    lq = L.lint(topic)
    rq = L.review_queue(topic)
    lines.append(f"## Health:  lint errors: {len(lq['errors'])}   warnings: {len(lq['warnings'])}"
                 f"   review-queue: {len(rq)}")
    for e in lq["errors"]:
        lines.append(f"  ! {e}")
    return "\n".join(lines) + "\n"
