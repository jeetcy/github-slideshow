"""Backtest: does the market reverse after a big full-bodied candle?

Signal  : a candle whose body >= BODY_MULT x average range of the previous
          20 candles, and whose body is >= MIN_BODY_RATIO of its own range
          (small wicks, "full" candle).
Reversal: big green -> short, big red -> long (entry = next candle's open).
Follow  : the opposite trade (go with the big candle), same rules.
Exit    : 1:1 risk-reward, SL and target = RR_FRAC x big candle's body from
          entry, checked on 1-minute bars (if both hit in the same minute,
          SL is assumed). Otherwise time exit after MAX_HOLD candles or 15:25.

Data: NIFTY 50 1-minute CSVs from github.com/technovusin/nifty50-historical-data
Usage: python big_candle_reversal.py /path/to/nifty50-historical-data
"""
import glob
import sys

import numpy as np
import pandas as pd

BODY_MULT = 2.0
MIN_BODY_RATIO = 0.7
RR_FRAC = 0.5
MAX_HOLD = 12
LAST_SIGNAL = "15:00"
TUESDAY_EXPIRY_FROM = pd.Timestamp("2025-09-01")
WEEKLY_EXPIRY_FROM = pd.Timestamp("2019-02-11")


def load_1min(root):
    files = sorted(glob.glob(f"{root}/1min/*/*.csv"))
    df = pd.concat((pd.read_csv(f, usecols=range(6)) for f in files), ignore_index=True)
    df["ts"] = pd.to_datetime(df["Timestamp"])
    df = df.set_index("ts")[["Open", "High", "Low", "Close"]]
    return df.between_time("09:15", "15:29")


def resample(m1, tf):
    day = m1.index.normalize()
    mins = (m1.index - day - pd.Timedelta("9h15min")) // pd.Timedelta(minutes=tf)
    start = day + pd.Timedelta("9h15min") + mins * pd.Timedelta(minutes=tf)
    g = m1.groupby(start)
    return pd.DataFrame({"Open": g["Open"].first(), "High": g["High"].max(),
                         "Low": g["Low"].min(), "Close": g["Close"].last()})


def expiry_dates(days):
    """Weekly Nifty expiry: Thursday (Tuesday from Sep 2025); if holiday, the trading day before."""
    s = pd.Series(days, index=days)
    out = set()
    for _, wk in s.groupby(days.to_period("W-SUN")):
        target = 1 if wk.iloc[0] >= TUESDAY_EXPIRY_FROM else 3
        cand = wk[wk.dt.weekday <= target]
        if len(cand) and wk.iloc[0] >= WEEKLY_EXPIRY_FROM:
            out.add(cand.iloc[-1])
    return out


def backtest(m1, tf):
    bars = resample(m1, tf)
    body = bars["Close"] - bars["Open"]
    rng = (bars["High"] - bars["Low"]).replace(0, np.nan)
    avg_rng = rng.shift(1).rolling(20).mean()
    big = (body.abs() >= BODY_MULT * avg_rng) & (body.abs() / rng >= MIN_BODY_RATIO)
    day = bars.index.normalize()
    first_bar = bars.index.time == pd.Timestamp("09:15").time()
    late = bars.index.time > pd.Timestamp(LAST_SIGNAL).time()
    sig = bars[big & ~first_bar & ~late]

    exp = expiry_dates(pd.DatetimeIndex(sorted(set(day))))
    m1_by_day = {d: g for d, g in m1.groupby(m1.index.normalize())}
    rows = []
    for ts, b in sig.iterrows():
        d = ts.normalize()
        entry_time = ts + pd.Timedelta(minutes=tf)
        end_time = min(entry_time + pd.Timedelta(minutes=tf * MAX_HOLD), d + pd.Timedelta("15h25min"))
        path = m1_by_day[d].loc[entry_time:end_time - pd.Timedelta(minutes=1)]
        if path.empty:
            continue
        entry = path["Open"].iloc[0]
        risk = RR_FRAC * abs(b["Close"] - b["Open"])
        green = b["Close"] > b["Open"]
        res = {}
        for mode, direction in (("reversal", -1 if green else 1), ("follow", 1 if green else -1)):
            tgt, sl = entry + direction * risk, entry - direction * risk
            hit_sl = (path["Low"] <= sl) if direction == 1 else (path["High"] >= sl)
            hit_tg = (path["High"] >= tgt) if direction == 1 else (path["Low"] <= tgt)
            i_sl = hit_sl.values.argmax() if hit_sl.any() else len(path)
            i_tg = hit_tg.values.argmax() if hit_tg.any() else len(path)
            if i_sl == len(path) and i_tg == len(path):
                pnl, how = direction * (path["Close"].iloc[-1] - entry), "time"
            elif i_sl <= i_tg:
                pnl, how = -risk, "sl"
            else:
                pnl, how = risk, "target"
            res[mode] = (pnl, how)
        rows.append({"ts": ts, "year": ts.year, "color": "green" if green else "red",
                     "expiry": d in exp, "body": abs(b["Close"] - b["Open"]),
                     "rev_pnl": res["reversal"][0], "rev_exit": res["reversal"][1],
                     "fol_pnl": res["follow"][0], "fol_exit": res["follow"][1]})
    return pd.DataFrame(rows)


def stats(pnl):
    wins, losses = pnl[pnl > 0], pnl[pnl < 0]
    pf = wins.sum() / -losses.sum() if len(losses) else np.inf
    return pd.Series({"trades": len(pnl), "win%": 100 * len(wins) / max(len(pnl), 1),
                      "avg_pts": pnl.mean(), "total_pts": pnl.sum(), "profit_factor": pf})


def report(t, label):
    print(f"\n=== {label} ===")
    groups = {"ALL": t, "expiry days": t[t.expiry], "non-expiry": t[~t.expiry],
              "after GREEN": t[t.color == "green"], "after RED": t[t.color == "red"],
              "expiry + GREEN": t[t.expiry & (t.color == "green")],
              "expiry + RED": t[t.expiry & (t.color == "red")]}
    out = []
    for name, g in groups.items():
        r = stats(g.rev_pnl).add_prefix("REV ")
        f = stats(g.fol_pnl)[["win%", "avg_pts", "total_pts", "profit_factor"]].add_prefix("FOL ")
        out.append(pd.concat([r, f]).rename(name))
    print(pd.DataFrame(out).round(2).to_string())
    print("\nReversal by year:")
    print(t.groupby("year").rev_pnl.apply(stats).unstack().round(2).to_string())


if __name__ == "__main__":
    root = sys.argv[1] if len(sys.argv) > 1 else "nifty50-historical-data"
    m1 = load_1min(root)
    print(f"Loaded {len(m1):,} 1-min bars, {m1.index[0]} -> {m1.index[-1]}")
    for tf in (5, 15):
        t = backtest(m1, tf)
        t.to_csv(f"big_candle_trades_{tf}min.csv", index=False)
        report(t, f"NIFTY {tf}-min big candle (body >= {BODY_MULT}x avg range, body/range >= {MIN_BODY_RATIO})")
