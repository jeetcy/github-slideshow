"""Compare three NIFTY intraday strategies on 1-minute data (2017 -> 2026).

ORB      : opening range = 09:15-09:30. First 5-min close above OR high -> buy,
           below OR low -> sell (before ORB_LAST). SL = OR midpoint,
           target = ORB_RR x risk, else exit 15:15.
BIGFOLLOW: 5-min candle with body >= 3x avg range of last 20 candles and
           body >= 70% of range, 09:30-13:30, in the direction of the day so far
           (close vs day open). Entry next candle open, SL = 0.5 x body, 1:1.
TRENDDAY : at 10:15, if NIFTY has moved >= TREND_PCT from the day open,
           trade in that direction. SL = day open, exit 15:15.

Every trade is charged COST points (brokerage + slippage, futures-style).
Results are split into 2017-2022 (in-sample) and 2023-2026 (out-of-sample).

Usage: python strategies.py /path/to/nifty50-historical-data
"""
import sys

import numpy as np
import pandas as pd

from big_candle_reversal import expiry_dates, load_1min, resample, stats

COST = 1.5
ORB_RR = 1.5
ORB_LAST = "11:30"
TREND_PCT = 0.4
EXIT = "15:15:00"


def run_trade(path, direction, entry, sl, tgt):
    """Walk 1-min bars; SL wins ties. tgt=None means hold to the end of path."""
    hit_sl = (path["Low"] <= sl) if direction == 1 else (path["High"] >= sl)
    i_sl = hit_sl.values.argmax() if hit_sl.any() else len(path)
    i_tg = len(path)
    if tgt is not None:
        hit_tg = (path["High"] >= tgt) if direction == 1 else (path["Low"] <= tgt)
        i_tg = hit_tg.values.argmax() if hit_tg.any() else len(path)
    if i_sl == len(path) and i_tg == len(path):
        return direction * (path["Close"].iloc[-1] - entry)
    if i_sl <= i_tg:
        return direction * (sl - entry)
    return direction * (tgt - entry)


def orb(day, d):
    rng = day.between_time("09:15", "09:29")
    if len(rng) < 10:
        return None
    hi, lo = rng["High"].max(), rng["Low"].min()
    mid = (hi + lo) / 2
    b5 = resample(day.between_time("09:30", ORB_LAST), 5)
    for ts, b in b5.iterrows():
        direction = 1 if b["Close"] > hi else -1 if b["Close"] < lo else 0
        if direction:
            path = day.loc[ts + pd.Timedelta(minutes=5): d + pd.Timedelta(EXIT)]
            if path.empty:
                return None
            entry = path["Open"].iloc[0]
            risk = abs(entry - mid)
            if risk <= 0:
                return None
            return direction, run_trade(path, direction, entry, mid, entry + direction * ORB_RR * risk)
    return None


def trend_day(day, d):
    open_ = day["Open"].iloc[0]
    at = day.loc[:d + pd.Timedelta("10h14min")]
    if at.empty:
        return None
    move = 100 * (at["Close"].iloc[-1] / open_ - 1)
    if abs(move) < TREND_PCT:
        return None
    direction = 1 if move > 0 else -1
    path = day.loc[d + pd.Timedelta("10h15min"): d + pd.Timedelta(EXIT)]
    if path.empty:
        return None
    return direction, run_trade(path, direction, path["Open"].iloc[0], open_, None)


def big_follow(m1, by_day):
    bars = resample(m1, 5)
    body = bars["Close"] - bars["Open"]
    rng = (bars["High"] - bars["Low"]).replace(0, np.nan)
    avg = rng.shift(1).rolling(20).mean()
    big = (body.abs() >= 3 * avg) & (body.abs() / rng >= 0.7)
    t = bars.index.time
    big &= (t >= pd.Timestamp("09:30").time()) & (t <= pd.Timestamp("13:30").time())
    day_open = bars["Open"].groupby(bars.index.normalize()).transform("first")
    out = []
    for ts in bars.index[big]:
        b = bars.loc[ts]
        direction = 1 if b["Close"] > b["Open"] else -1
        if direction * (b["Close"] - day_open[ts]) <= 0:
            continue
        d = ts.normalize()
        path = by_day[d].loc[ts + pd.Timedelta(minutes=5): ts + pd.Timedelta(minutes=65)]
        if path.empty:
            continue
        entry, risk = path["Open"].iloc[0], 0.5 * abs(body[ts])
        out.append((ts, direction, run_trade(path, direction, entry, entry - direction * risk,
                                             entry + direction * risk)))
    return out


def main(root):
    m1 = load_1min(root)
    by_day = {d: g for d, g in m1.groupby(m1.index.normalize())}
    exp = expiry_dates(pd.DatetimeIndex(sorted(by_day)))
    rows = []
    for d, day in by_day.items():
        for name, fn in (("ORB", orb), ("TRENDDAY", trend_day)):
            r = fn(day, d)
            if r:
                rows.append({"strategy": name, "ts": d, "dir": r[0], "gross": r[1]})
    rows += [{"strategy": "BIGFOLLOW", "ts": ts, "dir": dr, "gross": p} for ts, dr, p in big_follow(m1, by_day)]
    t = pd.DataFrame(rows)
    t["net"] = t["gross"] - COST
    t["year"] = t.ts.dt.year
    t["expiry"] = t.ts.dt.normalize().isin(exp)
    t["period"] = np.where(t.year <= 2022, "2017-22", "2023-26")
    t.to_csv("strategy_trades.csv", index=False)

    out = []
    for (name, label), g in [((s, "ALL"), t[t.strategy == s]) for s in t.strategy.unique()] + \
            [((s, p), t[(t.strategy == s) & (t.period == p)]) for s in t.strategy.unique() for p in ("2017-22", "2023-26")] + \
            [((s, "expiry"), t[(t.strategy == s) & t.expiry]) for s in t.strategy.unique()]:
        s = stats(g.net)
        y = g.groupby("year").net.sum()
        out.append({"strategy": name, "slice": label, "trades": int(s.trades), "win%": s["win%"],
                    "gross_avg": g.gross.mean(), "net_avg": s.avg_pts, "net_total": s.total_pts,
                    "PF": s.profit_factor, "yrs+": f"{(y > 0).sum()}/{len(y)}"})
    print(f"Net = gross - {COST} pts per trade\n")
    print(pd.DataFrame(out).sort_values(["strategy", "slice"]).round(2).to_string(index=False))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "nifty50-historical-data")
