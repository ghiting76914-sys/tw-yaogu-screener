#!/usr/bin/env python3
"""隔日沖：隔天不同賣法的比較（11 年回測）。

進場與網站相同：盤中漲到 +7%（開盤時還沒到 +7%）、接近 60 日高點（最高價 ≥ 60 日高 × 0.98）、
成交量 ≥ 1000 張，以 +7% 的價格買進。隔天的賣法：
  開盤賣（現行）／收盤賣
  掛限價 +2%、+3%、+5%：開盤就超過就用開盤價賣，盤中碰到就用限價賣，沒碰到收盤賣
  開高就賣、開低等回本：開盤 ≥ 買進價就開盤賣；否則掛買進價，盤中回到就賣，沒回到收盤賣
  一半開盤賣、一半掛 +3%
報酬已扣成本 0.585%。

  python3 research/overnight_exit.py
"""
import json
import os
import sys
from collections import defaultdict, deque

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import revenue_long as L  # noqa: E402

COST = 0.585


def limit_exit(entry, o, h, c, pct):
    tgt = entry * (1 + pct / 100)
    if o >= tgt:
        return o
    if h >= tgt:
        return tgt
    return c


EXITS = {
    "開盤賣（現行）": lambda e, o, h, l, c: o,
    "收盤賣": lambda e, o, h, l, c: c,
    "掛 +2% 限價，沒到收盤賣": lambda e, o, h, l, c: limit_exit(e, o, h, c, 2),
    "掛 +3% 限價，沒到收盤賣": lambda e, o, h, l, c: limit_exit(e, o, h, c, 3),
    "掛 +5% 限價，沒到收盤賣": lambda e, o, h, l, c: limit_exit(e, o, h, c, 5),
    "開高就賣，開低等回本": lambda e, o, h, l, c: o if o >= e else limit_exit(e, o, h, c, 0),
    "一半開盤、一半掛 +3%": lambda e, o, h, l, c: (o + limit_exit(e, o, h, c, 3)) / 2,
}


def main():
    days = L.trading_days()
    n = len(days)
    split = int(n * 0.75)
    window = deque(maxlen=61)
    pending, res = [], defaultdict(list)
    for t, ymd in enumerate(days):
        day = {}
        for m in ("twse", "tpex"):
            for r in json.load(open(os.path.join(L.CACHE_DIR, f"{m}_{ymd}.json"), encoding="utf-8")):
                if None not in (r["close"], r["open"], r["high"], r["low"], r["change"]):
                    day[r["code"]] = (r["open"], r["high"], r["low"], r["close"], r["change"], r["volume"])
        for code, entry, t0 in pending:
            x = day.get(code)
            if not x:
                continue
            o, h, l, c, ch, v = x
            if (c - ch) / entry < 0.9:  # 除權息、減資等價格斷層
                continue
            for name, fn in EXITS.items():
                res[(name, t0 >= split)].append((fn(entry, o, h, l, c) / entry - 1) * 100 - COST)
            res[("開盤低於買進價的比例", t0 >= split)].append(1 if o < entry else 0)
            res[("盤中最高高於開盤價的比例", t0 >= split)].append(1 if h > o else 0)
        pending = []
        window.append(day)
        if len(window) < 61:
            continue
        for code, (o, h, l, c, ch, v) in day.items():
            if len(code) != 4 or not code.isdigit() or code[0] == "0":
                continue
            prev = c - ch
            if prev <= 0 or v < 1_000_000 or (h / prev - 1) * 100 < 7 or (o / prev - 1) * 100 >= 7:
                continue
            past = [w[code][1] for w in list(window)[:-1] if code in w]
            if len(past) < 59 or h < max(past[-60:]) * 0.98:
                continue
            pending.append((code, prev * 1.07, t))

    def st(xs):
        return f"n={len(xs):5d} 平均{sum(xs) / len(xs):+6.2f}% 勝率{sum(x > 0 for x in xs) / len(xs) * 100:5.1f}%"

    print(f"{days[60]} ～ {days[-1]}；訓練期到 {days[split - 1]}，驗證期從 {days[split]}；已扣成本 {COST}%\n")
    for name in EXITS:
        print(f"{name:20s}\t訓練 {st(res[(name, False)])}\t驗證 {st(res[(name, True)])}")
    for name in ("開盤低於買進價的比例", "盤中最高高於開盤價的比例"):
        a, b = res[(name, False)], res[(name, True)]
        print(f"{name}：訓練 {sum(a) / len(a) * 100:.0f}%，驗證 {sum(b) / len(b) * 100:.0f}%")


if __name__ == "__main__":
    main()
