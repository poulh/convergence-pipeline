"""Database access, and the rules the tools read back out of it.

No rule lives in this file. Anything a tool enforces — who may decide a kind of
requirement, what an answer must carry, who owns a duty, when a thread has
stalled — is read from tables that cp-init seeded from pipeline.yaml.
"""

import os
import sqlite3
import sys
from datetime import datetime, timezone

HUMAN_ID = 1


class Refused(Exception):
    """A tool refused the call because it breaks a pipeline rule."""


def default_db():
    return os.environ.get("CP_DB", "project.db")


def connect(path, must_exist=True):
    if must_exist and not os.path.exists(path):
        raise Refused(f"no database at {path} — run cp-init first")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------------ lookups

def agent(conn, ident):
    """Find an agent by role, id or name. Roles are what tools should use."""
    if ident is None:
        raise Refused("no agent given")
    ident = str(ident)
    row = conn.execute("SELECT * FROM agents WHERE role = ?", (ident.lower(),)).fetchone()
    if row is None and ident.isdigit():
        row = conn.execute("SELECT * FROM agents WHERE id = ?", (int(ident),)).fetchone()
    if row is None:
        row = conn.execute("SELECT * FROM agents WHERE name = ?", (ident,)).fetchone()
    if row is None:
        known = ", ".join(r["role"] for r in conn.execute("SELECT role FROM agents ORDER BY id"))
        raise Refused(f"no agent '{ident}'. Known roles: {known}")
    return row


def require_active(conn, row, what="act"):
    if not row["active"]:
        raise Refused(
            f"{row['name']} is not in this project yet, so cannot {what}. "
            f"Use cp-staff request --agent {row['role']} to ask the human."
        )
    return row


def state(conn):
    return conn.execute(
        """SELECT s.*, p.key AS phase_key, p.name AS phase_name, p.ends_when
             FROM project_state s JOIN phases p ON p.number = s.phase WHERE s.id = 1"""
    ).fetchone()


def vocab(conn, table):
    return conn.execute(f"SELECT * FROM {table} ORDER BY seq").fetchall()


def term(conn, table, value, label):
    """One vocabulary value, with the rules it carries."""
    row = conn.execute(f"SELECT * FROM {table} WHERE value = ?", (value,)).fetchone()
    if row is None:
        values = [r["value"] for r in vocab(conn, table)]
        raise Refused(f"{label} '{value}' is not valid. Choose one of: {', '.join(values)}")
    return row


