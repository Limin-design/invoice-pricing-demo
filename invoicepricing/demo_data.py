"""Build a synthetic minimarket: products, suppliers, 12 months of sales, and three sample invoices.

Everything here is invented. Names, tax numbers and figures are fake; the shape of
the problems (stale costs, products sold below cost, generic products, two lines
for one product) mirrors what was found in the real store.
"""

import math
import os
import random
from datetime import date, timedelta

from .db import create, shelf_price

AS_OF = "2026-09-30"
STORE_NIF = "199999999"
SUPPLIERS = {
    "509000001": "Demo Cash & Carry",
    "509000002": "Demo Dairy Coop",
    "509000003": "Demo Bakery",
}

# family: (VAT %, usual margin %, typical cost range, items)
FAMILIES = {
    "Dairy": (6, 18, (0.15, 2.8), ["Yogurt Strawberry 125g", "Yogurt Natural 125g", "Butter Salted 250g",
                                   "Milk UHT 1L", "Cheese Slices 200g", "Cream 200ml", "Kefir 500g"]),
    "Bakery": (6, 25, (0.4, 3.5), ["Toast Bread 500g", "Croissant Pack x6", "Sponge Cake 300g", "Rusks 250g"]),
    "Fish": (6, 22, (2.5, 12.0), ["Salmon Fillet kg", "Sea Bass kg", "Sardines kg", "Cod Loin kg", "Shrimp 1kg"]),
    "Produce": (6, 30, (0.3, 3.0), ["Potatoes kg", "Carrots kg", "Onions kg", "Spinach 170g", "Parsley Bunch"]),
    "Grocery": (6, 20, (0.4, 4.5), ["Rice 1kg", "Pasta Spaghetti 500g", "Olive Oil 750ml", "Canned Tuna 120g",
                                    "Chickpeas 540g", "Tomato Pulp 500g", "Sugar 1kg"]),
    "Drinks": (23, 28, (0.3, 2.5), ["Cola 1,5L", "Orange Soda 33cl", "Ice Tea Lemon 1,5L", "Juice Apple 1L"]),
    "Water": (13, 30, (0.12, 0.9), ["Still Water 1,5L", "Sparkling Water 25cl"]),
    "Snacks": (23, 30, (0.5, 2.5), ["Crisps Salted 150g", "Gummies 90g", "Chocolate Bar 100g", "Peanuts 200g"]),
    "Household": (23, 35, (0.8, 6.0), ["Detergent 1L", "Kitchen Roll x2", "Batteries AA x4", "LED Bulb E27"]),
}
BRANDS = ["Aurora", "Vale Verde", "Mar Azul", "Serra", "Lumina", "Casa Boa", "Prado"]


def _code(n):
    return f"P{n:04d}"


def build(path, samples_dir, seed=7):
    rnd = random.Random(seed)
    if os.path.exists(path):
        os.remove(path)
    conn = create(path)
    conn.execute("BEGIN")
    for fam, (_, margin, _, _) in FAMILIES.items():
        conn.execute("INSERT INTO families VALUES (?, ?)", (fam, margin))

    as_of = date.fromisoformat(AS_OF)
    products, n = [], 0
    for fam, (vat, usual, (lo, hi), items) in FAMILIES.items():
        for item in items:
            for brand in rnd.sample(BRANDS, 3):
                n += 1
                cost = round(rnd.uniform(lo, hi), 3)
                margin = round(max(5.0, rnd.gauss(usual, 4)), 1)
                stale = rnd.random() < 0.35
                age = rnd.randint(800, 1800) if stale else rnd.randint(5, 600)
                cost_date = (as_of - timedelta(days=age)).isoformat()
                price = shelf_price(cost, margin, vat)
                products.append({"code": _code(n), "name": f"{brand} {item}", "family": fam, "vat": vat,
                                 "cost": cost, "margin": margin, "price": price, "barcodes": 1,
                                 "cost_date": cost_date, "popularity": rnd.lognormvariate(0, 1)})

    # Two generic "any brand" buckets, like the real store's biscuits with 325 barcodes.
    for name, fam, cost, barcodes in (("Biscuits Assorted", "Snacks", 0.95, 40), ("Cakes Assorted", "Bakery", 1.10, 25)):
        n += 1
        vat, usual = FAMILIES[fam][0], FAMILIES[fam][1]
        products.append({"code": _code(n), "name": name, "family": fam, "vat": vat, "cost": cost,
                         "margin": float(usual), "price": shelf_price(cost, usual, vat), "barcodes": barcodes,
                         "cost_date": "2025-11-02", "popularity": 3.0})

    # Products whose cost went up while the shelf price stayed: now sold below cost.
    for p in rnd.sample(products, 9):
        p["cost"] = round(p["price"] / (1 + p["vat"] / 100) * rnd.uniform(1.03, 1.18), 3)
        p["margin"] = round((p["price"] / (1 + p["vat"] / 100) / p["cost"] - 1) * 100, 2)

    for p in products:
        conn.execute("INSERT INTO products VALUES (?,?,?,?,?,?,?,?,?)",
                     (p["code"], p["name"], p["family"], p["vat"], p["cost"], p["margin"], p["price"],
                      p["barcodes"], p["cost_date"]))

    # Supplier links: each product from one supplier; a few from two.
    links = {nif: [] for nif in SUPPLIERS}
    supplier_of = {"Dairy": "509000002", "Bakery": "509000003"}
    sup_code = 210000
    for p in products:
        nifs = [supplier_of.get(p["family"], "509000001")]
        if rnd.random() < 0.05 and nifs[0] != "509000001":
            nifs.append("509000001")
        for nif in nifs:
            sup_code += rnd.randint(3, 40)
            links[nif].append((str(sup_code), p))
            conn.execute("INSERT INTO supplier_links VALUES (?,?,?,?)", (nif, str(sup_code), p["code"], "owner"))

    # 12 months of daily sales with a weekly pattern.
    start = as_of - timedelta(days=364)
    weekday = [0.85, 0.8, 0.9, 0.95, 1.15, 1.35, 1.0]
    for d in range(365):
        day = start + timedelta(days=d)
        season = 1 + 0.12 * math.sin(2 * math.pi * (d + 60) / 365)
        for p in products:
            mean = p["popularity"] * weekday[day.weekday()] * season * 0.6
            qty = sum(1 for _ in range(6) if rnd.random() < mean / 6)
            if qty:
                conn.execute("INSERT INTO sales VALUES (?,?,?,?)",
                             (day.isoformat(), p["code"], qty, round(qty * p["price"], 2)))
    conn.execute("COMMIT")

    os.makedirs(samples_dir, exist_ok=True)
    _write_samples(rnd, links, samples_dir)
    return conn


