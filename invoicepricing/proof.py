"""Prove an invoice: the lines read must add up to the QR, VAT rate by VAT rate.

Only a proven invoice can feed prices without extra confirmation. A missing page,
a line in the wrong VAT bucket or a misread number all show up here as a gap.
"""

from collections import defaultdict
from dataclasses import dataclass, field

TOLERANCE_EUR = 0.02
TOLERANCE_PER_LINE = 0.005  # each line value is rounded to the cent on paper


@dataclass
class RateCheck:
    rate: int
    declared: float
    read: float
    lines: int

    @property
    def gap(self):
        return round(self.declared - self.read, 2)

    @property
    def ok(self):
        return abs(self.gap) <= TOLERANCE_EUR + TOLERANCE_PER_LINE * self.lines


@dataclass
class Proof:
    proven: bool
    rates: list
    problems: list = field(default_factory=list)


def prove(lines, qr):
    read, count = defaultdict(float), defaultdict(int)
    problems = []
    for line in lines:
        if line.vat is None:
            problems.append(f"row {line.row}: VAT rate not read")
            continue
        read[line.vat] += line.value
        count[line.vat] += 1
        if line.doubtful:
            problems.append(f"row {line.row}: ambiguous quantity/price")

    rates = []
    for rate in sorted(set(qr.bases) | set(read)):
        check = RateCheck(rate, qr.bases.get(rate, 0.0), round(read[rate], 2), count[rate])
        rates.append(check)
        if not check.ok:
            problems.append(f"{rate}%: invoice says {check.declared:.2f}, lines add up to {check.read:.2f}")

    return Proof(proven=not problems, rates=rates, problems=problems)
