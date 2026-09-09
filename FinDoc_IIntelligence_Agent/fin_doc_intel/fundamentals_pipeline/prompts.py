"""Per-node prompts for the table-based extraction pipeline.

Ported from FinDoc Pypi's `fundamentals_module/prompts.py` (there stored obfuscated;
decoded once during the port). Two deliberate adaptations from the source text:

- STATEMENT_CLASSIFIER_PROMPT classifies into our five buckets (balance_sheet,
  income_statement, cash_flow, changes_in_equity, other) instead of the reference's
  three (IS/BS/CF/other) — the reference doesn't classify changes-in-equity tables
  at all (they'd fall into "other" and get dropped), which would be a real
  regression versus what our previous extraction already captured.
- CONSOLIDATION_PROMPT drops one reference-specific example institution name
  ("ING Group"/"ING Bank N.V.") in favor of the already-present generic signals
  ("Group", "Consolidated", "Bank", "Parent", "Standalone"), so the prompt doesn't
  bias toward a specific issuer's naming convention.
"""
from __future__ import annotations

RELEVANCE_PROMPT = """You decide whether a single table from a financial document is relevant for fundamentals analysis.

A table is relevant when it contains ANY data point a financial analyst would use — not just Income-Statement / Balance-Sheet / Cash-Flow variables, but also the KPIs, ratios, and operational metrics analysts read alongside them.

Relevant (return ``relevant: true``) examples:
  - Headline IS / BS / CF statements
  - Segment income statements (Retail Banking, Wholesale Banking, …)
  - Detail breakdowns of an IS/BS/CF line item: fee-income breakdown, operating-expense breakdown, loan portfolio by sector, deposit composition, RWA breakdown (decomposes asset exposure on the BS), capital composition / MREL / TLAC (decomposes equity & subordinated debt on the BS), debt maturity ladder (decomposes long-term debt on the BS).
  - Reconciliation tables that tie reported GAAP figures to alternative performance measures of IS/BS/CF variables.
  - KPI dashboards (mobile customer counts, NPS, sustainability volumes, mobilised volumes)
  - Ratio / percentage / margin tables, even with no underlying absolute amount (e.g. "CET1 ratio = 13.4%", "Leverage ratio = 4.5%") — these are exactly the metrics fundamentals analysis consumes.
  - Credit-rating tables (S&P / Moody's ratings)
  - Share-count tables, even when not EPS components

Not relevant (return ``relevant: false``):
  - Narrative-only tables (commentary, strategy, outlook — no numbers at all)
  - Glossaries, contact / IR information

Return a single ``RelevanceVerdict`` with a one-sentence reason."""

STATEMENT_CLASSIFIER_PROMPT = """You classify a fundamentals table into ONE of five buckets: balance_sheet, income_statement, cash_flow, changes_in_equity, or other.

The classification rule is: read the LINE-ITEM LABELS in the table. Decide which financial statement those labels belong to — either because they ARE variables of that statement themselves, or because they decompose a variable that lives on it. Tag the table with that statement type.

Concrete guidance:

income_statement — labels like revenue, interest income / expense, fees &
  commissions, operating expenses, provisions, taxation, net result,
  EPS components. Includes segment P&Ls and breakdowns of any income-
  statement line item (fee-income breakdown, operating-expense composition).

balance_sheet — labels like assets, liabilities, equity, loans, deposits,
  cash & balances, financial assets / liabilities, RWA, CET1 capital,
  Tier 1 / Tier 2 capital, MREL & TLAC composition, debt maturity
  ladder. Capital composition rows decompose equity; RWA rows
  decompose asset exposures — both balance-sheet variables. Long-term debt
  maturity ladders decompose long-term debt — also balance sheet.

cash_flow — labels like net cash from / used in operating / investing /
  financing activities, depreciation, working-capital changes,
  dividends paid, debt issuance / repayment, capex.

changes_in_equity — labels like opening equity, closing equity, dividends
  declared, retained earnings, share capital movements, other
  comprehensive income reconciling opening to closing equity for the period.

other — choose this when none of the line-item labels is a variable of
  one of the four statements above AND none decomposes one. This is
  where KPIs, ratios, operational metrics, and any other data point
  used in financial analysis land — e.g. "CET1 ratio 13.4%, leverage
  4.5%, NSFR 130%", customer counts, NPS. This bucket IS kept and
  appears in output like any other.

is_breakdown:
  ``true`` when this table decomposes a parent line item rather than
  presenting a complete statement. Examples: "Net fee and commission
  income breakdown", "Operating expenses by category", "Loans by
  sector", "Customer deposits by maturity", "RWA by risk type",
  "Capital composition", "Long-term debt by currency".
  ``false`` when this table IS the headline statement or a segment
  P&L (segment P&Ls are full income statements in their own right).

breakdown_of:
  When ``is_breakdown`` is true, the parent line-item label this
  table breaks down. Use the natural label as it would appear in the
  parent statement: "Net fee and commission income", "Operating
  expenses", "Customer lending", "Risk-weighted assets", "CET1
  capital", "Long-term debt", etc. Leave null when ``is_breakdown``
  is false.

Return a single ``StatementClassification``."""

