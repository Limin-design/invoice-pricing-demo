"""Parse the fiscal QR code printed on Portuguese invoices (Portaria n.º 195/2020).

The QR carries the supplier's tax number, the document number and, for each VAT
rate, the taxable base and the VAT. That is what lets the program prove that the
lines it read from a photo add up, rate by rate, before any price is touched.

Fields used here:
    A supplier NIF   B customer NIF   D document type   F date (YYYYMMDD)
    G document number   I2 exempt base   I3/I4 base/VAT at 6%
    I5/I6 base/VAT at 13%   I7/I8 base/VAT at 23%   L not subject to VAT
    N total VAT   O document total
"""

import re
from dataclasses import dataclass

# Mainland Portugal rates and the QR fields holding (base, VAT) for each.
RATE_FIELDS = {6: ("I3", "I4"), 13: ("I5", "I6"), 23: ("I7", "I8")}

_NIF = re.compile(r"^\d{9}$")
_DATE = re.compile(r"^\d{8}$")
# Document numbers look like "FT 1/448873" or "FR 26202604/0000075007".
_DOC_NUMBER = re.compile(r"^[A-Za-z0-9 /._-]{1,60}$")


class QRError(ValueError):
    """The QR text is malformed or does not add up."""


@dataclass(frozen=True)
class FiscalQR:
    supplier_nif: str
    customer_nif: str
    doc_type: str
    date: str
    number: str
    bases: dict  # VAT rate -> taxable base
    vat: dict  # VAT rate -> VAT amount
    exempt: float
    not_subject: float
    total_vat: float
    total: float


def _amount(fields, key):
    raw = fields.get(key, "0") or "0"
    try:
        return float(raw)
    except ValueError as exc:
        raise QRError(f"field {key} is not a number") from exc


def parse(text):
    """Return a FiscalQR, or raise QRError.

    Every text field is validated before it can reach the app or become a
    memory key: a forged QR once carried HTML in the supplier NIF.
    """
    fields = {}
    for part in text.strip().split("*"):
        if ":" not in part:
            raise QRError("malformed field")
        key, value = part.split(":", 1)
        fields[key.strip()] = value.strip()

    for key in ("A", "B", "D", "F", "G", "N", "O"):
        if key not in fields:
            raise QRError(f"missing field {key}")
    if not (_NIF.match(fields["A"]) and _NIF.match(fields["B"])):
        raise QRError("tax numbers must have 9 digits")
    if not _DATE.match(fields["F"]):
        raise QRError("date must be YYYYMMDD")
    if not _DOC_NUMBER.match(fields["G"]):
        raise QRError("unexpected characters in the document number")

    bases = {rate: _amount(fields, b) for rate, (b, _) in RATE_FIELDS.items() if b in fields}
    vat = {rate: _amount(fields, v) for rate, (_, v) in RATE_FIELDS.items() if v in fields}
    qr = FiscalQR(
        supplier_nif=fields["A"],
        customer_nif=fields["B"],
        doc_type=fields["D"],
        date=fields["F"],
        number=fields["G"],
        bases=bases,
        vat=vat,
        exempt=_amount(fields, "I2"),
        not_subject=_amount(fields, "L"),
        total_vat=_amount(fields, "N"),
        total=_amount(fields, "O"),
    )

    # The QR must agree with itself before it can be used to judge anything else.
    expected_total = sum(bases.values()) + qr.exempt + qr.not_subject + qr.total_vat
    if abs(expected_total - qr.total) > 0.011:
        raise QRError(f"bases + VAT = {expected_total:.2f}, but the total says {qr.total:.2f}")
    if abs(sum(vat.values()) - qr.total_vat) > 0.011:
        raise QRError("VAT per rate does not add up to the total VAT")
    for rate, base in bases.items():
        if abs(base * rate / 100 - vat.get(rate, 0.0)) > max(0.05, base * 0.001):
            raise QRError(f"VAT at {rate}% does not match its base")
    return qr
