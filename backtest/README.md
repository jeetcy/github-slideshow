# Big-candle reversal backtest (NIFTY 50)

`big_candle_reversal.py` tests whether NIFTY reverses after a big full-bodied
candle (body >= 2x average range of the last 20 candles, body >= 70% of range).

Data: 1-minute NIFTY 50 candles, Apr 2017 - 1 Oct 2026, from
https://github.com/technovusin/nifty50-historical-data

```
git clone --depth 1 https://github.com/technovusin/nifty50-historical-data
pip install pandas numpy
python backtest/big_candle_reversal.py nifty50-historical-data
```

## Result (5-min, 2065 signals)

| Trade after big candle | Win % | Avg pts | Profit factor |
|---|---|---|---|
| Reversal (opposite) | 43.0 | -2.57 | 0.77 |
| Follow (same direction) | 57.0 | +2.57 | 1.29 |

Reversal lost money in every year 2017-2026. Bigger candles (3x avg range)
continue even more often (follow win 64%, PF 1.55). Points are index points
before brokerage, slippage and option decay.
