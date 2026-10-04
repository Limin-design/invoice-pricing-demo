"""Turn OCR'd invoice rows into lines: code, description, quantity, unit cost, value, VAT.

The OCR gives text, not columns. A row is only trusted when its own numbers close
the account: quantity x unit price (minus any discount) = line value, to the cent.
Everything else on the page is checked later, against the QR totals.
"""

import re
from dataclasses import dataclass, field
from itertools import combinations

VAT_RATES = (6, 13, 23)
_NUMBER = re.compile(r"^-?\d{1,3}(?:\.\d{3})*(?:,\d+)?$|^-?\d+(?:[.,]\d+)?$")
_PERCENT = re.compile(r"^(\d+(?:,\d+)?)%$")


@dataclass
class Line:
    row: int
    code: str
    description: str
    qty: float
    unit_cost: float  # net of discount
    value: float
    vat: int | None
    discount: float = 0.0
    doubtful: bool = False
    notes: list = field(default_factory=list)


def to_number(token):
    """Portuguese number format: '1.234,56' -> 1234.56; '2,24' -> 2.24."""
    token = token.strip()
    if "," in token:
        token = token.replace(".", "").replace(",", ".")
    return float(token)


def _looks_like_qty(token):
    return "," not in token or bool(re.search(r",00$", token))


def _closes(qty, price, value, discount):
    return abs(qty * price * (1 - discount / 100) - value) <= 0.011


def parse_row(row_number, text):
    """Return a Line, or None when the row's numbers do not close the account."""
    tokens = text.split()
    if not tokens:
        return None

    code = tokens[0] if tokens[0].isdigit() and len(tokens[0]) >= 4 else ""
    numbers, percents, words = [], [], []
    for i, tok in enumerate(tokens[1 if code else 0:], start=1 if code else 0):
        pct = _PERCENT.match(tok)
        if pct:
            percents.append((i, tok, to_number(pct.group(1))))
        elif _NUMBER.match(tok):
            numbers.append((i, tok, to_number(tok)))
        else:
            words.append(tok)

    # A percentage written with decimals ("6,00%") is a discount. It is only read as
    # a VAT rate when the row has no other candidate: a 6% discount was once taken
    # for the 6% VAT rate and a new product almost got the wrong rate.
    discount = 0.0
    vat_candidates = [n for n in numbers if n[2] in VAT_RATES and "," not in n[1]]
    if percents:
        if vat_candidates or len(percents) > 1:
            discount = percents[0][2]
        elif percents[0][2] in VAT_RATES:
            vat_candidates = [percents[0]]

    # Find every (qty, price, value) triple that closes the row. Position alone does not
    # say which number is the quantity: a "price | qty | value" layout once "proved" a
    # swapped cost. A quantity is written as a whole number ("7") or with ",00".
    readings = []
    for (i, ti, a), (j, tj, b), (k, _, v) in combinations(numbers, 3):
        if not (i < j < k and a > 0 and b > 0 and _closes(a, b, v, discount)):
            continue
        qa, qb = _looks_like_qty(ti), _looks_like_qty(tj)
        if qa and not qb:
            readings.append((a, b, v, k))
        elif qb and not qa:
            readings.append((b, a, v, k))
        else:
            readings.append((a, b, v, k))
            readings.append((b, a, v, k))
    if not readings:
        return None

    # Several readings with different costs means the row is ambiguous. Never guess.
    costs = {round(p * (1 - discount / 100), 4) for q, p, v, _ in readings}
    qty, price, value, value_pos = readings[0]
    vat = next((int(n[2]) for n in vat_candidates if n[0] > value_pos), None)

    line = Line(
        row=row_number,
        code=code,
        description=" ".join(words),
        qty=qty,
        unit_cost=round(price * (1 - discount / 100), 4),
        value=value,
        vat=vat,
        discount=discount,
        doubtful=len(costs) > 1,
    )
    if line.doubtful:
        line.notes.append("more than one way to read quantity and price")
    return line


def parse_invoice(rows):
    """Parse OCR rows; return (lines, rows that did not close)."""
    lines, rejected = [], []
    for n, text in enumerate(rows):
        line = parse_row(n, text)
        if line is None:
            rejected.append((n, text))
        else:
            lines.append(line)
    return lines, rejected
