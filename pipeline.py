#!/usr/bin/env python3
"""
FX Rate Pipeline — B2 Impact Technical Case
============================================
Data source : European Central Bank (ECB) — eurofxref-hist.zip
              https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.zip

All ECB rates are expressed as: 1 EUR = X units of the target currency.
Cross rates are derived arithmetically: rate(A→B) = EUR/B ÷ EUR/A

Currencies : NOK, EUR, SEK, PLN, RON, DKK, CZK  →  42 ordered cross pairs
History    : 5 years back from today (configurable via START_DATE)
Output     : output/fx_rates.csv  — analytics-ready for Power BI
"""

import io
import zipfile
import logging
from itertools import permutations
from pathlib import Path
from datetime import date

import pandas as pd
import requests

# ── Configuration ──────────────────────────────────────────────────────────────
ECB_ZIP_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.zip"
CURRENCIES  = ["NOK", "EUR", "SEK", "PLN", "RON", "DKK", "CZK"]
TODAY       = date.today()
START_DATE  = date(TODAY.year - 5, 1, 1)   # change to extend/shorten the history window
OUTPUT_DIR  = Path(__file__).parent / "output"
OUTPUT_FILE = OUTPUT_DIR / "fx_rates.csv"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ── 1. Extract ─────────────────────────────────────────────────────────────────

def fetch_ecb_rates(start: date) -> pd.DataFrame:
    """
    Download the ECB historical ZIP and return a pivot DataFrame:
        date | NOK | SEK | PLN | RON | DKK | CZK | EUR
    where every value is  1 EUR = X units  (EUR column is always 1.0).
    Rows are filtered to [start, today] and sorted ascending by date.
    """
    log.info("Fetching ECB historical rates ...")
    response = requests.get(ECB_ZIP_URL, timeout=60)
    response.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        csv_name = zf.namelist()[0]
        log.info("Parsing %s from archive", csv_name)
        with zf.open(csv_name) as f:
            raw = pd.read_csv(f)

    # ECB CSV has trailing whitespace in column names and a trailing empty column
    raw.columns = raw.columns.str.strip()
    raw = raw.dropna(axis=1, how="all")
    raw = raw.rename(columns={"Date": "date"})
    raw["date"] = pd.to_datetime(raw["date"])

    # Keep only the non-EUR target currencies
    non_eur   = [c for c in CURRENCIES if c != "EUR"]
    available = [c for c in non_eur if c in raw.columns]
    missing   = set(non_eur) - set(available)
    if missing:
        raise ValueError(f"Currencies not found in ECB data: {missing}")

    df = raw[["date"] + available].copy()

    # ECB uses "N/A" strings for unavailable dates — coerce to NaN
    for col in available:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # EUR is the implicit base — add as constant so cross-pair formula is uniform
    df["EUR"] = 1.0

    df = (
        df[df["date"] >= pd.Timestamp(start)]
        .sort_values("date")
        .reset_index(drop=True)
    )

    log.info(
        "Loaded %d trading days  (%s → %s)",
        len(df),
        df["date"].min().date(),
        df["date"].max().date(),
    )
    return df


# ── 2. Transform: generate all cross pairs ─────────────────────────────────────

def build_cross_pairs(ecb: pd.DataFrame) -> pd.DataFrame:
    """
    Generate all 42 ordered cross pairs from the 7 currencies.

    Formula:  rate(base → quote) = ecb[quote] / ecb[base]

    This works uniformly for every combination because EUR = 1.0:
      EUR/NOK  →  1.0 / ecb[NOK]  ... wait, actually:
      NOK/EUR  →  ecb[EUR] / ecb[NOK] = 1.0 / ecb[NOK]   ✓
      EUR/NOK  →  ecb[NOK] / ecb[EUR] = ecb[NOK] / 1.0   ✓
      NOK/SEK  →  ecb[SEK] / ecb[NOK]                     ✓
    """
    all_ccy = [c for c in CURRENCIES if c in ecb.columns]
    frames  = []

    for base, quote in permutations(all_ccy, 2):
        rate = (ecb[quote] / ecb[base]).round(6)
        frames.append(
            pd.DataFrame({
                "date"          : ecb["date"].values,
                "base_currency" : base,
                "quote_currency": quote,
                "pair_label"    : f"{base}/{quote}",
                "rate"          : rate.values,
            })
        )

    df = (
        pd.concat(frames, ignore_index=True)
        .dropna(subset=["rate"])
        .sort_values(["base_currency", "quote_currency", "date"])
        .reset_index(drop=True)
    )

    log.info("Generated %d pairs → %d rows", df["pair_label"].nunique(), len(df))
    return df


# ── 3. Transform: analytical metrics ──────────────────────────────────────────

