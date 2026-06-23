# FX Rate Pipeline — B2 Impact Technical Case

Daily foreign exchange rates for **NOK, EUR, SEK, PLN, RON, DKK, CZK** fetched from the European Central Bank, transformed into all cross pairs, and delivered as an analytics-ready file for Power BI.

> **Notas de diseño / Design notes**
> Las decisiones clave de arquitectura y los trade-offs del proyecto están documentados en dos idiomas:
> - [DESIGN_NOTE_ES.md](DESIGN_NOTE_ES.md) — versión en **español**
> - [DESIGN_NOTE.md](DESIGN_NOTE.md) — version in **English**

---

## Repository structure

```
├── pipeline.py          # End-to-end data pipeline
├── requirements.txt     # Python dependencies
├── DESIGN_NOTE_ES.md    # Nota de diseño en español
├── DESIGN_NOTE.md       # Design note in English
├── output/
│   └── fx_rates.csv     # Generated output — ready for Power BI
└── README.md
```

---

## Requirements

- Python 3.10+
- Internet access (one HTTPS request to the ECB)

Install dependencies:

```bash
pip install -r requirements.txt
```

---

## How to run

```bash
python pipeline.py
```

The script will:
1. Download the ECB historical ZIP file
2. Parse and filter rates for the 7 configured currencies
3. Generate all 42 ordered cross pairs
4. Compute all analytical metrics
5. Write `output/fx_rates.csv`

Expected output:

```
08:00:00  INFO  FX Rate Pipeline — B2 Impact Technical Case
08:00:00  INFO  Currencies : NOK, EUR, SEK, PLN, RON, DKK, CZK
08:00:00  INFO  Period     : 2021-01-01 → 2026-06-23
08:00:00  INFO  Pairs      : 42 ordered cross pairs
08:00:01  INFO  Loaded 1400 trading days  (2021-01-04 → 2026-06-22)
08:00:01  INFO  Generated 42 pairs → 58,800 rows
08:00:01  INFO    Total rows      : 58,800
08:00:01  INFO    Unique pairs    : 42  (expected 42)
08:00:01  INFO    Null rates      : 0
08:00:01  INFO    Null YTD %      : 0
08:00:02  INFO  Saved → output/fx_rates.csv  (58,800 rows)
```

---

## How to validate the output

### 1. Row and pair count

```python
import pandas as pd

df = pd.read_csv("output/fx_rates.csv", parse_dates=["date"])

print(df["pair_label"].nunique())   # must be 42
print(df["rate"].isna().sum())      # must be 0
print(df["date"].min(), df["date"].max())
```

### 2. Cross-rate consistency check

For any three currencies A, B, C the triangular relationship must hold:  
`rate(A→C) ≈ rate(A→B) × rate(B→C)`

```python
latest = df[df["date"] == df["date"].max()].set_index("pair_label")["rate"]

eur_nok = latest["EUR/NOK"]
eur_sek = latest["EUR/SEK"]
nok_sek = latest["NOK/SEK"]

# Should be ~0
print(abs(nok_sek - eur_sek / eur_nok))
```

### 3. YTD sanity check

The `ytd_change_pct` for the first trading day of each year must be 0:

```python
first_days = df.groupby(["pair_label", "year"]).first().reset_index()
assert (first_days["ytd_change_pct"] == 0.0).all(), "YTD baseline error"
print("YTD baseline check passed")
```

### 4. Daily change sanity check

```python
sample = df[df["pair_label"] == "EUR/NOK"].sort_values("date").head(10)
print(sample[["date", "rate", "daily_change", "daily_change_pct", "ytd_change_pct"]])
```

---

## Output schema

| Column | Type | Description |
|---|---|---|
| `date` | DATE | Trading day (weekdays only, ECB calendar) |
| `year` | INT | Calendar year |
| `month` | INT | Month number (1–12) |
| `quarter` | INT | Quarter (1–4) |
| `week` | INT | ISO week number |
| `day_of_week` | STRING | Day name (Monday … Friday) |
| `base_currency` | STRING | Currency you are selling (e.g. EUR) |
| `quote_currency` | STRING | Currency you are buying (e.g. NOK) |
| `pair_label` | STRING | Readable pair label (e.g. EUR/NOK) |
| `rate` | FLOAT | Units of quote currency per 1 unit of base |
| `daily_change` | FLOAT | Absolute rate change vs previous trading day |
| `daily_change_pct` | FLOAT | Percentage rate change vs previous trading day |
| `rate_7d_avg` | FLOAT | 7-day rolling average of the rate |
| `rate_30d_avg` | FLOAT | 30-day rolling average of the rate |
| `ytd_start_rate` | FLOAT | Rate on the first trading day of the calendar year |
| `ytd_change_pct` | FLOAT | % change from `ytd_start_rate` to `rate` |

> **YTD definition:** Year-to-Date change is calculated from the first available ECB trading day of each calendar year for that specific pair. It is populated for every year in the dataset, enabling YTD analysis across multiple years in Power BI.

---

## Connecting to Power BI

1. Open Power BI Desktop
2. **Home → Get Data → Text/CSV**
3. Select `output/fx_rates.csv`
4. In the preview dialog click **Load** (no transformations needed — the schema is already clean)
5. Set `date` column type to **Date** if not auto-detected
6. The table is ready to use as a fact table — no additional modeling required

---

## Configuration

To change the historical window, edit `pipeline.py`:

```python
START_DATE = date(TODAY.year - 5, 1, 1)   # default: 5 years
```

To change the currency set, edit:

```python
CURRENCIES = ["NOK", "EUR", "SEK", "PLN", "RON", "DKK", "CZK"]
```

---

## Design note

**Data source — ECB eurofxref-hist.zip**  
The ECB publishes all reference rates in a single ZIP file updated each working day at ~16:00 CET. It requires no API key, has no rate limits, covers all seven required currencies back to 1999, and is the authoritative source — rates from third-party wrappers are ultimately derived from this file.

**Cross-pair derivation**  
The ECB only publishes EUR-based rates (1 EUR = X units). All 42 cross pairs are derived via `rate(A→B) = EUR_B / EUR_A`. This is arithmetically exact and avoids making 42 separate API calls.

**Historical window**  
Five years (2021 → present) provides sufficient depth for multi-year YTD comparisons and trend analysis while keeping the file size small enough (~4 MB) for direct Power BI import without performance concerns.

**Long (tall) format**  
Each row represents one pair on one date. This structure integrates naturally with Power BI's filter and slicer model: a single slicer on `pair_label` filters all visuals simultaneously without requiring unpivoting in Power Query.

**YTD across all years**  
`ytd_change_pct` is computed for every calendar year in the dataset, not just the current one. This allows a year slicer in Power BI to show historically accurate YTD figures for any selected year.