def _pt(x, places=2):
    return f"{x:.{places}f}".replace(".", ",")


def _invoice(nif, number, rows, missing_rows=()):
    """rows: (supplier_code, description, qty, unit_price, discount %, VAT). Returns OCR-like text."""
    bases, text_rows = {}, []
    for i, (code, desc, qty, price, disc, vat) in enumerate(rows):
        value = round(qty * price * (1 - disc / 100) + 1e-9, 2)
        bases[vat] = round(bases.get(vat, 0) + value, 2)
        if i in missing_rows:
            continue  # this line is on the page that was not photographed
        disc_txt = f" {_pt(disc)}%" if disc else ""
        text_rows.append(f"{code} {desc.upper()} {_pt(qty, 0)} {_pt(price, 3)}{disc_txt} {_pt(value)} {vat}")
    fields = [f"A:{nif}", f"B:{STORE_NIF}", "C:PT", "D:FT", "E:N", "F:20260930", f"G:FT {number}",
              f"H:DEMO-{number.replace('/', '-')}", "I1:PT"]
    total_vat = 0.0
    for rate, (b, v) in {6: ("I3", "I4"), 13: ("I5", "I6"), 23: ("I7", "I8")}.items():
        if rate in bases:
            vat_amt = round(bases[rate] * rate / 100 + 1e-9, 2)
            total_vat += vat_amt
            fields += [f"{b}:{bases[rate]:.2f}", f"{v}:{vat_amt:.2f}"]
    total = round(sum(bases.values()) + total_vat, 2)
    fields += [f"N:{total_vat:.2f}", f"O:{total:.2f}", "Q:demo", "R:0000"]
    return "QR: " + "*".join(fields) + "\n" + "\n".join(text_rows) + "\n"


def _write_samples(rnd, links, out):
    def pick(nif, k, generic=False):
        pool = [lp for lp in links[nif] if (lp[1]["barcodes"] >= 10) == generic]
        return rnd.sample(pool, k)

    # 1) A clean cash & carry invoice: costs up, one the same, one down. Proves.
    rows = []
    for sup_code, p in pick("509000001", 7):
        factor = rnd.choice([1.06, 1.12, 1.0, 0.95, 1.08])
        rows.append((sup_code, p["name"], rnd.randint(2, 12), round(p["cost"] * factor, 3), 0, p["vat"]))
    with open(os.path.join(out, "invoice_01_clean.txt"), "w", encoding="utf-8") as f:
        f.write(_invoice("509000001", "1/50211", rows))

    # 2) Bakery invoice with the traps found in the real store:
    #    two lines for the same product at different costs, a 6,00% discount, and a generic product.
    (c1, p1), (c2, p2) = pick("509000003", 2)
    (cg, pg), = pick("509000003", 1, generic=True)
    rows = [
        (c1, p1["name"], 6, round(p1["cost"] * 1.05, 3), 0, p1["vat"]),
        (c1, p1["name"], 4, round(p1["cost"] * 0.92, 3), 0, p1["vat"]),
        (c2, p2["name"], 10, round(p2["cost"] * 1.04, 3), 6.0, p2["vat"]),
        (cg, pg["name"], 12, round(pg["cost"] * 0.7, 3), 0, pg["vat"]),
    ]
    with open(os.path.join(out, "invoice_02_traps.txt"), "w", encoding="utf-8") as f:
        f.write(_invoice("509000003", "2026A/1430", rows))

    # 3) A dairy invoice with one page missing: the lines read do not reach the QR total.
    rows = [(c, p["name"], rnd.randint(4, 24), round(p["cost"] * 1.03, 3), 0, p["vat"])
            for c, p in pick("509000002", 6)]
    with open(os.path.join(out, "invoice_03_missing_page.txt"), "w", encoding="utf-8") as f:
        f.write(_invoice("509000002", "9/77001", rows, missing_rows=(4, 5)))
