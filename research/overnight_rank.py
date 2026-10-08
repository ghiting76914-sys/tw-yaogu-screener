#!/usr/bin/env python3
"""隔日沖排序研究（11 年）：在隔日沖名單裡，哪些盤中看得到的條件跟「+7% 買、隔天開盤賣」的報酬有關。

進場與網站相同（盤中漲到 +7%、開盤 < +7%、最高價 ≥ 60 日高 × 0.98、成交量 ≥ 1000 張、股價 ≥ 10），以 +7% 價格買進。
分組條件（盤中 12:30 大致看得到的）：
  曾碰到漲停、起漲第 1 根、量比（當天量 ÷ 20 日均量）、突破或只是接近 60 日高、開盤漲幅、股價、市場、20 日均量
報酬已扣成本 0.585%。

  python3 research/overnight_rank.py
"""
import json
import os
import sys
from collections import defaultdict, deque

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import revenue_long as L  # noqa: E402

COST = 0.585


def tick_floor(p):
    for limit, tick in ((10, 0.01), (50, 0.05), (100, 0.1), (500, 0.5), (1000, 1)):
        if p < limit:
            break
    else:
        tick = 5
    return round(int(round(p / tick, 6)) * tick, 2)


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
                    day[r["code"]] = (r["open"], r["high"], r["low"], r["close"], r["change"], r["volume"], m)
        for code, entry, t0, groups in pending:
            x = day.get(code)
            if not x or (x[3] - x[4]) / entry < 0.85:
                continue
            ret = (x[0] / entry - 1) * 100 - COST
            for g in groups:
                res[(g, t0 >= split)].append(ret)
        pending = []
        window.append(day)
        if len(window) < 61:
            continue
        for code, (o, h, l, c, ch, v, mkt) in day.items():
            if len(code) != 4 or not code.isdigit() or code[0] == "0" or c < 10:
                continue
            prev = c - ch
            if prev <= 0 or v < 1_000_000 or h / prev < 1.07 or o / prev >= 1.07:
                continue
            past = [w[code] for w in list(window)[:-1] if code in w]
            if len(past) < 59:
                continue
            hi60 = max(x[1] for x in past[-60:])
            if h < hi60 * 0.98:
                continue
            vol20 = sum(x[5] for x in past[-20:]) / 20
            vr = v / vol20 if vol20 else 99
            closes = [x[3] for x in past[-20:]]
            quiet = max(closes) / min(closes) <= 1.15 and not any(
                x[3] - x[4] > 0 and x[4] / (x[3] - x[4]) >= 0.05 for x in past[-20:])
            touched = h >= tick_floor(prev * 1.1)
            op = (o / prev - 1) * 100
            g = ["全部",
                 "曾碰到漲停" if touched else "沒碰到漲停",
                 "起漲第1根" if quiet and vr >= 2 and vol20 >= 500_000 else "非第1根",
                 "量比<2" if vr < 2 else "量比2~5" if vr < 5 else "量比5~10" if vr < 10 else "量比≥10",
                 "突破60日高" if h > hi60 else "接近60日高",
                 "開盤<0%" if op < 0 else "開盤0~3%" if op < 3 else "開盤3~7%",
                 "股價<30" if c < 30 else "股價30~100" if c < 100 else "股價≥100",
                 "上市" if mkt == "twse" else "上櫃",
                 "均量<500張" if vol20 < 500_000 else "均量500~3000張" if vol20 < 3_000_000 else "均量≥3000張"]
            first = quiet and vr >= 2 and vol20 >= 500_000
            score = 2 * first + (vr < 2) + (h > hi60)
            g.append(f"推薦分數{score}")
            if touched:
                g.append("曾碰漲停＋起漲第1根" if quiet and vr >= 2 and vol20 >= 500_000 else "曾碰漲停＋非第1根")
                # 已經漲停才看到：只能用漲停價排隊買
                lim = tick_floor(prev * 1.1)
                locked = c >= lim
                pending.append((code, lim, t, ["曾碰漲停｜用漲停價買",
                                               "收盤鎖漲停｜用漲停價買" if locked else "漲停被打開｜用漲停價買"]))
            pending.append((code, prev * 1.07, t, g))

    def st(xs):
        if len(xs) < 50:
            return f"n={len(xs):5d} 樣本太少"
        return f"n={len(xs):5d} 平均{sum(xs) / len(xs):+6.2f}% 勝率{sum(x > 0 for x in xs) / len(xs) * 100:5.1f}%"

    print(f"{days[60]} ～ {days[-1]}；訓練期到 {days[split - 1]}，驗證期從 {days[split]}；已扣成本\n")
    order = ["全部", "曾碰到漲停", "沒碰到漲停", "起漲第1根", "非第1根", "曾碰漲停＋起漲第1根", "曾碰漲停＋非第1根",
             "量比<2", "量比2~5", "量比5~10", "量比≥10", "突破60日高", "接近60日高",
             "開盤<0%", "開盤0~3%", "開盤3~7%", "股價<30", "股價30~100", "股價≥100", "上市", "上櫃",
             "均量<500張", "均量500~3000張", "均量≥3000張",
             "曾碰漲停｜用漲停價買", "收盤鎖漲停｜用漲停價買", "漲停被打開｜用漲停價買",
             "推薦分數0", "推薦分數1", "推薦分數2", "推薦分數3", "推薦分數4"]
    for g in order:
        print(f"{g:16s}\t訓練 {st(res[(g, False)])}\t驗證 {st(res[(g, True)])}")


if __name__ == "__main__":
    main()
