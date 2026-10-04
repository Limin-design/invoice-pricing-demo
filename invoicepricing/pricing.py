"""Propose a new cost and price for each invoice line. Proposals only: nothing is written here.

Rules (the store owner's decisions):
- cost goes up   -> keep the product's own margin, the price follows;
- cost goes down -> the shelf price stays (the margin improves);
- a product bought from two suppliers keeps the higher cost: it never goes down by itself;
- anything surprising is refused or sent to a person, never guessed.
"""

from collections import defaultdict
from dataclasses import dataclass, field

from .db import margin_from_price, shelf_price

PRICE_ACTIONS = ("cost_up", "cost_down")
GENERIC_BARCODES = 10  # a product with this many barcodes is a "generic" bucket
COST_RATIO = (0.6, 1.7)  # a new cost outside this band of the old one is suspicious
BIG_CHANGE = 0.30


@dataclass
class Proposal:
    line_row: int
    supplier_code: str
    description: str
    action: str  # cost_up | cost_down | same | needs_decision | refused | unlinked | no_change
    product_code: str = ""
    old_cost: float = 0.0
    old_margin: float = 0.0
    old_price: float = 0.0
    vat: int = 0
    new_cost: float = 0.0
    new_margin: float = 0.0
    new_price: float = 0.0
    reasons: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    approved: bool = False

    @property
    def changes_price(self):
        return self.action in PRICE_ACTIONS


def _link(conn, supplier_nif, supplier_code):
    row = conn.execute(
        "SELECT product_code FROM supplier_links WHERE supplier_nif=? AND supplier_code=?",
        (supplier_nif, supplier_code),
    ).fetchone()
    return row["product_code"] if row else None


def _supplier_count(conn, product_code):
    row = conn.execute(
        "SELECT COUNT(DISTINCT supplier_nif) AS n FROM supplier_links WHERE product_code=?",
        (product_code,),
    ).fetchone()
    return row["n"]


def propose_line(conn, supplier_nif, line):
    p = Proposal(line.row, line.code, line.description, "unlinked")
    code = _link(conn, supplier_nif, line.code)
    if code is None:
        p.reasons.append("no confirmed link for this supplier code")
        return p

    prod = conn.execute("SELECT * FROM products WHERE code=?", (code,)).fetchone()
    p.product_code = code
    p.old_cost, p.old_margin, p.old_price, p.vat = prod["cost"], prod["margin"], prod["price"], prod["vat"]
    new_cost = round(line.unit_cost, 4)

    if line.doubtful:
        p.action = "refused"
        p.reasons.append("the line can be read in more than one way")
        return p
    if line.vat is not None and line.vat != prod["vat"]:
        p.action = "refused"
        p.reasons.append(f"VAT on the invoice is {line.vat}%, the product has {prod['vat']}%")
        return p
    ratio = new_cost / prod["cost"] if prod["cost"] else 1.0
    if not COST_RATIO[0] <= ratio <= COST_RATIO[1]:
        p.action = "refused"
        p.reasons.append(f"new cost is {ratio:.2f}x the old one: probably the wrong product or a box price")
        return p
    if prod["barcodes"] >= GENERIC_BARCODES:
        # Real incident: a "biscuits, any brand" product with 325 barcodes took the
        # cost of one invoice line and its margin jumped from 20% to 79%.
        p.action = "needs_decision"
        p.new_cost = new_cost
        p.reasons.append(f"generic product ({prod['barcodes']} barcodes): one line cannot set its cost")
        return p

    if abs(new_cost - prod["cost"]) < 0.0005:
        p.action = "same"
        p.new_cost, p.new_margin, p.new_price = prod["cost"], prod["margin"], prod["price"]
        return p

    if new_cost > prod["cost"]:
        p.action = "cost_up"
        p.new_cost, p.new_margin = new_cost, prod["margin"]
        p.new_price = shelf_price(new_cost, prod["margin"], prod["vat"])
    else:
        if _supplier_count(conn, code) > 1:
            p.action = "needs_decision"
            p.new_cost = new_cost
            p.reasons.append("bought from two suppliers: the cost does not go down by itself")
            return p
        p.action = "cost_down"
        p.new_cost, p.new_price = new_cost, prod["price"]
        p.new_margin = margin_from_price(prod["price"], new_cost, prod["vat"])

    if p.new_price < shelf_price(p.new_cost, 0, p.vat):
        p.action = "refused"
        p.reasons.append("the shelf price would be below cost")
        return p
    change = abs(new_cost - prod["cost"]) / prod["cost"]
    if change > BIG_CHANGE:
        p.warnings.append(f"cost changes {change:.0%}")
    return p


def resolve_same_product(proposals):
    """Two lines of one invoice for the same product: only lines that change the price count.

    Real incident: a person confirmed the second of two lines, and the writer used
    the first one, which had been marked "no change" but still carried its cost.
    """
    by_product = defaultdict(list)
    for p in proposals:
        if p.product_code:
            by_product[p.product_code].append(p)
    for group in by_product.values():
        pricing = [p for p in group if p.changes_price]
        if len(pricing) > 1 and len({p.new_cost for p in pricing}) > 1:
            for p in pricing:
                p.action = "needs_decision"
                p.reasons.append("several lines for the same product with different costs: choose one")
        for p in group:
            if p not in pricing and p.action == "same" and pricing:
                p.action = "no_change"
    return proposals


def propose(conn, supplier_nif, lines):
    return resolve_same_product([propose_line(conn, supplier_nif, line) for line in lines])


def decide(proposals, row, accept):
    """A person's decision on a line that needed one. Other lines of that product step aside."""
    chosen = next(p for p in proposals if p.line_row == row)
    if chosen.action != "needs_decision":
        raise ValueError("this line does not need a decision")
    if not accept:
        chosen.action = "refused"
        chosen.reasons.append("rejected by a person")
        return chosen
    for p in proposals:
        if p is not chosen and p.product_code == chosen.product_code and p.action in PRICE_ACTIONS + ("needs_decision",):
            p.action = "no_change"
            p.reasons.append(f"another line (row {row}) was chosen for this product")
    if chosen.new_cost >= chosen.old_cost:
        chosen.action = "cost_up"
        chosen.new_margin = chosen.old_margin
        chosen.new_price = shelf_price(chosen.new_cost, chosen.old_margin, chosen.vat)
    else:
        chosen.action = "cost_down"
        chosen.new_price = chosen.old_price
        chosen.new_margin = margin_from_price(chosen.old_price, chosen.new_cost, chosen.vat)
    chosen.reasons.append("confirmed by a person")
    return chosen
