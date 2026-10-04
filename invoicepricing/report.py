"""Read-only margin report: where is the store losing money on prices?

Ranked by money at stake, not by number of cases: a product sold below cost 500
times a month matters more than one sold twice (the audit idea of materiality).
"""

import csv
import os
from datetime import date, timedelta
from statistics import median


def _window(as_of, days):
    end = date.fromisoformat(as_of)
    return (end - timedelta(days=days - 1)).isoformat(), end.isoformat()


def below_cost(conn, as_of, days=90):
    """Products whose shelf price, without VAT, is under their cost; with the money lost."""
    start, end = _window(as_of, days)
    rows = conn.execute(
        """
        SELECT p.code, p.name, p.family, p.cost, p.price, p.vat,
               COALESCE(SUM(s.qty), 0) AS qty
        FROM products p LEFT JOIN sales s
             ON s.product_code = p.code AND s.day BETWEEN ? AND ?
        WHERE p.price / (1 + p.vat / 100.0) < p.cost
        GROUP BY p.code
        """,
        (start, end),
    ).fetchall()
    out = []
    for r in rows:
        net_price = r["price"] / (1 + r["vat"] / 100)
        out.append({
            "code": r["code"], "name": r["name"], "family": r["family"],
            "cost": r["cost"], "price": r["price"], "qty_sold": r["qty"],
            "loss_per_unit": round(r["cost"] - net_price, 4),
            "money_lost": round(r["qty"] * (r["cost"] - net_price), 2),
        })
    return sorted(out, key=lambda x: x["money_lost"], reverse=True)


def margin_outliers(conn, low=10, high=25):
    """Products whose margin is far from the median of their own family."""
    by_family = {}
    for r in conn.execute("SELECT code, name, family, margin FROM products"):
        by_family.setdefault(r["family"], []).append(r)
    out = []
    for family, rows in by_family.items():
        mid = median(r["margin"] for r in rows)
        for r in rows:
            if r["margin"] < mid - low or r["margin"] > mid + high:
                out.append({"code": r["code"], "name": r["name"], "family": family,
                            "margin": r["margin"], "family_median": round(mid, 1)})
    return sorted(out, key=lambda x: x["margin"] - x["family_median"])


def stale_cost_share(conn, as_of, years=2, days=365):
    """Share of sales coming from products whose cost was last updated more than N years ago."""
    start, end = _window(as_of, days)
    cutoff = (date.fromisoformat(as_of) - timedelta(days=365 * years)).isoformat()
    r = conn.execute(
        """
        SELECT SUM(s.revenue) AS total,
               SUM(CASE WHEN p.cost_date < ? THEN s.revenue ELSE 0 END) AS stale
        FROM sales s JOIN products p ON p.code = s.product_code
        WHERE s.day BETWEEN ? AND ?
        """,
        (cutoff, start, end),
    ).fetchone()
    return round((r["stale"] or 0) / r["total"], 3) if r["total"] else 0.0


def _dump(path, rows, header=None):
    rows = [dict(r) for r in rows]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=header or list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def export_for_bi(conn, out_dir, as_of):
    """Star schema for Power BI: one fact table and its dimensions, plus the report tables."""
    os.makedirs(out_dir, exist_ok=True)
    _dump(os.path.join(out_dir, "dim_product.csv"),
          conn.execute("SELECT code, name, family, vat, cost, margin, price, barcodes, cost_date FROM products"))
    _dump(os.path.join(out_dir, "dim_family.csv"), conn.execute("SELECT * FROM families"))
    _dump(os.path.join(out_dir, "fact_sales.csv"),
          conn.execute("SELECT day, product_code, qty, revenue FROM sales ORDER BY day"))
    history = conn.execute("SELECT * FROM price_history ORDER BY changed_at").fetchall()
    _dump(os.path.join(out_dir, "fact_price_changes.csv"), history,
          header=["changed_at", "product_code", "old_cost", "new_cost", "old_price", "new_price", "reason"])
    losses = below_cost(conn, as_of)
    if losses:
        _dump(os.path.join(out_dir, "below_cost_90d.csv"), losses)
    return out_dir
