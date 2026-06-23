# Design Note — FX Rate Pipeline
**B2 Impact · Senior BI Analyst · Technical Case**

---

## 1. Data source — European Central Bank (ECB)

**Decision:** Use the ECB's official `eurofxref-hist.zip` file as the sole data source.

**Rationale:**
- The ECB is the authoritative source for EUR reference rates in Europe. All third-party providers (frankfurter.app, exchangeratesapi.io, etc.) ultimately derive their data from this file.
- A single HTTP request retrieves the complete history since 1999 — no API key, no rate limits, no pagination.
- The URL is stable and updated daily at ~16:00 CET on every TARGET2 banking day.

**Trade-offs:**

| Pro | Con |
|---|---|
| Official, auditable source | Only EUR-based rates published (cross pairs must be derived) |
| Free, no registration | Updated once per day — no intraday rates |
| Full history in one request | Tied to ECB banking calendar (no weekend rates) |

---

## 2. Historical window — 5 years

**Decision:** Load data from January 1st five years prior to the execution date.

**Rationale:**
- Five years provides enough depth for meaningful multi-year YTD comparisons, trend analysis, and moving average warm-up periods.
- The resulting dataset (~58,800 rows) is small enough for direct Power BI import without performance issues.

**Trade-offs:**

| Pro | Con |
|---|---|
| Covers multiple economic cycles | Excludes pre-2021 history (COVID impact, pre-Brexit) |
| Fast pipeline execution (~3 seconds) | START_DATE must be updated manually for longer windows |
| Manageable file size (~4 MB) | |

> The `START_DATE` constant in `pipeline.py` can be changed to extend the window at any time.

---

## 3. Cross-pair computation

**Decision:** Derive all 42 ordered cross pairs arithmetically from EUR-based rates using the formula:

```
rate(A → B) = ECB[B] / ECB[A]
```

**Rationale:**
- The ECB publishes 1 EUR = X units for every currency. Dividing two rates cancels the EUR and yields the exact cross rate.
- Arithmetically derived rates are consistent by construction — no triangular arbitrage is possible.
- This avoids making 42 separate API calls and guarantees internal consistency across all pairs.

**Trade-offs:**

| Pro | Con |
|---|---|
| Single source of truth | Rates are reference rates, not market bid/ask |
| No rounding inconsistencies between pairs | Derived rates may differ slightly from market cross rates |
| Computationally cheap | |

---

## 4. Output format and schema

**Decision:** Single flat CSV file in long (tall) format, with semicolon as field separator and comma as decimal separator.

**Rationale:**
- **Long format** — one row per pair per date — integrates natively with Power BI's filter and slicer model. A single slicer on `pair_label` filters all visuals simultaneously without requiring unpivoting in Power Query.
- **Pre-computed metrics** — `daily_change`, `ytd_change_pct`, moving averages — reduce the DAX complexity in Power BI and make the file self-contained for any BI tool.
- **Semicolon/comma format** matches the Windows European regional settings, ensuring correct numeric parsing in Power BI without locale configuration.
- **CSV over Parquet** — chosen for maximum compatibility. Any analyst can open and inspect the file in Excel without additional tooling.

**Trade-offs:**

| Pro | Con |
|---|---|
| No Power Query transformations needed | Larger file than Parquet (~4 MB vs ~0.5 MB) |
| Self-documenting column names | Repeated string columns (pair_label, etc.) increase size |
| Compatible with Excel, Power BI, any BI tool | Single file — no partitioning for very large datasets |

---

## 5. YTD definition

**Decision:** YTD is defined as the percentage change from the **first available ECB trading day of each calendar year** for that specific pair.

```
ytd_change_pct = (rate_today - rate_jan1st) / rate_jan1st × 100
```

**Rationale:**
- Computed for every year in the dataset (not just the current year), enabling year-over-year comparison in Power BI slicers.
- Using the first trading day rather than December 31st of the prior year avoids gaps when the dataset starts mid-January.

**Trade-offs:**

| Pro | Con |
|---|---|
| Works across all years in the dataset | First trading day varies by year (not always Jan 1st) |
| Consistent with standard financial YTD definition | Pairs with missing early data (e.g. RON pre-2005) have a later YTD baseline |

---

## 6. Metrics selection