def add_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """
    Enrich the cross-pair table with all analytical columns required for Power BI.

    Time dimensions
    ---------------
    year, month, quarter, week, day_of_week

    Daily movement
    --------------
    daily_change      — absolute difference vs the previous trading day
    daily_change_pct  — percentage change vs the previous trading day

    Trend (moving averages)
    -----------------------
    rate_7d_avg       — 7-day rolling mean
    rate_30d_avg      — 30-day rolling mean

    Year-to-Date (YTD)
    ------------------
    ytd_start_rate    — first available rate of the calendar year for that pair
    ytd_change_pct    — % change from ytd_start_rate to the current rate
                        Populated for EVERY year in the dataset, not just the
                        current year, so Power BI slicers work across all years.
    """
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])

    # Time dimensions
    df["year"]        = df["date"].dt.year
    df["month"]       = df["date"].dt.month
    df["quarter"]     = df["date"].dt.quarter
    df["week"]        = df["date"].dt.isocalendar().week.astype(int)
    df["day_of_week"] = df["date"].dt.day_name()

    pair_grp = df.groupby(["base_currency", "quote_currency"])

    # Daily change (data is sorted by date within each pair group)
    prev_rate              = pair_grp["rate"].shift(1)
    df["daily_change"]     = (df["rate"] - prev_rate).round(6)
    df["daily_change_pct"] = ((df["daily_change"] / prev_rate) * 100).round(4)

    # Moving averages
    df["rate_7d_avg"] = pair_grp["rate"].transform(
        lambda x: x.rolling(7, min_periods=1).mean()
    ).round(6)
    df["rate_30d_avg"] = pair_grp["rate"].transform(
        lambda x: x.rolling(30, min_periods=1).mean()
    ).round(6)

    # YTD — group by pair AND year so each year gets its own baseline
    df["ytd_start_rate"] = df.groupby(
        ["base_currency", "quote_currency", "year"]
    )["rate"].transform("first").round(6)

    df["ytd_change_pct"] = (
        (df["rate"] - df["ytd_start_rate"]) / df["ytd_start_rate"] * 100
    ).round(4)

    return df


# ── 4. Validate ────────────────────────────────────────────────────────────────

def validate(df: pd.DataFrame):
    expected_pairs = len(CURRENCIES) * (len(CURRENCIES) - 1)
    actual_pairs   = df["pair_label"].nunique()

    log.info("── Validation ──────────────────────────────────────")
    log.info("  Total rows      : %s",   f"{len(df):,}")
    log.info("  Unique pairs    : %d  (expected %d)", actual_pairs, expected_pairs)
    log.info("  Date range      : %s → %s", df["date"].min().date(), df["date"].max().date())
    log.info("  Null rates      : %d",  df["rate"].isna().sum())
    log.info("  Null YTD %%     : %d",  df["ytd_change_pct"].isna().sum())
    log.info("────────────────────────────────────────────────────")

    if actual_pairs != expected_pairs:
        log.warning("Pair count mismatch — some currencies may be missing for part of the range")


# ── 5. Main ────────────────────────────────────────────────────────────────────

def main():
    log.info("FX Rate Pipeline — B2 Impact Technical Case")
    log.info("Currencies : %s", ", ".join(CURRENCIES))
    log.info("Period     : %s → %s", START_DATE, TODAY)
    log.info("Pairs      : %d ordered cross pairs", len(CURRENCIES) * (len(CURRENCIES) - 1))

    OUTPUT_DIR.mkdir(exist_ok=True)

    ecb_df   = fetch_ecb_rates(START_DATE)
    pairs_df = build_cross_pairs(ecb_df)
    final_df = add_metrics(pairs_df)

    validate(final_df)

    col_order = [
        "date", "year", "month", "quarter", "week", "day_of_week",
        "base_currency", "quote_currency", "pair_label",
        "rate",
        "daily_change", "daily_change_pct",
        "rate_7d_avg", "rate_30d_avg",
        "ytd_start_rate", "ytd_change_pct",
    ]
    float_cols = [
        "rate", "daily_change", "daily_change_pct",
        "rate_7d_avg", "rate_30d_avg", "ytd_start_rate", "ytd_change_pct",
    ]
    output_df = final_df[col_order].copy()
    for col in float_cols:
        output_df[col] = output_df[col].apply(
            lambda x: f"{x:.6f}".replace(".", ",") if pd.notna(x) else ""
        )
    output_df.to_csv(OUTPUT_FILE, index=False, date_format="%Y-%m-%d", sep=";")
    log.info("Saved → %s  (%s rows)", OUTPUT_FILE, f"{len(final_df):,}")
    log.info("Ready for Power BI.")


if __name__ == "__main__":
    main()
