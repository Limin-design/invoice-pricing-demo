"""SQLite stand-in for the store's back-office database.

The real system writes to a legacy back-office database that another program owns.
This demo keeps the same idea, products with a cost, a margin, a VAT rate and a
shelf price, in a small SQLite schema with synthetic data.
"""

import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS families (
    family      TEXT PRIMARY KEY,
    usual_margin REAL NOT NULL           -- % over cost, before VAT
);
CREATE TABLE IF NOT EXISTS products (
    code        TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    family      TEXT NOT NULL REFERENCES families(family),
    vat         INTEGER NOT NULL,         -- 6, 13 or 23
    cost        REAL NOT NULL,            -- last unit cost, before VAT
    margin      REAL NOT NULL,            -- % over cost
    price       REAL NOT NULL,            -- shelf price, VAT included
    barcodes    INTEGER NOT NULL DEFAULT 1,
    cost_date   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS supplier_links (
    supplier_nif  TEXT NOT NULL,
    supplier_code TEXT NOT NULL,
    product_code  TEXT NOT NULL REFERENCES products(code),
    confirmed_by  TEXT NOT NULL,          -- a person, never the program's own guess
    PRIMARY KEY (supplier_nif, supplier_code)
);
CREATE TABLE IF NOT EXISTS sales (
    day          TEXT NOT NULL,
    product_code TEXT NOT NULL REFERENCES products(code),
    qty          REAL NOT NULL,
    revenue      REAL NOT NULL            -- VAT included
);
CREATE TABLE IF NOT EXISTS price_history (
    changed_at   TEXT NOT NULL,
    product_code TEXT NOT NULL,
    old_cost REAL, new_cost REAL,
    old_price REAL, new_price REAL,
    reason       TEXT
);
CREATE INDEX IF NOT EXISTS sales_day ON sales(day);
"""


def connect(path):
    conn = sqlite3.connect(path, isolation_level=None)  # explicit BEGIN/COMMIT only
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def create(path):
    conn = connect(path)
    conn.executescript(SCHEMA)
    return conn


def shelf_price(cost, margin, vat):
    """Price = cost x (1 + margin) x (1 + VAT), the back-office formula, to the cent."""
    return round(cost * (1 + margin / 100) * (1 + vat / 100) + 1e-9, 2)


def margin_from_price(price, cost, vat):
    if cost <= 0:
        return 0.0
    return round((price / (1 + vat / 100) / cost - 1) * 100, 2)
