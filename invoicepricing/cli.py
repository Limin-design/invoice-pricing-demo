"""Command line for the demo.

    python -m invoicepricing build
    python -m invoicepricing read samples/invoice_01_clean.txt
    python -m invoicepricing price samples/invoice_01_clean.txt            # simulate
    python -m invoicepricing price samples/invoice_01_clean.txt --write    # write what was simulated
    python -m invoicepricing summary samples/invoice_01_clean.txt          # JSON, read-only (for n8n)
    python -m invoicepricing undo logs/write_....json
    python -m invoicepricing report
"""

import argparse
import json
import sys

from . import apply, demo_data, lines, pricing, proof, qr, report
from .db import connect

DB = "demo.sqlite"


def load_invoice(path):
    with open(path, encoding="utf-8") as f:
        text = f.read().splitlines()
    fiscal = qr.parse(text[0].removeprefix("QR: "))
    parsed, rejected = lines.parse_invoice(text[1:])
    return fiscal, parsed, rejected


def cmd_read(args):
    fiscal, parsed, rejected = load_invoice(args.invoice)
    print(f"{fiscal.number}  supplier {fiscal.supplier_nif}  total {fiscal.total:.2f} EUR")
    for ln in parsed:
        flag = " ?" if ln.doubtful else ""
        disc = f" -{ln.discount:g}%" if ln.discount else ""
        print(f"  {ln.code:>8}  {ln.description[:30]:<30} {ln.qty:>5g} x {ln.unit_cost:>8.4f}{disc}"
              f" = {ln.value:>8.2f}  VAT {ln.vat}{flag}")
    for n, row in rejected:
        print(f"  row {n} did not close: {row}")
    result = proof.prove(parsed, fiscal)
    for r in result.rates:
        print(f"  {r.rate:>2}%: QR {r.declared:>8.2f}  lines {r.read:>8.2f}  {'ok' if r.ok else 'GAP ' + format(r.gap, '.2f')}")
    print("PROVEN" if result.proven else "NOT PROVEN: " + "; ".join(result.problems))
    return fiscal, parsed, result


def cmd_price(args):
    fiscal, parsed, result = cmd_read(args)
    conn = connect(DB)
    proposals = pricing.propose(conn, fiscal.supplier_nif, parsed)
    conn.close()
    print()
    for p in proposals:
        new_price = f"{p.new_price:.2f}" if p.new_price else "?"
        print(f"  row {p.line_row}: {p.action:<15} {p.product_code:<6} cost {p.old_cost:.3f} -> {p.new_cost:.3f}"
              f"  price {p.old_price:.2f} -> {new_price}  {'; '.join(p.reasons + p.warnings)}")
        # In the app a person ticks each card. The demo approves price changes only on proven invoices.
        p.approved = result.proven and p.changes_price
    if not result.proven:
        print("\nInvoice not proven: nothing is pre-approved.")
    plan, fp, stale = apply.simulate(DB, proposals)
    print(f"\nPlan: {len(plan)} product(s), fingerprint {fp}" + (f", changed since proposal: {stale}" if stale else ""))
    if args.write:
        log = apply.write(DB, proposals, fp)
        print(f"Written. Undo log: {log}")


def summarize(db_path, invoice_path):
    """Read-only summary of one invoice for automations: never writes, never approves."""
    fiscal, parsed, rejected = load_invoice(invoice_path)
    result = proof.prove(parsed, fiscal)
    conn = connect(db_path)
    proposals = pricing.propose(conn, fiscal.supplier_nif, parsed)
    conn.close()
    actions = {}
    for p in proposals:
        actions[p.action] = actions.get(p.action, 0) + 1
    needs_person = [f"{p.product_code}: {'; '.join(p.reasons + p.warnings)}"
                    for p in proposals if p.action in ("needs_decision", "refused", "unlinked")]
    return {
        "invoice": fiscal.number,
        "supplier_nif": fiscal.supplier_nif,
        "total": round(fiscal.total, 2),
        "proven": result.proven,
        "problems": result.problems,
        "rows_not_read": len(rejected),
        "actions": actions,
        "needs_person": needs_person,
        "ready_for_one_tap_approval": result.proven and not needs_person and not rejected,
    }


def cmd_summary(args):
    print(json.dumps(summarize(DB, args.invoice), ensure_ascii=False))


def cmd_undo(args):
    apply.undo(DB, args.log)
    print("Undone.")


def cmd_report(args):
    conn = connect(DB)
    print(f"Sales from products with a cost older than 2 years: {report.stale_cost_share(conn, demo_data.AS_OF):.0%}")
    print("\nSold below cost, last 90 days (by money lost):")
    for r in report.below_cost(conn, demo_data.AS_OF)[:10]:
        print(f"  {r['code']}  {r['name'][:34]:<34} sold {r['qty_sold']:>5g}  lost {r['money_lost']:>8.2f} EUR")
    out = report.export_for_bi(conn, "powerbi/data", demo_data.AS_OF)
    print(f"\nCSV for Power BI written to {out}/")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="invoicepricing")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build").set_defaults(fn=lambda a: (demo_data.build(DB, "samples"), print("demo.sqlite and samples/ created")))
    r = sub.add_parser("read")
    r.add_argument("invoice")
    r.set_defaults(fn=cmd_read)
    p = sub.add_parser("price")
    p.add_argument("invoice")
    p.add_argument("--write", action="store_true")
    p.set_defaults(fn=cmd_price)
    s = sub.add_parser("summary")
    s.add_argument("invoice")
    s.set_defaults(fn=cmd_summary)
    u = sub.add_parser("undo")
    u.add_argument("log")
    u.set_defaults(fn=cmd_undo)
    sub.add_parser("report").set_defaults(fn=cmd_report)
    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
