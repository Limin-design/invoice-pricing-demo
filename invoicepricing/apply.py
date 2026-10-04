"""Write approved prices. The only module that writes to the products table.

Guarantees, each one added after a real failure or an audit finding:
1. Simulate by default. Writing needs the fingerprint of the plan that was simulated,
   so what gets written is exactly what the person saw.
2. A backup is taken and opened before writing, and its row count is checked.
3. The undo log, with the values before, is written before the commit.
4. One transaction; each row is updated only if it still has the values the plan was
   built from (compare-and-swap on every column read). One mismatch rolls back everything.
5. Undo uses the same compare-and-swap, so it never overwrites a later change.
"""

import hashlib
import json
import os
import sqlite3
from datetime import datetime

from .db import connect


class PlanChanged(RuntimeError):
    """The plan to write is not the one that was simulated."""


class RecordChanged(RuntimeError):
    """A product changed after the proposal; nothing was written."""


def build_plan(proposals):
    plan = []
    for p in proposals:
        if not (p.approved and p.changes_price):
            continue
        plan.append({
            "code": p.product_code,
            "before": {"cost": p.old_cost, "margin": p.old_margin, "price": p.old_price, "vat": p.vat},
            "after": {"cost": p.new_cost, "margin": p.new_margin, "price": p.new_price, "vat": p.vat},
            "reason": p.action,
        })
    return plan


def fingerprint(plan):
    return hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()[:16]


def simulate(db_path, proposals):
    """Return (plan, fingerprint, stale codes) without writing anything."""
    plan = build_plan(proposals)
    conn = connect(db_path)
    stale = [item["code"] for item in plan if not _matches(conn, item["code"], item["before"])]
    conn.close()
    return plan, fingerprint(plan), stale


def _matches(conn, code, values):
    row = conn.execute("SELECT cost, margin, price, vat FROM products WHERE code=?", (code,)).fetchone()
    return row is not None and all(row[k] == v for k, v in values.items())


def _write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)  # atomic: a crash leaves the old file or the new one, never half


def _verified_backup(db_path, backup_dir):
    os.makedirs(backup_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    target = os.path.join(backup_dir, f"backup_{stamp}.sqlite")
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(target)
    src.backup(dst)
    n_src = src.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    n_dst = dst.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    src.close()
    dst.close()
    if n_src != n_dst:
        raise RuntimeError(f"backup check failed: {n_dst} products in the copy, {n_src} in the database")
    return target


_CAS_UPDATE = (
    "UPDATE products SET cost=?, margin=?, price=?, cost_date=? "
    "WHERE code=? AND cost=? AND margin=? AND price=? AND vat=?"
)


def write(db_path, proposals, expected_fingerprint, backup_dir="backups", log_dir="logs"):
    plan = build_plan(proposals)
    if fingerprint(plan) != expected_fingerprint:
        raise PlanChanged("the proposal changed after the simulation: simulate again")
    if not plan:
        return None

    backup = _verified_backup(db_path, backup_dir)
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"write_{datetime.now():%Y%m%d_%H%M%S_%f}.json")
    log = {"status": "writing", "backup": backup, "plan": plan, "at": datetime.now().isoformat()}
    _write_json(log_path, log)  # before the commit: a crash after it still leaves a way back

    now = datetime.now().isoformat(timespec="seconds")
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        for item in plan:
            a, b = item["after"], item["before"]
            cur = conn.execute(_CAS_UPDATE, (a["cost"], a["margin"], a["price"], now[:10],
                                             item["code"], b["cost"], b["margin"], b["price"], b["vat"]))
            if cur.rowcount != 1:
                raise RecordChanged(f"{item['code']} changed since the proposal")
            conn.execute(
                "INSERT INTO price_history VALUES (?,?,?,?,?,?,?)",
                (now, item["code"], b["cost"], a["cost"], b["price"], a["price"], item["reason"]),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        log["status"] = "aborted"
        _write_json(log_path, log)
        raise
    finally:
        conn.close()

    log["status"] = "written"
    _write_json(log_path, log)
    return log_path


def undo(db_path, log_path):
    """Put the values before back, only where the values after are still there.

    A log left as "writing" (crash or timeout after the commit) can be undone too:
    the compare-and-swap refuses if the write never happened.
    """
    with open(log_path, encoding="utf-8") as f:
        log = json.load(f)
    if log["status"] not in ("written", "writing"):
        raise RuntimeError(f"nothing to undo: the log says '{log['status']}'")

    now = datetime.now().isoformat(timespec="seconds")
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        for item in log["plan"]:
            a, b = item["after"], item["before"]
            cur = conn.execute(_CAS_UPDATE, (b["cost"], b["margin"], b["price"], now[:10],
                                             item["code"], a["cost"], a["margin"], a["price"], a["vat"]))
            if cur.rowcount != 1:
                raise RecordChanged(f"{item['code']} changed after the write; undo it by hand")
            conn.execute(
                "INSERT INTO price_history VALUES (?,?,?,?,?,?,?)",
                (now, item["code"], a["cost"], b["cost"], a["price"], b["price"], "undo"),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()

    log["status"] = "undone"
    _write_json(log_path, log)
