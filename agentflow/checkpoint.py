"""事件溯源式 checkpointer（SQLite，纯标准库）。

对应报告第三章「状态持久化 / 错误恢复」与差异分析第 3 点：
- 每个 thread（一次工作流执行）维护一条追加式事件历史；
- 每完成一个 super-step 写一份 checkpoint（state 快照 + 待执行的 frontier）；
- **硬约束**：恢复时从最近 checkpoint 的 frontier 继续，已完成节点绝不重跑
  —— 因为 frontier 里只存「还没跑的节点」，已落盘的 state 直接复用。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class Checkpoint:
    thread_id: str
    step: int
    state: Dict[str, Any]
    frontier: List[str]          # 下一个 super-step 要执行的节点
    status: str                  # running | interrupted | completed | failed
    interrupt_payload: Any = None
    ts: float = 0.0


class Checkpointer:
    def __init__(self, db_path: str = ":memory:"):
        # check_same_thread=False 便于线程池里读写；并发写由 _lock 串行化
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.Lock()
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS checkpoints (
                thread_id        TEXT NOT NULL,
                step             INTEGER NOT NULL,
                state            TEXT NOT NULL,
                frontier         TEXT NOT NULL,
                status           TEXT NOT NULL,
                interrupt_payload TEXT,
                ts               REAL NOT NULL,
                PRIMARY KEY (thread_id, step)
            )
            """
        )
        # 事件日志：纯追加，用于审计 / 时间旅行（对应报告的 Event History 思路）
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                thread_id TEXT NOT NULL,
                seq       INTEGER NOT NULL,
                kind      TEXT NOT NULL,
                payload   TEXT,
                ts        REAL NOT NULL,
                PRIMARY KEY (thread_id, seq)
            )
            """
        )
        self._conn.commit()

        # —— activity 缓存：以 (thread_id, node, step, activity_key) 为键缓存 LLM 等调用 —— #
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS activity_results (
                thread_id    TEXT NOT NULL,
                node         TEXT NOT NULL,
                step         INTEGER NOT NULL,
                activity_key TEXT NOT NULL,
                result       TEXT NOT NULL,
                status       TEXT NOT NULL,
                created_at   REAL NOT NULL,
                PRIMARY KEY (thread_id, node, step, activity_key)
            )
            """
        )
        self._conn.commit()

    def put_activity(self, thread_id: str, node: str, step: int,
                     activity_key: str, result: Any, status: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO activity_results VALUES (?,?,?,?,?,?,?)",
                (thread_id, node, step, activity_key,
                 json.dumps(result, ensure_ascii=False),
                 status, time.time()),
            )
            self._conn.commit()

    def get_activity(self, thread_id: str, node: str, step: int,
                     activity_key: str) -> Optional[tuple[Any, str]]:
        row = self._conn.execute(
            "SELECT result, status FROM activity_results "
            "WHERE thread_id=? AND node=? AND step=? AND activity_key=?",
            (thread_id, node, step, activity_key),
        ).fetchone()
        if row is None:
            return None
        return (json.loads(row[0]), row[1])

    def put(self, cp: Checkpoint) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO checkpoints VALUES (?,?,?,?,?,?,?)",
                (
                    cp.thread_id,
                    cp.step,
                    json.dumps(cp.state, ensure_ascii=False),
                    json.dumps(cp.frontier),
                    cp.status,
                    json.dumps(cp.interrupt_payload, ensure_ascii=False),
                    cp.ts or time.time(),
                ),
            )
            self._conn.commit()

    def latest(self, thread_id: str) -> Optional[Checkpoint]:
        row = self._conn.execute(
            "SELECT thread_id, step, state, frontier, status, interrupt_payload, ts "
            "FROM checkpoints WHERE thread_id=? ORDER BY step DESC LIMIT 1",
            (thread_id,),
        ).fetchone()
        if row is None:
            return None
        return Checkpoint(
            thread_id=row[0],
            step=row[1],
            state=json.loads(row[2]),
            frontier=json.loads(row[3]),
            status=row[4],
            interrupt_payload=json.loads(row[5]) if row[5] else None,
            ts=row[6],
        )

    def history(self, thread_id: str) -> List[Checkpoint]:
        rows = self._conn.execute(
            "SELECT thread_id, step, state, frontier, status, interrupt_payload, ts "
            "FROM checkpoints WHERE thread_id=? ORDER BY step ASC",
            (thread_id,),
        ).fetchall()
        return [
            Checkpoint(r[0], r[1], json.loads(r[2]), json.loads(r[3]), r[4],
                       json.loads(r[5]) if r[5] else None, r[6])
            for r in rows
        ]

    def log_event(self, thread_id: str, kind: str, payload: Any = None) -> None:
        with self._lock:
            seq = self._conn.execute(
                "SELECT COALESCE(MAX(seq), -1) + 1 FROM events WHERE thread_id=?",
                (thread_id,),
            ).fetchone()[0]
            self._conn.execute(
                "INSERT INTO events VALUES (?,?,?,?,?)",
                (thread_id, seq, kind, json.dumps(payload, ensure_ascii=False), time.time()),
            )
            self._conn.commit()

    def events(self, thread_id: str) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT seq, kind, payload, ts FROM events WHERE thread_id=? ORDER BY seq ASC",
            (thread_id,),
        ).fetchall()
        return [
            {"seq": r[0], "kind": r[1], "payload": json.loads(r[2]) if r[2] else None, "ts": r[3]}
            for r in rows
        ]

    def close(self) -> None:
        self._conn.close()
