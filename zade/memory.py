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
