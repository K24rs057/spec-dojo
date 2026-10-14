"""SQLite への保存(基本設計書 3 テーブル定義)。

決定事項は「最新だけを持つ decisions」と「変更を書き足す decision_history」に分け、
両方への書き込みは必ず1つのトランザクションで行う。
"""
import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

import rules

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  current_phase TEXT NOT NULL,
  archived_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id),
  item_key TEXT NOT NULL,
  value TEXT,
  reason TEXT,
  source TEXT,
  status TEXT NOT NULL DEFAULT 'undecided',
  reviewed INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL,
  UNIQUE(project_id, item_key)
);
CREATE TABLE IF NOT EXISTS decision_history (
  id INTEGER PRIMARY KEY,
  decision_id INTEGER NOT NULL REFERENCES decisions(id),
  old_value TEXT,
  new_value TEXT,
  reason TEXT,
  changed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS turns (
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id),
  item_key TEXT,
  mode TEXT NOT NULL,
  ai_text TEXT,
  question_json TEXT,
  selected_json TEXT,
  free_input TEXT,
  advice_text TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS review_findings (
  id INTEGER PRIMARY KEY,
  decision_id INTEGER NOT NULL REFERENCES decisions(id),
  layer INTEGER NOT NULL,
  viewpoint_id TEXT,
  message TEXT NOT NULL,
  resolved INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS glossary (
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id),
  term TEXT NOT NULL,
  explanation TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(project_id, term)
);
CREATE TABLE IF NOT EXISTS exports (
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id),
  doc_type TEXT NOT NULL,
  version TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  exported_at TEXT NOT NULL
);
"""


def now():
    return datetime.now().isoformat(timespec="seconds")


class Store:
    def __init__(self, path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.lock = threading.RLock()

    # ---- projects ----
    def create_project(self, name):
        t = now()
        with self.tx():
            cur = self.conn.execute(
                "INSERT INTO projects(name, current_phase, created_at, updated_at) VALUES (?,?,?,?)",
                (name, rules.phase_keys()[0], t, t))
            pid = cur.lastrowid
            for it in rules.items():
                self.conn.execute(
                    "INSERT INTO decisions(project_id, item_key, updated_at) VALUES (?,?,?)",
                    (pid, it["key"], t))
        return pid

    def list_projects(self, archived=False):
        cond = "IS NOT NULL" if archived else "IS NULL"
        rows = self.conn.execute(
            f"SELECT * FROM projects WHERE archived_at {cond} ORDER BY updated_at DESC").fetchall()
        out = []
        for r in rows:
            p = dict(r)
            phase_items = [it["key"] for it in rules.items(p["current_phase"])]
            done = self.conn.execute(
                f"SELECT COUNT(*) FROM decisions WHERE project_id=? AND status='decided' "
                f"AND item_key IN ({','.join('?' * len(phase_items))})",
                (p["id"], *phase_items)).fetchone()[0]
            p["progress"] = f"{done}/{len(phase_items)}"
            out.append(p)
        return out

    def get_project(self, pid):
        r = self.conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
        return dict(r) if r else None

    def set_archived(self, pid, archived):
        self.conn.execute("UPDATE projects SET archived_at=? WHERE id=?",
                          (now() if archived else None, pid))

    def set_phase(self, pid, phase):
        self.conn.execute("UPDATE projects SET current_phase=?, updated_at=? WHERE id=?", (phase, now(), pid))

    def touch(self, pid):
        self.conn.execute("UPDATE projects SET updated_at=? WHERE id=?", (now(), pid))

    # ---- decisions ----
    def decisions(self, pid):
        rows = self.conn.execute("SELECT * FROM decisions WHERE project_id=?", (pid,)).fetchall()
        return {r["item_key"]: dict(r) for r in rows}

    def decision(self, pid, item_key):
        r = self.conn.execute("SELECT * FROM decisions WHERE project_id=? AND item_key=?",
                              (pid, item_key)).fetchone()
        return dict(r) if r else None

    def decide(self, pid, item_key, value, reason="", source=""):
        """決定する/変更する。前の内容は履歴に残し、依存している決定済み項目に「要確認」を付ける。

        戻り値は「要確認」を付けた項目のキー。
        """
        d = self.decision(pid, item_key)
        if d is None:
            raise KeyError(item_key)
        t = now()
        marked = []
        with self.tx():
            self.conn.execute(
                "UPDATE decisions SET value=?, reason=?, source=?, status='decided', reviewed=0, updated_at=? "
                "WHERE id=?", (value, reason, source, t, d["id"]))
            self.conn.execute(
                "INSERT INTO decision_history(decision_id, old_value, new_value, reason, changed_at) "
                "VALUES (?,?,?,?,?)", (d["id"], d["value"], value, reason, t))
            changed = d["status"] != "undecided" and d["value"] != value
            if changed:
                for dep in rules.dependents(item_key):
                    cur = self.conn.execute(
                        "UPDATE decisions SET status='needs_check', updated_at=? "
                        "WHERE project_id=? AND item_key=? AND status='decided'", (t, pid, dep))
                    if cur.rowcount:
                        marked.append(dep)
            self.conn.execute("UPDATE projects SET updated_at=? WHERE id=?", (t, pid))
        return marked

    def keep(self, pid, item_key):
        """「このままでいい」:要確認を外す。"""
        self.conn.execute(
            "UPDATE decisions SET status='decided', updated_at=? WHERE project_id=? AND item_key=? "
            "AND status='needs_check'", (now(), pid, item_key))

    def history(self, pid):
        return [dict(r) for r in self.conn.execute(
            "SELECT h.*, d.item_key FROM decision_history h JOIN decisions d ON d.id=h.decision_id "
            "WHERE d.project_id=? ORDER BY h.id", (pid,))]

    def unreviewed(self, pid):
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM decisions WHERE project_id=? AND status!='undecided' AND reviewed=0", (pid,))]

    # ---- turns ----
    def add_turn(self, pid, item_key, mode, ai_text, question=None):
        cur = self.conn.execute(
            "INSERT INTO turns(project_id, item_key, mode, ai_text, question_json, created_at) VALUES (?,?,?,?,?,?)",
            (pid, item_key, mode, ai_text, json.dumps(question, ensure_ascii=False) if question else None, now()))
        return cur.lastrowid

    def turn(self, tid):
        r = self.conn.execute("SELECT * FROM turns WHERE id=?", (tid,)).fetchone()
        if not r:
            return None
        t = dict(r)
        t["question"] = json.loads(t["question_json"]) if t["question_json"] else None
        t["selected"] = json.loads(t["selected_json"]) if t["selected_json"] else []
        return t

    def answer_turn(self, tid, selected, free_input, advice):
        self.conn.execute("UPDATE turns SET selected_json=?, free_input=?, advice_text=? WHERE id=?",
                          (json.dumps(selected, ensure_ascii=False), free_input, advice, tid))

    def recent_turns(self, pid, limit=10):
        rows = self.conn.execute("SELECT * FROM turns WHERE project_id=? ORDER BY id DESC LIMIT ?",
                                 (pid, limit)).fetchall()
        return [dict(r) for r in reversed(rows)]

    def open_question(self, pid, item_key):
        """まだ答えていない最新の質問(モードを切り替えても取っておく)。"""
        r = self.conn.execute(
            "SELECT id FROM turns WHERE project_id=? AND item_key=? AND mode='guided' "
            "AND question_json IS NOT NULL AND selected_json IS NULL ORDER BY id DESC LIMIT 1",
            (pid, item_key)).fetchone()
        return self.turn(r["id"]) if r else None

    # ---- review ----
    def add_findings(self, pid, findings):
        t = now()
        decs = self.decisions(pid)
        with self.tx():
            for f in findings:
                d = decs.get(f["item_key"])
                if not d:
                    continue
                self.conn.execute(
                    "INSERT INTO review_findings(decision_id, layer, viewpoint_id, message, created_at) "
                    "VALUES (?,?,?,?,?)", (d["id"], f["layer"], f.get("viewpoint_id"), f["message"], t))

    def mark_reviewed(self, pid, item_keys):
        for k in item_keys:
            self.conn.execute("UPDATE decisions SET reviewed=1 WHERE project_id=? AND item_key=?", (pid, k))

    def findings(self, pid, only_open=True):
        cond = "AND f.resolved=0" if only_open else ""
        return [dict(r) for r in self.conn.execute(
            f"SELECT f.*, d.item_key FROM review_findings f JOIN decisions d ON d.id=f.decision_id "
            f"WHERE d.project_id=? {cond} ORDER BY f.id", (pid,))]

    def resolve_finding(self, fid):
        self.conn.execute("UPDATE review_findings SET resolved=1 WHERE id=?", (fid,))

    # ---- glossary ----
    def glossary(self, pid):
        return [dict(r) for r in self.conn.execute(
            "SELECT term, explanation FROM glossary WHERE project_id=? ORDER BY id", (pid,))]

    def add_terms(self, pid, terms):
        """一度作った説明は上書きしない(説明がぶれないように使い回す)。"""
        for t in terms or []:
            if t.get("term") and t.get("explanation"):
                self.conn.execute(
                    "INSERT OR IGNORE INTO glossary(project_id, term, explanation, created_at) VALUES (?,?,?,?)",
                    (pid, t["term"], t["explanation"], now()))

    # ---- exports ----
    def last_export(self, pid, doc_type):
        r = self.conn.execute("SELECT * FROM exports WHERE project_id=? AND doc_type=? ORDER BY id DESC LIMIT 1",
                              (pid, doc_type)).fetchone()
        return dict(r) if r else None

    def exports(self, pid, doc_type):
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM exports WHERE project_id=? AND doc_type=? ORDER BY id", (pid, doc_type))]

    def add_export(self, pid, doc_type, version, content_hash):
        self.conn.execute("INSERT INTO exports(project_id, doc_type, version, content_hash, exported_at) "
                          "VALUES (?,?,?,?,?)", (pid, doc_type, version, content_hash, now()))

    # ---- transaction ----
    def tx(self):
        return _Tx(self.conn, self.lock)


class _Tx:
    """BEGIN〜COMMIT。途中で例外が出たら全部なかったことにする。"""

    def __init__(self, conn, lock):
        self.conn = conn
        self.lock = lock

    def __enter__(self):
        self.lock.acquire()
        self.conn.execute("BEGIN")

    def __exit__(self, exc_type, exc, tb):
        try:
            self.conn.execute("ROLLBACK" if exc_type else "COMMIT")
        finally:
            self.lock.release()
        return False
