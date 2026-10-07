# Automation: invoice intake with n8n

`n8n_invoice_intake.json` is an n8n workflow (import it with *Workflows → Import from file*).
It turns each new invoice into a triage message, and it never changes a price.

```
new invoice text in /data/inbox
  → python -m invoicepricing summary <file>     (read-only JSON)
  → log the invoice to a Google Sheet
  → proven and nothing to decide?  yes → "ready to approve" email
                                    no  → "needs a person" email, with the reasons
```

Why it stops at a message: prices are a business decision. The automation removes the reading and
checking work; a person still approves in the app, and the write path (simulate, fingerprint, backup,
undo log) stays the only way to change the database.

Example output of the summary step for `samples/invoice_02_traps.txt`:

```json
{"invoice": "FT 2026A/1430", "supplier_nif": "509000003", "total": 44.21, "proven": true,
 "problems": [], "rows_not_read": 0, "actions": {"needs_decision": 3, "cost_down": 1},
 "needs_person": ["P0027: several lines for the same product with different costs: choose one", "...",
                  "P0128: generic product (25 barcodes): one line cannot set its cost"],
 "ready_for_one_tap_approval": false}
```

To run it, n8n needs this repository at `/app` (for example a Docker volume), Python 3.11+, and your own
Google Sheets and Gmail credentials; the addresses and sheet ID in the file are placeholders.
