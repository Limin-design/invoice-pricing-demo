"""Price rules and safe writes. Several cases reproduce bugs found by the two audits."""

import json
import sqlite3

import pytest

from invoicepricing import apply, lines, pricing
from invoicepricing.db import connect, shelf_price

NIF = "509000001"


def propose(store, rows):
    parsed, _ = lines.parse_invoice(rows)
    conn = connect(store)
    out = pricing.propose(conn, NIF, parsed)
    conn.close()
    return out


def product(store, code):
    conn = sqlite3.connect(store)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM products WHERE code=?", (code,)).fetchone()
    conn.close()
    return row


def test_cost_up_keeps_the_margin(store):
    (p,) = propose(store, ["1001 CRISPS 10 1,100 11,00 23"])
    assert p.action == "cost_up"
    assert p.new_margin == 30.0
    assert p.new_price == shelf_price(1.10, 30, 23)


def test_cost_down_keeps_the_shelf_price(store):
    (p,) = propose(store, ["1001 CRISPS 10 0,900 9,00 23"])
    assert p.action == "cost_down"
    assert p.new_price == p.old_price
    assert p.new_margin > 30


def test_generic_product_needs_a_person(store):
    # Regression: one line set the cost of a 325-barcode generic product (margin 20% -> 79%).
    (p,) = propose(store, ["1002 BISCUITS 12 0,700 8,40 23"])
    assert p.action == "needs_decision"


def test_two_suppliers_cost_does_not_go_down_by_itself(store):
    (p,) = propose(store, ["1003 GUMMIES 10 0,700 7,00 23"])
    assert p.action == "needs_decision"


def test_wrong_vat_is_refused(store):
    (p,) = propose(store, ["1001 CRISPS 10 1,100 11,00 6"])
    assert p.action == "refused"


def test_two_lines_one_product_writes_the_confirmed_line(store):
    # Regression (critical, 29/09 audit): the person confirmed the second of two lines and
    # the writer stored the first line's cost (1.06 instead of 1.20).
    proposals = propose(store, ["1001 CRISPS 6 1,060 6,36 23", "1001 CRISPS 4 1,200 4,80 23"])
    assert [p.action for p in proposals] == ["needs_decision", "needs_decision"]
    pricing.decide(proposals, row=1, accept=True)
    for p in proposals:
        p.approved = p.changes_price
    plan, fp, _ = apply.simulate(store, proposals)
    assert len(plan) == 1 and plan[0]["after"]["cost"] == 1.20
    apply.write(store, proposals, fp, backup_dir=store + "_bk", log_dir=store + "_logs")
    assert product(store, "P1")["cost"] == 1.20


def test_write_needs_the_simulated_plan(store):
    (p,) = propose(store, ["1001 CRISPS 10 1,100 11,00 23"])
    p.approved = True
    _, fp, _ = apply.simulate(store, [p])
    p.new_price += 0.10  # the proposal changes between simulation and write
    with pytest.raises(apply.PlanChanged):
        apply.write(store, [p], fp, backup_dir=store + "_bk", log_dir=store + "_logs")
    assert product(store, "P1")["cost"] == 1.00


def test_poison_record_changed_rolls_back_everything(store):
    """The 'poison' test: another program edits a product after the proposal. Nothing is written."""
    proposals = propose(store, ["1001 CRISPS 10 1,100 11,00 23", "1003 GUMMIES 10 0,900 9,00 23"])
    for p in proposals:
        p.approved = True
    _, fp, _ = apply.simulate(store, proposals)
    conn = sqlite3.connect(store)
    conn.execute("UPDATE products SET price = 2.49 WHERE code='P3'")  # someone edits P3 by hand
    conn.commit()
    conn.close()
    with pytest.raises(apply.RecordChanged):
        apply.write(store, proposals, fp, backup_dir=store + "_bk", log_dir=store + "_logs")
    assert product(store, "P1")["cost"] == 1.00  # P1 was valid, but the whole write rolled back


def test_control_write_backup_log_and_undo(store, tmp_path):
    (p,) = propose(store, ["1001 CRISPS 10 1,100 11,00 23"])
    p.approved = True
    _, fp, _ = apply.simulate(store, [p])
    log = apply.write(store, [p], fp, backup_dir=str(tmp_path / "bk"), log_dir=str(tmp_path / "logs"))
    assert product(store, "P1")["cost"] == 1.10
    with open(log, encoding="utf-8") as f:
        assert json.load(f)["status"] == "written"
    apply.undo(store, log)
    assert product(store, "P1")["cost"] == 1.00


def test_undo_accepts_a_log_left_as_writing(store, tmp_path):
    # Regression: a write that crashed after the commit left the log as "writing", and
    # undo refused exactly the case it exists for.
    (p,) = propose(store, ["1001 CRISPS 10 1,100 11,00 23"])
    p.approved = True
    _, fp, _ = apply.simulate(store, [p])
    log = apply.write(store, [p], fp, backup_dir=str(tmp_path / "bk"), log_dir=str(tmp_path / "logs"))
    with open(log, encoding="utf-8") as f:
        data = json.load(f)
    data["status"] = "writing"
    with open(log, "w", encoding="utf-8") as f:
        json.dump(data, f)
    apply.undo(store, log)
    assert product(store, "P1")["cost"] == 1.00


def test_undo_never_overwrites_a_later_change(store, tmp_path):
    (p,) = propose(store, ["1001 CRISPS 10 1,100 11,00 23"])
    p.approved = True
    _, fp, _ = apply.simulate(store, [p])
    log = apply.write(store, [p], fp, backup_dir=str(tmp_path / "bk"), log_dir=str(tmp_path / "logs"))
    conn = sqlite3.connect(store)
    conn.execute("UPDATE products SET price = 1.99 WHERE code='P1'")
    conn.commit()
    conn.close()
    with pytest.raises(apply.RecordChanged):
        apply.undo(store, log)
    assert product(store, "P1")["price"] == 1.99


def _write_invoice(path, rows, bases):
    from conftest import make_qr
    path.write_text("QR: " + make_qr(bases) + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return str(path)


def test_summary_is_read_only_and_flags_what_needs_a_person(store, tmp_path):
    import hashlib
    from invoicepricing.cli import summarize
    before = hashlib.sha256(open(store, "rb").read()).hexdigest()
    clean = _write_invoice(tmp_path / "a.txt", ["1001 CRISPS 150G 10 1,10 11,00 23"], {23: 11.0})
    generic = _write_invoice(tmp_path / "b.txt", ["1002 BISCUITS ASSORTED 10 1,05 10,50 23"], {23: 10.5})
    s1, s2 = summarize(store, clean), summarize(store, generic)
    assert s1["proven"] and s1["ready_for_one_tap_approval"] and s1["actions"] == {"cost_up": 1}
    assert s2["proven"] and not s2["ready_for_one_tap_approval"] and s2["needs_person"]
    assert hashlib.sha256(open(store, "rb").read()).hexdigest() == before  # nothing was written
