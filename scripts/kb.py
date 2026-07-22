#!/usr/bin/env python3
"""kb.py — command-line driver for the investigative knowledge base.

Single entry point. Canonical store is the append-only event log; every
command re-projects the SQLite read-model as needed, so output always
reflects the log. Run `python scripts/kb.py --help`.

Pure stdlib. The agent-facing write path is `ingest` (write a candidate-claim
JSON file, then ingest it) — see templates/candidate_claim.json.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kb import claims as C          # noqa: E402
from kb import conflicts as CF      # noqa: E402
from kb import events as E          # noqa: E402
from kb import ingest as ING        # noqa: E402
from kb import invalidation as INV  # noqa: E402
from kb import lint as L            # noqa: E402
from kb import projection as P      # noqa: E402
from kb import query as Q           # noqa: E402
from kb import report as R          # noqa: E402


def _p(obj):
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def cmd_init(a):
    topic = a.topic
    E.EVENTS_DIR.mkdir(parents=True, exist_ok=True)
    path = E.topic_path(topic)
    if not path.exists():
        path.touch()
    cfg = E.read_config()
    cfg["topic"] = topic
    E.write_config(cfg)
    P.build_index(topic).close()
    print(f"Initialized topic '{topic}'. Active topic set. Log: {path}")


def cmd_topic(a):
    print(E.active_topic())


def cmd_ingest(a):
    res = ING.ingest_file(a.file, topic=a.topic)
    if not res["ok"]:
        print("INGEST REJECTED — fix these and retry:", file=sys.stderr)
        for e in res["errors"]:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)
    print(f"Ingested into '{res['topic']}': {len(res['created'])} new claim(s), "
          f"{len(res['skipped'])} duplicate(s), {res['sources']} source(s), "
          f"{res['evidence_added']} evidence item(s).")
    for cid in res["created"]:
        print(f"  + {cid}")


def cmd_source(a):
    if a.action == "add":
        _p(C.add_source(a.uri, title=a.title, origin=a.origin, authority=a.authority,
                        credibility=a.credibility, agent_run=a.run, topic=a.topic))
    elif a.action == "discredit":
        _p(INV.discredit_source(a.uri, reason=a.reason, agent_run=a.run, topic=a.topic))
    elif a.action == "recredit":
        _p(INV.recredit_source(a.uri, agent_run=a.run, topic=a.topic))


def cmd_claim(a):
    if a.action == "show":
        c = Q.show(a.claim, topic=a.topic)
        if not c:
            sys.exit(f"No such claim: {a.claim}")
        _p(c)
    elif a.action == "promote":
        chk = C.promotion_check(a.claim, topic=a.topic)
        if not a.to:
            _p(chk)
            return
        C.change_status(a.claim, a.to, reason=a.reason or "promotion", agent_run=a.run, topic=a.topic)
        print(f"{a.claim} -> {a.to}")
    elif a.action == "status":
        _p(C.change_status(a.claim, a.to, reason=a.reason, agent_run=a.run, topic=a.topic))
    elif a.action == "dispute":
        _p(C.change_status(a.claim, "disputed", reason=a.reason, agent_run=a.run, topic=a.topic))
    elif a.action == "retract":
        _p(INV.retract_claim(a.claim, reason=a.reason, retracted_by=a.by, agent_run=a.run, topic=a.topic))
    elif a.action == "supersede":
        _p(INV.supersede_claim(a.claim, a.new, agent_run=a.run, topic=a.topic))
    elif a.action == "recalibrate":
        _p(C.recalibrate(a.claim, a.score, method=a.method, agent_run=a.run, topic=a.topic))
    elif a.action == "rebuttal":
        _p(C.trigger_rebuttal(a.claim, a.condition, evidence=a.evidence, agent_run=a.run, topic=a.topic))


def cmd_conflict(a):
    if a.action == "detect":
        pairs = CF.detect(topic=a.topic)
        if not pairs:
            print("No structured conflicts (same subject+predicate, different object).")
            return
        for p in pairs:
            tag = "CONFLICT" if p["functional"] else "maybe"
            link = "linked" if p["already_linked"] else "UNLINKED"
            print(f"  [{tag}/{link}] {p['claim_a']}  vs  {p['claim_b']}")
            print(f"        {p['subject']} — {p['predicate']} — '{p['object_a']}' vs '{p['object_b']}'")
    elif a.action == "link":
        _p(CF.link(a.claim_a, a.claim_b, conflict_type=a.type, note=a.note, agent_run=a.run, topic=a.topic))
    elif a.action == "resolve":
        _p(CF.resolve_thread(a.thread, a.status, note=a.note, agent_run=a.run, topic=a.topic))


def cmd_invalidate(a):
    _p(INV.invalidate_claim(a.claim, cause=a.cause, new_status=a.to, agent_run=a.run, topic=a.topic))


def cmd_query(a):
    if a.action == "as-of":
        rows = Q.as_of_world(a.date, topic=a.topic) if a.world else Q.as_of_system(a.date, topic=a.topic)
        axis = "world-time (believed true on)" if a.world else "system-time (believed as of)"
        print(f"# Belief state — {axis} {a.date}")
        for r in rows:
            print(f"  [{r['epistemic_status']}] {r['subj']} — {r['pred']} — {r['obj']}")
        if not rows:
            print("  (nothing believed)")
    elif a.action == "downstream":
        _p(Q.downstream(a.source, topic=a.topic))
    elif a.action == "status":
        _p(Q.by_status(a.status, topic=a.topic))
    elif a.action == "revisions":
        _p(Q.revision_log(a.claim, topic=a.topic))


def cmd_reindex(a):
    P.build_index(a.topic).close()
    print(f"Rebuilt index at {E.INDEX_PATH}")


def cmd_lint(a):
    res = L.lint(topic=a.topic)
    for w in res["warnings"]:
        print(f"WARN  {w}")
    for e in res["errors"]:
        print(f"ERROR {e}")
    print(f"\n{len(res['errors'])} error(s), {len(res['warnings'])} warning(s).")
    sys.exit(1 if res["errors"] else 0)


def cmd_review_queue(a):
    items = L.review_queue(topic=a.topic)
    if not items:
        print("Review queue empty.")
        return
    for it in items:
        print(f"  [{it['trigger']}] {it.get('assertion') or it.get('detail') or ''}")
        if it.get("detail") and it.get("assertion"):
            print(f"        {it['detail']}")


def cmd_report(a):
    print(R.report(topic=a.topic))


def build_parser():
    ap = argparse.ArgumentParser(prog="kb.py", description="Investigative knowledge base")
    sub = ap.add_subparsers(dest="cmd", required=True)

    # common options usable AFTER any subcommand: `kb.py ingest f.json --run r --topic t`
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--topic", help="override active topic")
    common.add_argument("--run", help="agent_run id to attribute events to")

    # Flat commands take `common` directly. For command GROUPS with an action
    # sub-subparser, `common` goes on each LEAF (so --topic/--run parse at the
    # end of the line, where they're typed) — not on the group, which would
    # trigger argparse's default-override gotcha.
    def add(name, **kw):
        return sub.add_parser(name, parents=[common], **kw)

    def leaf(ss, name, **kw):
        return ss.add_parser(name, parents=[common], **kw)

    s = sub.add_parser("init"); s.add_argument("--topic", required=True); s.set_defaults(func=cmd_init)
    add("topic").set_defaults(func=cmd_topic)

    s = add("ingest"); s.add_argument("file"); s.set_defaults(func=cmd_ingest)

    s = sub.add_parser("source"); ss = s.add_subparsers(dest="action", required=True)
    a1 = leaf(ss, "add"); a1.add_argument("uri"); a1.add_argument("--title"); a1.add_argument("--origin")
    a1.add_argument("--authority", type=float); a1.add_argument("--credibility")
    a2 = leaf(ss, "discredit"); a2.add_argument("uri"); a2.add_argument("--reason", required=True)
    a3 = leaf(ss, "recredit"); a3.add_argument("uri")
    s.set_defaults(func=cmd_source)

    s = sub.add_parser("claim"); ss = s.add_subparsers(dest="action", required=True)
    b = leaf(ss, "show"); b.add_argument("claim")
    b = leaf(ss, "promote"); b.add_argument("claim"); b.add_argument("--to"); b.add_argument("--reason")
    b = leaf(ss, "status"); b.add_argument("claim"); b.add_argument("--to", required=True); b.add_argument("--reason")
    b = leaf(ss, "dispute"); b.add_argument("claim"); b.add_argument("--reason")
    b = leaf(ss, "retract"); b.add_argument("claim"); b.add_argument("--reason", required=True); b.add_argument("--by")
    b = leaf(ss, "supersede"); b.add_argument("claim"); b.add_argument("new")
    b = leaf(ss, "recalibrate"); b.add_argument("claim"); b.add_argument("--score", type=float, required=True)
    b.add_argument("--method", required=True)
    b = leaf(ss, "rebuttal"); b.add_argument("claim"); b.add_argument("--condition", required=True)
    b.add_argument("--evidence")
    s.set_defaults(func=cmd_claim)

    s = sub.add_parser("conflict"); ss = s.add_subparsers(dest="action", required=True)
    leaf(ss, "detect")
    b = leaf(ss, "link"); b.add_argument("claim_a"); b.add_argument("claim_b")
    b.add_argument("--type", default="factual"); b.add_argument("--note")
    b = leaf(ss, "resolve"); b.add_argument("thread"); b.add_argument("status"); b.add_argument("--note")
    s.set_defaults(func=cmd_conflict)

    s = add("invalidate"); s.add_argument("claim"); s.add_argument("--cause", required=True)
    s.add_argument("--to", default="retracted"); s.set_defaults(func=cmd_invalidate)

    s = sub.add_parser("query"); ss = s.add_subparsers(dest="action", required=True)
    b = leaf(ss, "as-of"); b.add_argument("date"); b.add_argument("--world", action="store_true")
    b = leaf(ss, "downstream"); b.add_argument("source")
    b = leaf(ss, "status"); b.add_argument("status")
    b = leaf(ss, "revisions"); b.add_argument("claim")
    s.set_defaults(func=cmd_query)

    add("reindex").set_defaults(func=cmd_reindex)
    add("lint").set_defaults(func=cmd_lint)
    add("review-queue").set_defaults(func=cmd_review_queue)
    add("report").set_defaults(func=cmd_report)
    return ap


def main(argv=None):
    ap = build_parser()
    a = ap.parse_args(argv)
    # allow global --topic/--run to reach subcommand handlers
    if not hasattr(a, "topic"):
        a.topic = None
    if not hasattr(a, "run"):
        a.run = None
    a.func(a)


if __name__ == "__main__":
    main()
