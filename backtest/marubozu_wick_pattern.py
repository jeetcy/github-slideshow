"""Backtest: full candle followed by an opposite-colour candle with a rejection wick.

Bullish: C1 = full green candle (body >= FULL_RATIO of range, size >= MIN_SIZE x
         avg range of last 20 candles), C2 = red candle whose lower wick is
         >= WICK_RATIO of its range and bigger than its upper wick.
         -> buy at C3 open.
Bearish: mirror image (full red, then green candle with long upper wick) -> sell.

Exit: 1:1, SL and target = RR_FRAC x C1 body from entry (checked on 1-min bars,
SL assumed first if both hit in one minute), else time exit after MAX_HOLD
candles or 15:25. Also reports the plain "price after N candles" hit rate,
against the base rate of all candles.

Usage: python marubozu_wick_pattern.py /path/to/nifty50-historical-data
"""
import sys

import numpy as np
import pandas as pd

from big_candle_reversal import expiry_dates, load_1min, resample, stats

FULL_RATIO = 0.85
MIN_SIZE = 1.0
WICK_RATIO = 0.4
RR_FRAC = 0.5
MAX_HOLD = 12
HORIZONS = (3, 6, 12)


def find_signals(bars):
    o, h, l, c = bars["Open"], bars["High"], bars["Low"], bars["Close"]
    rng = (h - l).replace(0, np.nan)
    body = (c - o).abs()
    avg_rng = rng.shift(1).rolling(20).mean()
    upper = h - np.maximum(o, c)
    lower = np.minimum(o, c) - l
    full = (body / rng >= FULL_RATIO) & (body >= MIN_SIZE * avg_rng)
    green, red = c > o, c < o

    day = bars.index.normalize()
    same_day = pd.Series(day, index=bars.index).shift(1) == day
    late = bars.index.time > pd.Timestamp("15:00").time()

    bull = full.shift(1, fill_value=False) & green.shift(1, fill_value=False) & red \
        & (lower / rng >= WICK_RATIO) & (lower > upper)
    bear = full.shift(1, fill_value=False) & red.shift(1, fill_value=False) & green \
        & (upper / rng >= WICK_RATIO) & (upper > lower)
    sig = pd.Series(0, index=bars.index)
    sig[bull & same_day & ~late] = 1
    sig[bear & same_day & ~late] = -1
    return sig, body.shift(1)


def backtest(m1, tf):
    bars = resample(m1, tf)
    sig, c1_body = find_signals(bars)
    exp = expiry_dates(pd.DatetimeIndex(sorted(set(bars.index.normalize()))))
    m1_by_day = {d: g for d, g in m1.groupby(m1.index.normalize())}
    closes_by_day = {d: g["Close"] for d, g in bars.groupby(bars.index.normalize())}

    rows = []
    for ts in sig[sig != 0].index:
        d, direction = ts.normalize(), sig[ts]
        entry_time = ts + pd.Timedelta(minutes=tf)
        end_time = min(entry_time + pd.Timedelta(minutes=tf * MAX_HOLD), d + pd.Timedelta("15h25min"))
        path = m1_by_day[d].loc[entry_time:end_time - pd.Timedelta(minutes=1)]
        if path.empty:
            continue
        entry = path["Open"].iloc[0]
        risk = RR_FRAC * c1_body[ts]
        tgt, sl = entry + direction * risk, entry - direction * risk
        hit_sl = (path["Low"] <= sl) if direction == 1 else (path["High"] >= sl)
        hit_tg = (path["High"] >= tgt) if direction == 1 else (path["Low"] <= tgt)
        i_sl = hit_sl.values.argmax() if hit_sl.any() else len(path)
        i_tg = hit_tg.values.argmax() if hit_tg.any() else len(path)
        if i_sl == len(path) and i_tg == len(path):
            pnl = direction * (path["Close"].iloc[-1] - entry)
        elif i_sl <= i_tg:
            pnl = -risk
        else:
            pnl = risk
        row = {"ts": ts, "year": ts.year, "side": "bull" if direction == 1 else "bear",
               "expiry": d in exp, "pnl": pnl}
        closes = closes_by_day[d]
        pos = closes.index.get_loc(ts)
        for n in HORIZONS:
            row[f"move_{n}"] = direction * (closes.iloc[pos + n] - entry) if pos + n < len(closes) else np.nan
        rows.append(row)
    return pd.DataFrame(rows), bars


def base_rate(bars, n):
    """Share of all candles where price n candles later is above the next open (same day)."""
    day = bars.index.normalize()
    nxt_open = bars["Open"].groupby(day).shift(-1)
    later = bars["Close"].groupby(day).shift(-n)
    m = (later - nxt_open).dropna()
    return 100 * (m > 0).mean()


def report(t, bars, tf):
    print(f"\n=== NIFTY {tf}-min: full candle + opposite candle with rejection wick ===")
    print("Base rate (any candle) price up after N candles: "
          + ", ".join(f"{n}={base_rate(bars, n):.1f}%" for n in HORIZONS))
    rows = {}
    for name, g in {"ALL": t, "BULL (buy)": t[t.side == "bull"], "BEAR (sell)": t[t.side == "bear"],
                    "expiry": t[t.expiry], "non-expiry": t[~t.expiry]}.items():
        s = stats(g.pnl)
        r = {"signals": int(s.trades)}
        for n in HORIZONS:
            r[f"right dir after {n}"] = 100 * (g[f"move_{n}"].dropna() > 0).mean()
        r.update({"1:1 win%": s["win%"], "avg_pts": s.avg_pts, "PF": s.profit_factor})
        rows[name] = r
    print(pd.DataFrame(rows).T.round(2).to_string())
    y = t.groupby("year").pnl.apply(stats).unstack()
    print(f"Years profitable: {(y.total_pts > 0).sum()}/{len(y)}")


if __name__ == "__main__":
    root = sys.argv[1] if len(sys.argv) > 1 else "nifty50-historical-data"
    m1 = load_1min(root)
    for tf in (5, 15):
        t, bars = backtest(m1, tf)
        t.to_csv(f"marubozu_wick_trades_{tf}min.csv", index=False)
        report(t, bars, tf)
