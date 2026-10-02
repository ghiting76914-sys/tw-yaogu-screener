#!/usr/bin/env python3
"""均值回歸研究：多頭趨勢中的股票短線急跌後買進，等反彈回 5 日均線出場。

訊號（第 t 天收盤後判斷）：
  - 趨勢：收盤 > 60 日均線，且 60 日均線高於 5 天前（長期向上）
  - 超賣：RSI(2) < 門檻，或連跌 3 天且累計跌幅 ≥ 5%
  - 流動性：20 日均量 ≥ 1000 張、股價 ≥ 10 元
進出場：第 t+1 天開盤買進；之後收盤高於 5 日均線就以收盤價賣出，最多持有 H 天，
可選擇停損。報酬含除權息還原（strategy.held_bars）、已扣交易成本。

  python3 research/meanrev.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import strategy  # noqa: E402
import study  # noqa: E402
from yaogu import pct_change  # noqa: E402

COST = strategy.ROUND_TRIP_COST


def rsi2_series(s):
    """RSI(2)，用每日漲跌（已排除除權息造成的價差）計算，Wilder 平滑。"""
    out = [None] * len(s)
    up = down = None
    for i, x in enumerate(s):
        if not x or x["change"] is None:
            up = down = None
            continue
        g, l = max(x["change"], 0), max(-x["change"], 0)
        if up is None:
            up, down = g, l
        else:
            up, down = (up + g) / 2, (down + l) / 2
        out[i] = 100.0 if down == 0 else 100 - 100 / (1 + up / down)
    return out


def signals(days, series, taiex):
    rows = []
    n = len(days)
    for code, s in series.items():
        rsi = rsi2_series(s)
        closes = [x["close"] if x else None for x in s]
        for t in range(65, n - 7):
            r = s[t]
            if not r or r["close"] is None or r["close"] < 10:
                continue
            win = closes[t - 64:t + 1]
            if any(c is None for c in win):
                continue
            ma60 = sum(win[-60:]) / 60
            ma60_prev = sum(win[-65:-5]) / 60
            if not (r["close"] > ma60 > ma60_prev):
                continue
            vols = [s[d]["volume"] for d in range(t - 19, t + 1) if s[d]]
            if len(vols) < 20 or sum(vols) / 20 < 1_000_000:
                continue
            p3 = [pct_change(s[d]) for d in (t - 2, t - 1, t)]
            three_down = all(p is not None and p < 0 for p in p3) and sum(p3) <= -5
            if rsi[t] is None or (rsi[t] >= 25 and not three_down):
                continue
            up, _ = study.market_state(days, taiex, t)
            rows.append({"t": t, "code": code, "rsi": rsi[t], "three_down": three_down,
                         "mkt_up": up, "bias60": r["close"] / ma60 - 1, "s": s})
    return rows


def trade(s, t, n, hold=5, stop=None):
    """t+1 開盤買；收盤 > 5 日均線（含當天）出場；最多 hold 天；stop 為停損比例（例如 0.08）。"""
    bars = strategy.held_bars(s, t + 1, n, hold)
    if not bars or bars[0][0] != t + 1:
        return None
    entry = bars[0][1]["open"]
    raw_closes = [x["close"] for x in s[t - 4:t + 1] if x]
    for d, b in bars:
        if stop and b["low"] <= entry * (1 - stop):
            px = entry * (1 - stop) if d == t + 1 else min(b["open"], entry * (1 - stop))
            return (px / entry - 1) * 100 - COST
        raw_closes.append(s[d]["close"])
        ma5 = sum(raw_closes[-5:]) / 5
        if s[d]["close"] > ma5:
            return (b["close"] / entry - 1) * 100 - COST
    return (bars[-1][1]["close"] / entry - 1) * 100 - COST


def stats(rs):
    if not rs:
        return "n=   0"
    avg = sum(rs) / len(rs)
    win = sum(x > 0 for x in rs) / len(rs) * 100
    gains = sum(x for x in rs if x > 0)
    losses = -sum(x for x in rs if x <= 0)
    pf = gains / losses if losses else float("inf")
    return f"n={len(rs):5d}  勝率 {win:4.1f}%  平均 {avg:+.2f}%  獲利因子 {pf:4.2f}  最差 {min(rs):+.1f}%"


def main():
    days, series, taiex = study.load()
    n = len(days)
    split = int(n * study.TRAIN_RATIO)
    print(f"資料 {days[0]} ～ {days[-1]}；訓練期到 {days[split - 1]}，驗證期從 {days[split]}\n")
    sig = signals(days, series, taiex)

    variants = [
        ("RSI2<10，最多 5 天", lambda r: r["rsi"] < 10, dict(hold=5)),
        ("RSI2<5，最多 5 天", lambda r: r["rsi"] < 5, dict(hold=5)),
        ("RSI2<10，最多 5 天，停損 8%", lambda r: r["rsi"] < 10, dict(hold=5, stop=0.08)),
        ("RSI2<10，最多 10 天", lambda r: r["rsi"] < 10, dict(hold=10)),
        ("連跌 3 天累計 -5%", lambda r: r["three_down"], dict(hold=5)),
        ("RSI2<10 + 大盤多頭", lambda r: r["rsi"] < 10 and r["mkt_up"] is True, dict(hold=5)),
        ("RSI2<10 + 大盤空頭", lambda r: r["rsi"] < 10 and r["mkt_up"] is False, dict(hold=5)),
        ("RSI2<10 + 距季線 < 5%", lambda r: r["rsi"] < 10 and r["bias60"] < 0.05, dict(hold=5)),
    ]
    print(f"{'規則':28s}{'訓練期':60s}驗證期")
    for name, cond, kw in variants:
        out = {0: [], 1: []}
        for r in sig:
            if cond(r):
                res = trade(r["s"], r["t"], n, **kw)
                if res is not None:
                    out[r["t"] >= split].append(res)
        print(f"{name:24s}\t{stats(out[0])}\t{stats(out[1])}")

    # 對照組：同樣站上 60 日均線、流動性足夠的股票，隨便一天買進、同樣出場規則
    import random
    random.seed(0)
    base = {0: [], 1: []}
    for code, s in series.items():
        for t in range(70, n - 7, 7):
            r = s[t]
            if not r or r["close"] is None or r["close"] < 10:
                continue
            closes = [x["close"] if x else None for x in s[t - 64:t + 1]]
            if any(c is None for c in closes) or not closes[-1] > sum(closes[-60:]) / 60:
                continue
            vols = [s[d]["volume"] for d in range(t - 19, t + 1) if s[d]]
            if len(vols) < 20 or sum(vols) / 20 < 1_000_000:
                continue
            res = trade(s, t, n, hold=5)
            if res is not None:
                base[t >= split].append(res)
    print(f"{'對照：多頭股隨便買、同出場規則':20s}\t{stats(base[0])}\t{stats(base[1])}")


if __name__ == "__main__":
    main()
