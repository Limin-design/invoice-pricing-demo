"""Render a static preview of the pricing-health dashboard from the CSVs in powerbi/data.

It computes the same measures as the DAX in powerbi/README.md, so the numbers match the
Power BI report. Run:  python powerbi/preview_dashboard.py  ->  docs/dashboard.png
"""
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "powerbi" / "data"
OUT = ROOT / "docs" / "dashboard.png"
STALE_BEFORE = pd.Timestamp("2024-09-30")

INK, MUTED, ACCENT, WARN, GRID = "#1d1d1f", "#6e6e73", "#1f4e79", "#c0392b", "#e5e5ea"


def load():
    sales = pd.read_csv(DATA / "fact_sales.csv", parse_dates=["day"])
    products = pd.read_csv(DATA / "dim_product.csv", parse_dates=["cost_date"])
    families = pd.read_csv(DATA / "dim_family.csv")
    below = pd.read_csv(DATA / "below_cost_90d.csv")
    sales = sales.merge(products[["code", "family", "vat", "cost", "cost_date"]],
                        left_on="product_code", right_on="code", how="left")
    return sales, families, below


def measures(sales, below):
    rev_ex_vat = sales["revenue"] / (1 + sales["vat"] / 100)
    cogs = sales["qty"] * sales["cost"]
    stale = sales["cost_date"] < STALE_BEFORE
    return {
        "Revenue": sales["revenue"].sum(),
        "Gross margin %": (rev_ex_vat.sum() - cogs.sum()) / rev_ex_vat.sum(),
        "Stale cost revenue %": sales.loc[stale, "revenue"].sum() / sales["revenue"].sum(),
        "Money lost below cost (90d)": below["money_lost"].sum(),
    }


def margin_by_family(sales, families):
    s = sales.assign(rev_ex_vat=sales["revenue"] / (1 + sales["vat"] / 100),
                     cogs=sales["qty"] * sales["cost"])
    g = s.groupby("family")[["rev_ex_vat", "cogs"]].sum()
    # usual_margin in the database is a markup over cost, so compare like with like
    g["markup"] = (g["rev_ex_vat"] / g["cogs"] - 1) * 100
    return g.join(families.set_index("family")).sort_values("markup")


def card(ax, title, value, colour=INK):
    ax.axis("off")
    ax.text(0.02, 0.62, value, fontsize=20, fontweight="bold", color=colour, transform=ax.transAxes)
    ax.text(0.02, 0.22, title, fontsize=9.5, color=MUTED, transform=ax.transAxes)


def main():
    sales, families, below = load()
    m = measures(sales, below)
    fam = margin_by_family(sales, families)
    monthly = sales.set_index("day")["revenue"].resample("MS").sum()

    plt.rcParams.update({"font.family": "Segoe UI", "axes.edgecolor": GRID, "axes.labelcolor": MUTED,
                         "xtick.color": MUTED, "ytick.color": MUTED})
    fig = plt.figure(figsize=(13, 8.2), dpi=130, facecolor="white")
    gs = fig.add_gridspec(3, 4, height_ratios=[0.55, 2, 2], hspace=0.55, wspace=0.35)
    fig.suptitle("Pricing health — where is the store losing margin?", x=0.04, ha="left",
                 fontsize=15, fontweight="bold", color=INK)
    fig.text(0.04, 0.925, "Synthetic data from invoice-pricing-demo · same measures as the Power BI model",
             fontsize=9, color=MUTED)

    card(fig.add_subplot(gs[0, 0]), "Revenue (12 months)", f"€{m['Revenue']:,.0f}")
    card(fig.add_subplot(gs[0, 1]), "Gross margin %", f"{m['Gross margin %']:.1%}", ACCENT)
    card(fig.add_subplot(gs[0, 2]), "Revenue on costs older than 2 years", f"{m['Stale cost revenue %']:.0%}", WARN)
    card(fig.add_subplot(gs[0, 3]), "Money lost selling below cost (90 days)",
         f"€{m['Money lost below cost (90d)']:,.0f}", WARN)

    ax = fig.add_subplot(gs[1, :2])
    ax.plot(monthly.index, monthly.values, color=ACCENT, lw=2.2, marker="o", ms=3.5)
    ax.set_title("Revenue by month", loc="left", fontsize=11, color=INK)
    ax.yaxis.set_major_formatter(lambda v, _: f"€{v/1000:.0f}k")
    ax.grid(axis="y", color=GRID)
    ax.spines[["top", "right"]].set_visible(False)

    ax = fig.add_subplot(gs[1, 2:])
    y = range(len(fam))
    colours = [WARN if r.markup < r.usual_margin else ACCENT for r in fam.itertuples()]
    ax.barh(y, fam["markup"], color=colours, height=0.6)
    ax.scatter(fam["usual_margin"], y, marker="|", s=260, color=INK, zorder=3, label="usual markup")
    ax.set_yticks(list(y), fam.index)
    ax.set_title("Markup over cost % by family vs usual markup (red = below)", loc="left", fontsize=11, color=INK)
    ax.legend(frameon=False, fontsize=8, loc="lower right")
    ax.grid(axis="x", color=GRID)
    ax.spines[["top", "right"]].set_visible(False)

    ax = fig.add_subplot(gs[2, :])
    ax.axis("off")
    ax.set_title("Below cost in the last 90 days — the to-do list for the next price review",
                 loc="left", fontsize=11, color=INK)
    top = below.sort_values("money_lost", ascending=False).head(8)
    rows = [[r.name, r.family, f"€{r.cost:.2f}", f"€{r.price:.2f}", f"{r.qty_sold:.0f}", f"€{r.money_lost:.2f}"]
            for r in top.itertuples()]
    table = ax.table(cellText=rows, colLabels=["Product", "Family", "Cost", "Price", "Units sold", "Money lost"],
                     loc="upper left", colLoc="left", cellLoc="left", colWidths=[0.38, 0.14, 0.1, 0.1, 0.12, 0.12])
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    table.scale(1, 1.35)
    for (row, _), cell in table.get_celld().items():
        cell.set_edgecolor(GRID)
        if row == 0:
            cell.set_text_props(color="white", fontweight="bold")
            cell.set_facecolor(ACCENT)

    OUT.parent.mkdir(exist_ok=True)
    fig.savefig(OUT, bbox_inches="tight", facecolor="white")
    print(f"saved {OUT}")
    for k, v in m.items():
        print(f"{k}: {v:,.4f}")


if __name__ == "__main__":
    main()
