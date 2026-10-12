# KPI computation rules

**The AI interprets the request and proposes a plan; the data engine computes.** The model never produces a figure,
SQL or code. The three plan origins (AI, suggestions, guided builder) and the recipes all go through the same
grammar, the same validation and the same engine.

## Grammar
- Aggregations: `sum`, `count_rows`, `count`, `count_distinct`, `avg`, `median`, `min`, `max`, with filters and an
  optional `per` grain (one value per key).
- Derived operations: `add`, `sub`, `mul`, `div`, decimal constants; depth ≤ 4, ≤ 6 aggregates.
  The simplified format (model and builder) offers: aggregate, ratio, difference, margin rate
  `(A − B) ÷ A × 100`, percentage `A ÷ B × 100`.
- Filters: `eq neq in not_in gt gte lt lte is_null not_null between`; `neq` / `not_in` keep empty values.
- Charts: `line` (by period), `bar`, `hbar` (top N ≤ 20). No pie charts, no 3D.
- KPI definition: stable identifier, label, unit, structured expression, filters, comparison, rate rule, display
  decimals. Grain, source fields, period, and the handling of missing values and division by zero follow the rules
  below and are visible in "Check this figure".

## Rules (all tested in `backend/tests/test_kpi_rules.py`)
| Rule | Behaviour |
|---|---|
| Average basket | `sum(amount) ÷ distinct order count`; never divided by the number of rows. The model receives this instruction and the evaluation checks that `count_distinct` is used. |
| Global rate | `Σ numerator ÷ Σ denominator`. Summing percentages is rejected; averaging row-level rates is rejected unless an explicit `row_average` rule is given. |
| Division by zero | Result "unavailable" with reason code `division_by_zero`. |
| Missing value | Never converted to 0; ignored by the aggregate, counted and displayed ("empty values ignored"). An aggregate with no value at all is "unavailable" (`no_values`). An unreadable value is counted as invalid, never replaced by 0. |
| Period-over-period change | Selected period vs the previous period of the same length; with no filter, the last month of the data vs the month before. Unavailable if the previous period is outside the data or empty (`no_comparable_period`); partial coverage is flagged (`previous_partial`). |
| Missing months | Gaps in line charts, never 0. |
| Decimals | Amounts are `DECIMAL(38, s)` in DuckDB and `decimal.Decimal` in Python; the median is computed with one extra decimal to stay exact; rounding happens only at display time (Intl.NumberFormat on strings, ReportLab on Decimal). |
| Currencies | Amounts in different currencies: separate results per currency (currency column, per-row symbol, or column currency). No conversion; combining two columns with different currencies is rejected. An unknown currency triggers a question. |
| Tax-inclusive vs exclusive, cancellations, refunds, credit notes | Never guessed: if two amount bases exist, or a column contains cancellation/refund values, a question is asked (exclude / include). |
| Different grains | An amount that is constant within an order and repeated on each line triggers a question; "once per order" applies `per`. The engine checks the value is constant per key (`grain_conflict`) and refuses to split a key across several categories (`grain_dimension_conflict`). |
| Totals and subtotals | Rows with a text cell starting with a total keyword (6 languages), or an unlabelled row equal to the sum of the preceding rows, are excluded visibly and reversibly. |
| Duplicates | Never removed automatically; flagged, and a question offers to exclude them. |
| Joins | Many-to-one only: unique right key, compatible key types, identical row counts and sums before and after, otherwise blocked. |
| Global filter | Applies to cards, charts, the detail table, the evidence and the exports. |
| Full scope | Every computation runs on all retained rows; the model only receives a profile, never a sample used for computing. |
| Impossible KPI | The model must declare it `unavailable` with the missing data; the interface shows the missing fields and offers the suggestions and the guided builder. A non-existent field or an incompatible operation makes the plan invalid (never an approximation). |

## Safe normalisations of the model output
Strictly structural and deterministic: if only one date column exists and the plan asks for a change or a time
curve without a period, that column becomes the period; a chart whose dimension is that column becomes a by-period
chart. The resulting plan is always shown to the user, who validates it.
