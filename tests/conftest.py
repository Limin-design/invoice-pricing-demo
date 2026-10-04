import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from invoicepricing import db  # noqa: E402


@pytest.fixture
def store(tmp_path):
    """A tiny store: one normal product, one generic, one bought from two suppliers."""
    path = str(tmp_path / "store.sqlite")
    conn = db.create(path)
    conn.execute("BEGIN")
    conn.execute("INSERT INTO families VALUES ('Snacks', 30)")
    rows = [
        ("P1", "Crisps 150g", "Snacks", 23, 1.00, 30.0, db.shelf_price(1.00, 30, 23), 1),
        ("P2", "Biscuits Assorted", "Snacks", 23, 0.95, 30.0, db.shelf_price(0.95, 30, 23), 40),
        ("P3", "Gummies 90g", "Snacks", 23, 0.80, 30.0, db.shelf_price(0.80, 30, 23), 1),
    ]
    for r in rows:
        conn.execute("INSERT INTO products VALUES (?,?,?,?,?,?,?,?, '2025-01-01')", r)
    links = [("509000001", "1001", "P1"), ("509000001", "1002", "P2"),
             ("509000001", "1003", "P3"), ("509000002", "9003", "P3")]
    for nif, code, prod in links:
        conn.execute("INSERT INTO supplier_links VALUES (?,?,?, 'owner')", (nif, code, prod))
    conn.execute("COMMIT")
    conn.close()
    return path


def make_qr(lines_by_rate, supplier="509000001", customer="199999999"):
    """Build a valid QR text for the given {rate: base} amounts."""
    fields = [f"A:{supplier}", f"B:{customer}", "C:PT", "D:FT", "E:N", "F:20260930", "G:FT 1/1"]
    names = {6: ("I3", "I4"), 13: ("I5", "I6"), 23: ("I7", "I8")}
    total_vat = 0.0
    for rate, base in lines_by_rate.items():
        vat = round(base * rate / 100, 2)
        total_vat += vat
        fields += [f"{names[rate][0]}:{base:.2f}", f"{names[rate][1]}:{vat:.2f}"]
    total = sum(lines_by_rate.values()) + total_vat
    fields += [f"N:{total_vat:.2f}", f"O:{total:.2f}"]
    return "*".join(fields)
