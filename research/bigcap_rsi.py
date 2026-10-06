#!/usr/bin/env python3
"""大型股短線反彈（RSI(2) 均值回歸，11 年回測）：能否做到勝率 60% 以上且扣成本後仍賺錢。

訊號：收盤 RSI(2) < 門檻、站上 200 日均線（長期多頭中的短線急跌），隔天開盤買進；
出場：收盤站上 5 日均線，或持有滿 5 天收盤賣。
股票範圍：全部／20 日均成交值前 100／前 50。指標用漲跌（change）計算，避免除權息造成假訊號。
報酬已扣成本 0.585%。

  python3 research/bigcap_rsi.py
"""
import json
import os
import sys
from collections import defaultdict, deque

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import revenue_long as L  # noqa: E402

COST = 0.585
MAX_HOLD = 5
UNIVERSES = (("全部", None), ("成交值前100", 100), ("成交值前50", 50))
THRESHOLDS = (10, 5)


def main():
    days = L.trading_days()
    n = len(days)
    split = int(n * 0.75)
    idx = {}                                   # 代號 -> 還原收盤指數
    closes = defaultdict(lambda: deque(maxlen=200))
    amounts = defaultdict(lambda: deque(maxlen=20))
    rsi = {}                                   # 代號 -> (平均漲, 平均跌)
    positions, res = [], defaultdict(list)

    for t, ymd in enumerate(days):
        day = {}
        for m in ("twse", "tpex"):
            for r in json.load(open(os.path.join(L.CACHE_DIR, f"{m}_{ymd}.json"), encoding="utf-8")):
                if None not in (r["close"], r["open"], r["change"]) and r["close"] - r["change"] > 0:
                    day[r["code"]] = (r["open"], r["close"], r["change"], r["volume"] * r["close"])
        prev_idx = dict(idx)
        for code, (o, c, ch, a) in day.items():
            ref = c - ch
            g = ch / ref
            if not -0.11 < g < 0.11:          # 異常值（減資、恢復交易等）重新開始
                closes.pop(code, None)
                rsi.pop(code, None)
                idx[code] = 100.0
                prev_idx.pop(code, None)
            else:
                idx[code] = idx.get(code, 100.0) * (1 + g)
                ag, al = rsi.get(code, (0.0, 0.0))
                rsi[code] = ((ag + max(g, 0)) / 2, (al + max(-g, 0)) / 2)
            closes[code].append(idx[code])
            amounts[code].append(a)

        still = []
        for p in positions:
            code = p["code"]
            if code not in day or code not in idx:
                still.append(p)
                continue
            o, c, ch, a = day[code]
            if p["entry"] is None:
                if code not in prev_idx:
                    continue
                p["entry"] = prev_idx[code] * o / (c - ch)
            p["k"] += 1
            cs = closes[code]
            ma5 = sum(list(cs)[-5:]) / 5 if len(cs) >= 5 else None
            if (ma5 and idx[code] > ma5) or p["k"] >= MAX_HOLD:
                ret = (idx[code] / p["entry"] - 1) * 100
                if -50 < ret < 100:
                    for g in p["groups"]:
                        res[(g, p["t"] >= split)].append(ret - COST)
                continue
            still.append(p)
        positions = still
        if t + MAX_HOLD + 1 >= n:
            continue

        ranked = sorted((sum(amounts[c]) / len(amounts[c]), c) for c in day
                        if len(c) == 4 and c.isdigit() and c[0] != "0" and len(amounts[c]) == 20)
        rank = {c: i for i, (_, c) in enumerate(reversed(ranked))}
        for code, r in rank.items():
            cs = closes[code]
            if len(cs) < 200 or code not in rsi or day[code][1] < 10:
                continue
            if idx[code] <= sum(cs) / 200:
                continue
            ag, al = rsi[code]
            val = 100 if al == 0 else 100 - 100 / (1 + ag / al)
            groups = [f"{u}／RSI2<{th}" for u, top in UNIVERSES for th in THRESHOLDS
                      if val < th and (top is None or r < top)]
            if groups:
                positions.append({"code": code, "groups": groups, "t": t, "entry": None, "k": 0})

    def st(xs):
        if len(xs) < 30:
            return f"n={len(xs):5d} 樣本太少"
        return f"n={len(xs):5d} 勝率{sum(x > 0 for x in xs) / len(xs) * 100:5.1f}% 平均{sum(xs) / len(xs):+6.2f}%"

    print(f"行情 {days[0]} ～ {days[-1]}；訓練期到 {days[split - 1]}，驗證期從 {days[split]}；已扣成本\n")
    for u, _ in UNIVERSES:
        for th in THRESHOLDS:
            g = f"{u}／RSI2<{th}"
            print(f"{g:18s}\t訓練 {st(res[(g, False)])}\t驗證 {st(res[(g, True)])}")


if __name__ == "__main__":
    main()