def policy(conn, key, default=None):
    row = conn.execute("SELECT value FROM policy WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    value = row["value"]
    return int(value) if str(value).lstrip("-").isdigit() else value


def duties_of(conn, agent_id):
    return conn.execute(
        """SELECT d.duty, d.relation, t.description
             FROM agent_duties d JOIN duties t ON t.value = d.duty
            WHERE d.agent_id = ? ORDER BY d.relation, d.duty""",
        (agent_id,),
    ).fetchall()


def phases_of(conn, agent_id):
    return [r["phase"] for r in conn.execute(
        "SELECT phase FROM agent_phases WHERE agent_id = ? ORDER BY phase", (agent_id,))]


def who(conn, duty, relation):
    """The agents holding a duty, e.g. who owns 'milestones'."""
    return conn.execute(
        """SELECT a.* FROM agent_duties d JOIN agents a ON a.id = d.agent_id
            WHERE d.duty = ? AND d.relation = ? ORDER BY a.id""",
        (duty, relation),
    ).fetchall()


def require_duty(conn, agent_row, duty, relation, what):
    if agent_row["id"] == HUMAN_ID:
        return
    held = conn.execute(
        "SELECT 1 FROM agent_duties WHERE agent_id = ? AND duty = ? AND relation = ?",
        (agent_row["id"], duty, relation),
    ).fetchone()
    if held:
        return
    holders = who(conn, duty, relation)
    names = ", ".join(h["role"] for h in holders) or "nobody in this roster"
    verb = {"owns": "own", "rules_on": "rule on", "reviews": "review",
            "checks": "check"}.get(relation, relation)
    raise Refused(f"{agent_row['name']} does not {verb} '{duty}', so cannot {what}. "
                  f"That is: {names}")


def require_phase(conn, agent_row, what):
    current = state(conn)["phase"]
    if agent_row["id"] == HUMAN_ID:
        return
    if current not in phases_of(conn, agent_row["id"]):
        raise Refused(f"{agent_row['name']} does not take part in phase {current}, so cannot {what}")


def requirement(conn, req_id):
    row = conn.execute("SELECT * FROM requirements WHERE id = ?", (req_id,)).fetchone()
    if row is None:
        raise Refused(f"no requirement R-{req_id}")
    return row


def concern(conn, concern_id):
    row = conn.execute("SELECT * FROM concerns WHERE id = ?", (concern_id,)).fetchone()
    if row is None:
        raise Refused(f"no concern C-{concern_id}")
    return row


def deliverable(conn, deliverable_id):
    row = conn.execute("SELECT * FROM deliverables WHERE id = ?", (deliverable_id,)).fetchone()
    if row is None:
        raise Refused(f"no deliverable D-{deliverable_id}")
    return row


def milestone(conn, milestone_id):
    row = conn.execute("SELECT * FROM milestones WHERE id = ?", (milestone_id,)).fetchone()
    if row is None:
        raise Refused(f"no milestone M-{milestone_id}")
    return row


# ------------------------------------------------------------------ writing

def record_event(conn, req_id, actor_id, from_status, to_status, reason=None, concern_id=None):
    """Append to requirement_events and move the project's change mark.

    The event id is the change mark: an agent that has read up to event 42 can
    be shown exactly what happened after it.
    """
    cur = conn.execute(
        """INSERT INTO requirement_events
             (requirement_id, round, actor, from_status, to_status, reason, concern_id)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (req_id, state(conn)["round"], actor_id, from_status, to_status, reason, concern_id),
    )
    conn.execute(
        "UPDATE project_state SET change_mark = ?, updated_at = ? WHERE id = 1",
        (cur.lastrowid, now()),
    )
    return cur.lastrowid


def pause_reason(conn):
    """What is stopping the loop, if anything."""
    return conn.execute(
        """SELECT c.id, c.kind, a.name AS raiser
             FROM concerns c JOIN agents a ON a.id = c.raised_by
            WHERE c.addressed_to = ? AND c.status = 'open'
            ORDER BY c.id""",
        (HUMAN_ID,),
    ).fetchall()


def outstanding(conn, agent_row):
    """Concerns this agent owes an answer to."""
    return conn.execute(
        """SELECT c.*, a.name AS raiser_name
             FROM concerns c JOIN agents a ON a.id = c.raised_by
            WHERE c.addressed_to = ? AND c.status = 'open'
              AND NOT EXISTS (
                    SELECT 1 FROM answers w
                     WHERE w.concern_id = c.id AND w.answered_by = c.addressed_to
                       AND (w.satisfied IS NULL OR w.satisfied = 1)
                       AND w.id = (SELECT MAX(id) FROM answers w2
                                    WHERE w2.concern_id = c.id AND w2.answered_by = c.addressed_to))
            ORDER BY c.id""",
        (agent_row["id"],),
    ).fetchall()


def to_review(conn, agent_row):
    """Answers to this agent's own concerns that it has not judged yet."""
    return conn.execute(
        """SELECT w.*, c.body AS concern_body, c.kind AS concern_kind, g.name AS answerer
             FROM answers w
             JOIN concerns c ON c.id = w.concern_id
             JOIN agents g ON g.id = w.answered_by
            WHERE c.raised_by = ? AND w.satisfied IS NULL
            ORDER BY w.id""",
        (agent_row["id"],),
    ).fetchall()


def changes_since(conn, mark):
    return conn.execute(
        """SELECT e.*, r.kind, r.statement, g.name AS actor_name
             FROM requirement_events e
             JOIN requirements r ON r.id = e.requirement_id
             JOIN agents g ON g.id = e.actor
            WHERE e.id > ?
            ORDER BY e.id""",
        (mark,),
    ).fetchall()


def stalled(conn):
    """Threads the scribe should hand to the human, per the policy thresholds."""
    replies = policy(conn, "stall_replies", 4)
    rounds_open = policy(conn, "stall_rounds_open", 3)
    current = state(conn)["round"]
    return conn.execute(
        """SELECT c.id, c.kind, c.round, COUNT(w.id) AS replies,
                  MAX(COALESCE(w.round, c.round)) AS last_round
             FROM concerns c LEFT JOIN answers w ON w.concern_id = c.id
            WHERE c.status = 'open' AND c.addressed_to <> ?
            GROUP BY c.id
           HAVING replies >= ? OR (? - last_round) >= ?
            ORDER BY c.id""",
        (HUMAN_ID, replies, current, rounds_open),
    ).fetchall()


def fail(exc):
    print(f"refused: {exc}", file=sys.stderr)
    return 2
