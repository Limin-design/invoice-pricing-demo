"""QR, line parsing and proof. Several cases are regressions of real findings."""

import pytest

from conftest import make_qr
from invoicepricing import lines, proof, qr


def test_qr_parses_and_adds_up():
    q = qr.parse(make_qr({6: 100.0, 23: 50.0}))
    assert q.bases == {6: 100.0, 23: 50.0}
    assert q.total == pytest.approx(167.5)


def test_qr_rejects_html_in_tax_number():
    # Regression: a forged QR carried HTML in the supplier NIF and reached the app unescaped.
    text = make_qr({23: 10.0}).replace("A:509000001", "A:<img src=x onerror=alert(1)>")
    with pytest.raises(qr.QRError):
        qr.parse(text)


def test_qr_rejects_totals_that_do_not_add_up():
    text = make_qr({23: 10.0}).replace("O:12.30", "O:99.99")
    with pytest.raises(qr.QRError):
        qr.parse(text)


def test_row_closes_the_account():
    ln = lines.parse_row(0, "212296 CHOURICO FUMADO 250G 7 2,24 15,68 23")
    assert (ln.code, ln.qty, ln.unit_cost, ln.value, ln.vat) == ("212296", 7, 2.24, 15.68, 23)


def test_row_that_does_not_close_is_rejected():
    assert lines.parse_row(0, "212296 CHOURICO 7 2,24 19,99 23") is None


def test_discount_is_not_read_as_vat():
    # Regression: "6,00%" (a discount) was read as the 6% VAT rate on a 23% line.
    ln = lines.parse_row(0, "300100 GUMMIES 90G 10 1,500 6,00% 14,10 23")
    assert ln.discount == 6.0
    assert ln.vat == 23
    assert ln.unit_cost == pytest.approx(1.41)


def test_price_before_quantity_is_not_read_by_position():
    # Regression (22/09 audit, M5): in a "price | qty | value" layout the position-based
    # reader took the quantity 12,00 as the cost, and every sum still "proved" it.
    ln = lines.parse_row(0, "100200 SOMETHING 1,01 12,00 12,12 23")
    assert (ln.qty, ln.unit_cost, ln.doubtful) == (12.0, 1.01, False)


def test_ambiguous_row_is_flagged():
    # Neither number looks like a quantity: both readings close the row, so never guess.
    ln = lines.parse_row(0, "100200 SOMETHING 1,50 2,50 3,75 23")
    assert ln.doubtful


def test_proof_finds_a_missing_page():
    rows = ["1001 CRISPS 10 1,000 10,00 23", "1003 GUMMIES 5 0,800 4,00 23"]
    parsed, _ = lines.parse_invoice(rows)
    q = qr.parse(make_qr({23: 30.0}))  # the QR includes a line on a page nobody photographed
    result = proof.prove(parsed, q)
    assert not result.proven
    assert result.rates[0].gap == 16.0


def test_proof_passes_when_every_rate_adds_up():
    rows = ["1001 CRISPS 10 1,000 10,00 23", "1003 GUMMIES 5 0,800 4,00 6"]
    parsed, _ = lines.parse_invoice(rows)
    assert proof.prove(parsed, qr.parse(make_qr({23: 10.0, 6: 4.0}))).proven
