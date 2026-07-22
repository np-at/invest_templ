# Event schema

The canonical store is an append-only JSONL log: `kb/events/<topic>.jsonl`,
one JSON object per line. **Nothing is ever mutated or deleted.** Belief change
is a new event. The SQLite index (`kb/index.db`) is a disposable projection
rebuilt by replaying events in append order.

## Envelope (every event)

| field | set by | meaning |
|---|---|---|
| `v` | script | schema version (currently `1`) — lets old logs still replay |
| `event` | script | event type (see below) |
| `topic` | script | investigation topic |
| `t_created` | **script** | UTC ISO-8601 — system time the event was recorded |
| `agent_run` | caller | run id this event is attributed to (nullable) |
| `event_id` | script | `evt_` + sha256 of the record |

`t_created` is the **system-time** axis. It is always script-set; never write it yourself.

## The two temporal axes (do not conflate)

- **World time** — `t_valid` / `t_invalid` on a claim: when the asserted fact
  was true *in the world* (e.g. "X was CEO 2015–2020"). Only set these for
  genuine real-world change.
- **System time** — `t_created` (event) and `t_expired` (claim): when the
  *system* learned or retired a belief. Discrediting a source, retracting, and
  superseding are **epistemic** changes → system time only. They must **never**
  touch `t_valid`/`t_invalid`.

`query as-of <D>` = system time (replay to D). `query as-of <D> --world` = world time.

## Event types

| event | key payload | effect on projection |
|---|---|---|
| `seed_marker` | `note` | none — marks a log as the seed (used by fresh_start) |
| `agent_run_started` | `run_id, role, model, task` | upsert agent_runs |
| `source_registered` | `source_id, uri, title, origin, authority, credibility, retrieved_at` | insert source |
| `source_discredited` | `source_id, reason` | mark source discredited; cascade re-flags dependents |
| `source_recredited` | `source_id` | clear discredited flag |
| `claim_created` | `claim_id, assertion{s,p,o,nl}, warrant, backing[], rebuttal_conditions[], grounds[], likelihood_band, analytic_confidence, confidence_score, confidence_method, depends_on[], t_valid, t_invalid` | insert claim + depends_on edges |
| `evidence_added` | `evidence_id, claim_id, source_id, quote, locator` | insert evidence |
| `status_changed` | `claim_id, from, to, reason` | update epistemic_status (+revision) |
| `confidence_recalibrated` | `claim_id, old_score, new_score, method` | update confidence (+revision) |
| `conflict_linked` | `edge_id, thread_id, claim_a, claim_b, conflict_type, note` | conflicts_with edge + open thread |
| `resolution_updated` | `thread_id, status, note` | update thread |
| `claim_invalidated` | `claim_id, cause, new_status, invalidated_by` | set `t_expired` + status (system time) |
| `claim_superseded` | `old_claim_id, new_claim_id` | old→superseded, `supersededBy`, supersedes edge |
| `claim_retracted` | `claim_id, reason, retracted_by` | status→retracted, `t_expired` |
| `rebuttal_triggered` | `claim_id, condition, evidence` | claim→needs_review |

## Content-addressed IDs (script-computed)

- `claim_id  = clm_ + sha256(topic | norm(s) | norm(p) | norm(o))`
- `source_id = src_ + sha256(norm(uri))`

`norm` = NFC + casefold + whitespace-collapse. Same proposition → same id
(exact-normalized dedup). Paraphrase-level dedup is the reconciler agent's job,
not the script's.

## Status vocabulary & legal transitions

`hypothesized → probable → corroborated → confirmed`, plus `disputed`,
`needs_review`, `superseded`, `retracted`. `superseded` is terminal;
`retracted` is terminal except explicit **revival** (`retracted → hypothesized`)
when a proposition is re-asserted with fresh evidence. The full matrix is in
`scripts/kb/events.py` (`TRANSITIONS`) and enforced by `claims.change_status`.
