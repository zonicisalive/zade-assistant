import json
import pathlib
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS history (
  id     INTEGER PRIMARY KEY,
  ts     TEXT    NOT NULL DEFAULT (datetime('now')),
  text   TEXT    NOT NULL,
  action TEXT    NOT NULL,
  source TEXT    NOT NULL CHECK (source IN ('shortcut','pattern','laya','llm')),
  ok     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS history_text ON history(text);
CREATE TABLE IF NOT EXISTS shortcuts (
  id      INTEGER PRIMARY KEY,
  phrase  TEXT    NOT NULL UNIQUE,
  actions TEXT    NOT NULL,
  uses    INTEGER NOT NULL DEFAULT 0,
  created TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS declined (
  text   TEXT NOT NULL,
  action TEXT NOT NULL,
  PRIMARY KEY (text, action)
);
CREATE TABLE IF NOT EXISTS reminders (
  id       INTEGER PRIMARY KEY,
  due      REAL    NOT NULL,          -- unix time
  message  TEXT    NOT NULL,
  repeat_s REAL                       -- NULL = once; 86400 = daily
);
CREATE TABLE IF NOT EXISTS requests (
  id    INTEGER PRIMARY KEY,
  ts    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
  heard TEXT    NOT NULL,
  reply TEXT    NOT NULL,
  route TEXT    NOT NULL,
  ms    INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS facts (
  id      INTEGER PRIMARY KEY,
  fact    TEXT NOT NULL UNIQUE,
  created TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def connect(path):
    if str(path) != ":memory:":
        path = pathlib.Path(path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.executescript(SCHEMA)
    return conn


def _key(actions):
    return json.dumps(actions, sort_keys=True)


def log(conn, text, actions, source, ok):
    conn.execute(
        "INSERT INTO history (text, action, source, ok) VALUES (?, ?, ?, ?)",
        (text, _key(actions), source, int(ok)),
    )
    conn.commit()


def should_offer(conn, text, actions, promote_after):
    k = _key(actions)
    n = conn.execute(
        "SELECT COUNT(*) FROM history WHERE text = ? AND action = ? AND ok = 1", (text, k)
    ).fetchone()[0]
    if n < promote_after:
        return False
    if conn.execute("SELECT 1 FROM shortcuts WHERE phrase = ?", (text,)).fetchone():
        return False
    return not conn.execute(
        "SELECT 1 FROM declined WHERE text = ? AND action = ?", (text, k)
    ).fetchone()


def add_shortcut(conn, phrase, actions):
    conn.execute(
        "INSERT INTO shortcuts (phrase, actions) VALUES (?, ?) "
        "ON CONFLICT(phrase) DO UPDATE SET actions = excluded.actions",
        (phrase, _key(actions)),
    )
    conn.commit()


def decline(conn, text, actions):
    conn.execute("INSERT OR IGNORE INTO declined VALUES (?, ?)", (text, _key(actions)))
    conn.commit()


def shortcuts(conn):
    return {p: json.loads(a) for p, a in conn.execute("SELECT phrase, actions FROM shortcuts")}


def use_shortcut(conn, phrase):
    conn.execute("UPDATE shortcuts SET uses = uses + 1 WHERE phrase = ?", (phrase,))
    conn.commit()


def last_actions(conn):
    row = conn.execute("SELECT action FROM history WHERE ok = 1 ORDER BY id DESC LIMIT 1").fetchone()
    return json.loads(row[0]) if row else None


def add_fact(conn, fact):
    conn.execute("INSERT OR IGNORE INTO facts (fact) VALUES (?)", (fact.strip(),))
    conn.commit()


def forget_fact(conn, query):
    if not query.strip():
        return 0
    cur = conn.execute("DELETE FROM facts WHERE fact LIKE ?", (f"%{query.strip()}%",))
    conn.commit()
    return cur.rowcount


def facts(conn, limit=50):
    return [f for (f,) in conn.execute("SELECT fact FROM facts ORDER BY id DESC LIMIT ?", (limit,))]


def add_reminder(conn, due, message, repeat_s=None):
    conn.execute("INSERT INTO reminders (due, message, repeat_s) VALUES (?, ?, ?)", (due, message, repeat_s))
    conn.commit()


def due_reminders(conn, now):
    """Messages due by `now`; one-off reminders are removed, repeating ones move to their next time."""
    rows = conn.execute("SELECT id, due, message, repeat_s FROM reminders WHERE due <= ? ORDER BY due",
                        (now,)).fetchall()
    for rid, due, _, repeat_s in rows:
        if repeat_s:
            while due <= now:
                due += repeat_s
            conn.execute("UPDATE reminders SET due = ? WHERE id = ?", (due, rid))
        else:
            conn.execute("DELETE FROM reminders WHERE id = ?", (rid,))
    conn.commit()
    return [m for _, _, m, _ in rows]


def reminders(conn):
    return conn.execute("SELECT due, message, repeat_s FROM reminders ORDER BY due").fetchall()


def cancel_reminder(conn, query):
    if not query.strip():
        return 0
    cur = conn.execute("DELETE FROM reminders WHERE message LIKE ?", (f"%{query.strip()}%",))
    conn.commit()
    return cur.rowcount


def log_request(conn, heard, reply, route, ms, keep=1000):
    conn.execute("INSERT INTO requests (heard, reply, route, ms) VALUES (?, ?, ?, ?)", (heard, reply, route, int(ms)))
    conn.execute("DELETE FROM requests WHERE id NOT IN (SELECT id FROM requests ORDER BY id DESC LIMIT ?)",
                 (max(1, int(keep)),))
    conn.commit()


def requests(conn, limit=200):
    rows = conn.execute("SELECT ts, heard, reply, route, ms FROM requests ORDER BY id DESC LIMIT ?", (limit,))
    return [dict(zip(("ts", "heard", "reply", "route", "ms"), r)) for r in rows]


def fact_rows(conn):
    return [{"id": i, "text": f} for i, f in conn.execute("SELECT id, fact FROM facts ORDER BY id DESC")]


def delete_fact(conn, fact_id):
    n = conn.execute("DELETE FROM facts WHERE id = ?", (fact_id,)).rowcount
    conn.commit()
    return n


def shortcut_rows(conn):
    rows = conn.execute("SELECT phrase, actions, uses FROM shortcuts ORDER BY uses DESC, phrase")
    return [{"phrase": p, "actions": json.loads(a), "uses": u} for p, a, u in rows]


def rename_shortcut(conn, old, new):
    n = conn.execute("UPDATE shortcuts SET phrase = ? WHERE phrase = ?", (new, old)).rowcount
    conn.commit()
    return n


def delete_shortcut(conn, phrase):
    n = conn.execute("DELETE FROM shortcuts WHERE phrase = ?", (phrase,)).rowcount
    conn.commit()
    return n


def reminder_rows(conn):
    rows = conn.execute("SELECT id, due, message, repeat_s FROM reminders ORDER BY due")
    return [{"id": i, "due": d, "message": m, "daily": bool(r)} for i, d, m, r in rows]


def delete_reminder(conn, reminder_id):
    n = conn.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,)).rowcount
    conn.commit()
    return n