CONSOLIDATION_PROMPT = """You decide which columns of a fundamentals table are consolidated (Group-level) and which are unconsolidated (parent-only / Bank-only / standalone).

You will be given the column headers as a numbered list (0-based). Decide for each whether it belongs to ``consolidated_columns`` or ``unconsolidated_columns``. Skip columns that are computed deltas or percentages (their headers say "Change", "%", "vs prior", etc.) — do not list them in either set.

Header signals:
  Consolidated: "Group", "Consolidated", "Holding", "the Group", or
                absence of a parent-only qualifier when the table is
                at issuer level.
  Unconsolidated: "Bank", "Company", "Parent", "Standalone", "Parent only".

Segment detection: when the surrounding heading clearly names a segment (Retail Banking, Wholesale Banking, Corporate Line, US Retail, Asia, etc.), set ``segment_id`` to the segment label as printed in the heading. Treat all of that table's columns as consolidated within the segment.

Skip flag: set ``skip: true`` only when the table has NO consolidated columns AND is not itself a segment table. Such a table is dropped from the pipeline.

Return a single ``ConsolidationMeta``."""

LINE_ITEM_EXTRACTION_PROMPT = """You extract every numeric line item from a single fundamentals table into a flat list of ``LineItem`` objects.

Inputs you receive:
  - The table HTML.
  - The list of column header indices to KEEP (consolidated columns
    only). Ignore every other column.
  - An outer scale annotation from the surrounding page text (may be
    empty).
  - A statement type (balance_sheet / income_statement / cash_flow /
    changes_in_equity).
  - A segment_id (may be null) — when set, every cell you emit must
    carry ``coordinates["segment_id"] = <that value>``.

Every Cell must also set ``raw_text`` to the literal token exactly as printed in the
source cell, BEFORE you strip parentheses/commas or apply the sign conversion below
(e.g. the source shows "(1,234)" -> ``raw_text: "(1,234)"``, ``value: -1234``). This is
an evidence field, not a formatting exercise — copy what you actually read.

Coordinates required on every Cell:
  period_end   - ISO "YYYY-MM-DD" derived from the column header.
  period_type  - "point" for balance-sheet rows; "3M" / "6M" / "9M" /
                 "12M" for income-statement / cash-flow / changes-in-
                 equity flows.
                 ("3Q2025" -> 3M; "9M2025" -> 9M; "FY2024" -> 12M;
                  "30 Sep 2025" -> point.)
Also include where applicable:
  segment_id   - set when input segment_id is non-null.
  consolidation - "consolidated" (always for cells you emit; we have
                  already filtered out unconsolidated columns).

Some tables are NOT period-over-period at all: every column is dated the same
single reporting date, but each column is a different NAMED METRIC for the
same row category — e.g. a probability-of-default band / exposure-class
breakdown, where each PD-band row ("0.50 to < 2.00", "2.00 to < 3.50", ...)
has columns like "Original exposure", "EAD", "Number of obligors",
"Average PD", "Average LGD", "RWA", "RWA density". For that shape:
  - Still set period_end/period_type to the table's one reporting date —
    do not invent distinct periods that aren't there.
  - Append that column's own header to the row's label so it stays
    distinguishable: row label "0.50 to < 2.00" + column header
    "Original exposure" -> emit label "0.50 to < 2.00 — Original exposure".
    Do this for every metric column in the row, not just some of them.
  - Never emit two Cells under the same label + period_end + segment_id
    whose values differ without doing this — that leaves both rows'
    identity ambiguous to anyone reading the output.

Hard rules:
  - SKIP variance / comparison columns entirely — do not emit any Cell
    from a column whose header is a computed delta or ratio. Column
    headers that indicate a variance column: "Amount", "%", "Variance",
    "Change", "Difference", "vs.", "+/-", "bps", "Basis Points",
    "Increase", "Decrease", or any header that is a bare percentage
    symbol. Only extract values from primary period columns
    (e.g. "June 30, 2025", "December 31, 2024", "Average Balance",
    "Interest", "Yield/Cost"). This rule overrides "all numeric
    columns" — variance columns are always skipped.
  - Emit EVERY row that has at least one numeric value across the kept
    columns — including sub-items that appear beneath a section header
    row. A row such as "Cash on hand" under the "Cash and cash
    equivalents:" header, or "Non-interest bearing" under "Deposits:",
    or "Additional paid-in capital" under "Stockholders' equity" MUST
    be emitted. Do not skip a row because it is a component of a total
    or because it lives inside a named section. Extract items from ALL
    sections of the table (assets, liabilities, equity, etc.) in full.
  - Section subheader rows (rows whose value cells are all empty,
    e.g. "Assets", "Investment securities:", "Deposits:",
    "Liabilities", "Stockholders' equity") are NOT emitted as
    LineItems. Track them in a STACK so each numeric row carries its
    full enclosing section context.

    Stack rules:
      * On a new section subheader row -> push its label (trailing
        ":" stripped) onto the stack.
      * On a "Total <X>" / "Net <X>" / "Total" row that closes a
        section currently on the stack (i.e. <X> matches the top
        section label, its plural, or its singular form): emit the
        row as a normal numeric LineItem first, then pop that section
        off the stack.
      * If a new section subheader appears that is incompatible with
        the current stack (e.g. "Liabilities" appearing while "Assets"
        is still on the stack with no "Total assets" closer), pop the
        conflicting section(s) off the stack before pushing the new
        one.

    Every numeric LineItem you emit MUST set ``section_path`` to the
    stack contents at emission time (a list of strings, ordered
    outermost -> innermost). Use an empty list when no section is
    open. Currency/scale propagation from subheaders continues as
    before.
  - "(123)" -> -123. Already-negative numbers stay negative.
  - Strip footnote markers from labels: "<sup>1)</sup>", trailing
    "1)", "*", "(a)".
  - Currency / scale precedence: row-level annotation > inner
    subheader > outer page-text annotation.
  - Per-row unit overrides: "(in € billion)" / "(in euros)" / "%" /
    "bps" override defaults for that row only.

Return a single ``ExtractedLineItems`` with the flat list."""


def build_correction_addendum(notes: list[str]) -> str:
    """Format one table's prior-iteration validation issues as a prompt addendum.

    Appended to LINE_ITEM_EXTRACTION_PROMPT's user message on a retry so the
    model sees exactly what its previous attempt on THIS table got flagged for
    — not the whole document's issue list.
    """
    bullets = "\n".join(f"  - {note}" for note in notes)
    return (
        "\n\nYour previous extraction of this table was validated and the following "
        "issues were found. Re-read the table and correct them; do not repeat the "
        f"same mistake:\n{bullets}"
    )
