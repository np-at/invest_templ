"""Projection: replay the event log into a disposable SQLite read-model.

The index is never authoritative — it is rebuilt from events on demand.
`build_index(as_of=...)` replays only events up to a system-time cutoff,
which is exactly how `query as-of --system D` answers "what did we believe
on D" (B2/S9): the discredit/retract events after D simply aren't applied.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from . import events as E

SCHEMA = """
CREATE TABLE sources (
    source_id TEXT PRIMARY KEY,
    uri TEXT, title TEXT, origin TEXT,
    authority REAL, credibility TEXT,
    retrieved_at TEXT,
    discredited INTEGER DEFAULT 0,
    discredit_reason TEXT, discredited_at TEXT
);
CREATE TABLE agent_runs (
    run_id TEXT PRIMARY KEY, role TEXT, model TEXT, task TEXT, started_at TEXT
);
CREATE TABLE claims (
    claim_id TEXT PRIMARY KEY, topic TEXT,
    subj TEXT, pred TEXT, obj TEXT, nl TEXT,
    warrant TEXT, backing TEXT, rebuttal TEXT,
    epistemic_status TEXT,
    likelihood_band TEXT, analytic_confidence TEXT,
    confidence_score REAL, confidence_method TEXT,
    method TEXT,
    t_valid TEXT, t_invalid TEXT,       -- WORLD time (fact true-then-false)
    t_created TEXT, t_expired TEXT,     -- SYSTEM time (learned / retired)
    superseded_by TEXT, retracted_by TEXT,
    needs_review INTEGER DEFAULT 0, review_reason TEXT,
    depends_on TEXT, grounds TEXT,
    created_by_run TEXT
);
CREATE TABLE evidence (
    evidence_id TEXT PRIMARY KEY, claim_id TEXT, source_id TEXT,
    quote TEXT, locator TEXT
);
CREATE TABLE edges (
    edge_id TEXT PRIMARY KEY, kind TEXT, from_id TEXT, to_id TEXT,
    meta TEXT, created_at TEXT
);
CREATE TABLE threads (
    thread_id TEXT PRIMARY KEY, claim_a TEXT, claim_b TEXT,
    conflict_type TEXT, status TEXT, note TEXT, created_at TEXT
);
CREATE TABLE revisions (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id TEXT, event TEXT, detail TEXT, t_created TEXT, agent_run TEXT
);
CREATE INDEX ix_claims_sp ON claims(subj, pred);
CREATE INDEX ix_evidence_claim ON evidence(claim_id);
CREATE INDEX ix_evidence_source ON evidence(source_id);
CREATE INDEX ix_edges_from ON edges(from_id);
CREATE INDEX ix_edges_to ON edges(to_id);
"""


def _revision(cur, rec, claim_id, detail):
    cur.execute(
        "INSERT INTO revisions(claim_id,event,detail,t_created,agent_run) VALUES(?,?,?,?,?)",
        (claim_id, rec["event"], json.dumps(detail, ensure_ascii=False),
         rec.get("t_created"), rec.get("agent_run")),
    )


def _apply(cur, rec):
    ev = rec["event"]
    p = rec

    if ev == "seed_marker":
        return

    if ev == "agent_run_started":
        cur.execute(
            "INSERT OR REPLACE INTO agent_runs VALUES(?,?,?,?,?)",
            (p["run_id"], p.get("role"), p.get("model"), p.get("task"), rec.get("t_created")),
        )

    elif ev == "source_registered":
        cur.execute(
            "INSERT OR IGNORE INTO sources(source_id,uri,title,origin,authority,credibility,retrieved_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (p["source_id"], p.get("uri"), p.get("title"), p.get("origin"),
             p.get("authority"), p.get("credibility"), p.get("retrieved_at")),
        )

    elif ev == "source_discredited":
        cur.execute(
            "UPDATE sources SET discredited=1, discredit_reason=?, discredited_at=? WHERE source_id=?",
            (p.get("reason"), rec.get("t_created"), p["source_id"]),
        )

    elif ev == "source_recredited":
        cur.execute(
            "UPDATE sources SET discredited=0, discredit_reason=NULL, discredited_at=NULL WHERE source_id=?",
            (p["source_id"],),
        )

    elif ev == "claim_created":
        a = p["assertion"]
        cur.execute(
            "INSERT OR IGNORE INTO claims(claim_id,topic,subj,pred,obj,nl,warrant,backing,rebuttal,"
            "epistemic_status,likelihood_band,analytic_confidence,confidence_score,confidence_method,"
            "method,t_valid,t_invalid,t_created,t_expired,depends_on,grounds,created_by_run) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (p["claim_id"], rec["topic"], a["s"], a["p"], a["o"], a.get("nl"),
             p.get("warrant"), json.dumps(p.get("backing", [])),
             json.dumps(p.get("rebuttal_conditions", [])),
             p.get("epistemic_status", "hypothesized"),
             p.get("likelihood_band"), p.get("analytic_confidence"),
             p.get("confidence_score"), p.get("confidence_method"),
             p.get("method"),
             p.get("t_valid"), p.get("t_invalid"),
             rec.get("t_created"), None,
             json.dumps(p.get("depends_on", [])),
             json.dumps(p.get("grounds", [])),
             rec.get("agent_run")),
        )
        for parent in p.get("depends_on", []):
            cur.execute(
                "INSERT OR IGNORE INTO edges(edge_id,kind,from_id,to_id,meta,created_at) VALUES(?,?,?,?,?,?)",
                ("edg_dep_" + p["claim_id"][4:] + "_" + str(parent)[4:14],
                 "depends_on", p["claim_id"], parent, None, rec.get("t_created")),
            )

    elif ev == "evidence_added":
        cur.execute(
            "INSERT OR IGNORE INTO evidence VALUES(?,?,?,?,?)",
            (p["evidence_id"], p["claim_id"], p["source_id"], p.get("quote"), p.get("locator")),
        )

    elif ev == "status_changed":
        cur.execute("UPDATE claims SET epistemic_status=? WHERE claim_id=?",
                    (p["to"], p["claim_id"]))
        if p["to"] == "needs_review":
            cur.execute("UPDATE claims SET needs_review=1, review_reason=? WHERE claim_id=?",
                        (p.get("reason"), p["claim_id"]))
        else:
            cur.execute("UPDATE claims SET needs_review=0, review_reason=NULL WHERE claim_id=?",
                        (p["claim_id"],))
        _revision(cur, rec, p["claim_id"], {"from": p.get("from"), "to": p["to"], "reason": p.get("reason")})

    elif ev == "confidence_recalibrated":
        cur.execute(
            "UPDATE claims SET confidence_score=?, confidence_method=? WHERE claim_id=?",
            (p["new_score"], p.get("method"), p["claim_id"]),
        )
        _revision(cur, rec, p["claim_id"],
                  {"old": p.get("old_score"), "new": p["new_score"], "method": p.get("method")})

    elif ev == "conflict_linked":
        cur.execute(
            "INSERT OR IGNORE INTO edges(edge_id,kind,from_id,to_id,meta,created_at) VALUES(?,?,?,?,?,?)",
            (p["edge_id"], "conflicts_with", p["claim_a"], p["claim_b"],
             json.dumps({"type": p.get("conflict_type")}), rec.get("t_created")),
        )
        cur.execute(
            "INSERT OR IGNORE INTO threads VALUES(?,?,?,?,?,?,?)",
            (p["thread_id"], p["claim_a"], p["claim_b"], p.get("conflict_type"),
             "open", p.get("note"), rec.get("t_created")),
        )

    elif ev == "resolution_updated":
        cur.execute("UPDATE threads SET status=?, note=? WHERE thread_id=?",
                    (p["status"], p.get("note"), p["thread_id"]))

    elif ev == "claim_invalidated":
        # Epistemic retirement -> SYSTEM time (t_expired) + status. Never t_invalid.
        cur.execute(
            "UPDATE claims SET t_expired=?, epistemic_status=? WHERE claim_id=?",
            (rec.get("t_created"), p.get("new_status", "retracted"), p["claim_id"]),
        )
        _revision(cur, rec, p["claim_id"], {"cause": p.get("cause"), "by": p.get("invalidated_by")})

    elif ev == "claim_superseded":
        cur.execute(
            "UPDATE claims SET epistemic_status='superseded', superseded_by=?, t_expired=? WHERE claim_id=?",
            (p["new_claim_id"], rec.get("t_created"), p["old_claim_id"]),
        )
        cur.execute(
            "INSERT OR IGNORE INTO edges(edge_id,kind,from_id,to_id,meta,created_at) VALUES(?,?,?,?,?,?)",
            ("edg_sup_" + p["new_claim_id"][4:] + "_" + p["old_claim_id"][4:12],
             "supersedes", p["new_claim_id"], p["old_claim_id"], None, rec.get("t_created")),
        )
        _revision(cur, rec, p["old_claim_id"], {"superseded_by": p["new_claim_id"]})

    elif ev == "claim_retracted":
        cur.execute(
            "UPDATE claims SET epistemic_status='retracted', retracted_by=?, t_expired=? WHERE claim_id=?",
            (p.get("retracted_by"), rec.get("t_created"), p["claim_id"]),
        )
        _revision(cur, rec, p["claim_id"], {"reason": p.get("reason"), "by": p.get("retracted_by")})

    elif ev == "rebuttal_triggered":
        row = cur.execute("SELECT epistemic_status FROM claims WHERE claim_id=?",
                          (p["claim_id"],)).fetchone()
        reason = "rebuttal condition met: " + str(p.get("condition"))
        if row and row["epistemic_status"] not in ("retracted", "superseded"):
            cur.execute("UPDATE claims SET epistemic_status='needs_review', needs_review=1, "
                        "review_reason=? WHERE claim_id=?", (reason, p["claim_id"]))
        else:
            cur.execute("UPDATE claims SET needs_review=1, review_reason=? WHERE claim_id=?",
                        (reason, p["claim_id"]))
        _revision(cur, rec, p["claim_id"], {"condition": p.get("condition"), "evidence": p.get("evidence")})


def build_index(topic: str | None = None, *, as_of: str | None = None,
                db_path: str | Path | None = None) -> sqlite3.Connection:
    """Rebuild the read-model from events and return an open connection.

    as_of  -> system-time cutoff (belief as of that instant).
    db_path -> ':memory:' for as-of/ephemeral queries; defaults to kb/index.db.
    """
    if db_path is None:
        db_path = E.INDEX_PATH
    if str(db_path) != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        if Path(db_path).exists():
            Path(db_path).unlink()
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    cur = con.cursor()
    for rec in E.read_events(topic, as_of=as_of):
        _apply(cur, rec)
    con.commit()
    return con