| Metric | Definition | Purpose |
|---|---|---|
| `daily_change` | rate(t) − rate(t−1) | Absolute daily movement |
| `daily_change_pct` | daily_change / rate(t−1) × 100 | Normalized daily movement |
| `rate_7d_avg` | 7-day rolling mean | Short-term trend smoothing |
| `rate_30d_avg` | 30-day rolling mean | Medium-term trend baseline |
| `ytd_change_pct` | % change from Jan 1st | Year-to-date performance |
| `trend_signal` (DAX) | 7d avg vs 30d avg | Momentum indicator (Bullish/Bearish/Neutral) |

**Trade-off:** Moving averages use `min_periods=1` so the first days of the series are not dropped. This means early averages are computed on fewer than 7 or 30 observations — acceptable given the 5-year history provides sufficient warm-up.

---

## 7. Numeric separator compatibility — American vs European locale

**Problem encountered:** Python and pandas generate CSV files using the **American numeric format** by default: dot (`.`) as decimal separator and comma (`,`) as thousands separator. Power BI Desktop configured with **Spanish/European regional settings** reads these files with the opposite convention — dot as thousands separator and comma as decimal — causing systematic misreading of every numeric value.

**Concrete example:**

| Value in CSV (Python default) | Power BI reads it as (Spanish locale) |
|---|---|
| `7.474700` | 7,474,700 — wrong by a factor of 1,000,000 |
| `-2.6E-05` | `-2,6E-05` displayed as scientific notation |
| `0.317000` | 317,000 — completely wrong |

The first symptom was the `Latest Rate` card for EUR/DKK showing **7.474.700** instead of **7,4747**. The second symptom was columns like `daily_change` displaying values such as **-2,6E-05** (scientific notation) in the Power BI data preview.

**Root cause — two separate issues:**

1. **Decimal separator mismatch:** pandas writes `.` as decimal; Spanish Power BI expects `,`.
2. **Scientific notation:** pandas automatically switches to scientific notation for very small values (e.g. `9e-05`). When combined with a locale mismatch this becomes unreadable in Power BI.

**Solution applied:** The `to_csv()` call was replaced with an explicit pre-formatting step before writing:

```python
# Format each float explicitly: 6 fixed decimal places, comma as decimal separator
for col in float_cols:
    output_df[col] = output_df[col].apply(
        lambda x: f"{x:.6f}".replace(".", ",") if pd.notna(x) else ""
    )
output_df.to_csv(OUTPUT_FILE, index=False, date_format="%Y-%m-%d", sep=";")
```

This approach:
- Forces **fixed-point notation** (`%.6f`) — eliminates scientific notation entirely
- Replaces `.` with `,` — correct decimal separator for Spanish locale
- Uses `;` as field separator — standard for European CSV files where `,` is reserved for decimals
- Power BI with Spanish regional settings reads the file natively without any Power Query transformation

**Why not use `to_csv(decimal=",", float_format="%.6f")`?**

This pandas option was tested first but proved unreliable: pandas applies the format string before the decimal substitution, and for certain float values the substitution did not propagate correctly, leaving residual scientific notation in the output. Explicit pre-formatting is more robust and transparent.

**Trade-offs:**

| Pro | Con |
|---|---|
| Zero configuration needed in Power BI | File is not directly readable by systems with American locale |
| No scientific notation in any cell | Requires explicit formatting loop in the pipeline |
| Fully compatible with Spanish Excel and Power BI | If shared internationally, the separator convention must be documented |

> **Note for international use:** if the file needs to be consumed by a system using American locale, change the `replace(".", ",")` to keep the dot and change `sep=";"` back to `sep=","`.

---

## 8. Power BI model design

**Decision:** Single flat table model — no star schema.

**Rationale:**
- With a single fact table and no dimension tables, the model is simple to maintain and immediately understandable by any analyst.
- All time dimensions (`year`, `month`, `quarter`, `week`) are pre-computed in the pipeline, removing the need for a separate Date table for basic analysis.
- DAX measures are stored in an isolated `_Medidas` table so they survive data source updates without being lost.

**Trade-offs:**

| Pro | Con |
|---|---|
| Zero model complexity | No reusable Date table (limits advanced time intelligence DAX) |
| Fast to set up and hand over | Repeated dimension values increase model size |
| Measures survive data refreshes | Less flexible for multi-table joins if scope expands |

---

*Pipeline executed on: 2026-06-23 · Data through: 2026-06-22 · Source: ECB eurofxref-hist.zip*
