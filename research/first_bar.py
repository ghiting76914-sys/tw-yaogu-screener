#!/usr/bin/env python3
"""起漲第 1 根／第 2 根研究（11 年）。

第 1 根（第 t 天收盤後確認）：一般股、股價 ≥ 10、20 日均量 ≥ 500 張；
  前 20 天盤整：收盤最高 ÷ 最低 ≤ 1.15，且沒有任何一天漲 ≥ 5%；
  當天漲幅 ≥ 5%（另測 ≥ 9.5% 漲停版）、收盤突破前 60 日最高價、成交量 ≥ 20 日均量 × 2。
買法：
  A 第 1 根收盤買（收盤鎖漲停者另列，實際多半買不到）
  B 隔天開盤買
  C 第 2 根收紅（收盤 > 第 1 根收盤）才在第 2 根收盤買
持有 5／10／20 個交易日收盤賣。對照組：同樣流動性條件的任意股票、任意一天收盤買（每 5 天抽樣）。
價格用漲跌（change）還原，含除權息；報酬已扣成本 0.585%。

  python3 research/first_bar.py
"""
import json
import os
import sys
from collections import defaultdict, deque

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import revenue_long as L  # noqa: E402

COST = 0.585
HOLDS = (5, 10, 20)


def main():
    days = L.trading_days()
    n = len(days)
    split = int(n * 0.75)
    idx = {}                                        # 代號 -> 還原收盤指數
    hist = defaultdict(lambda: deque(maxlen=61))    # 代號 -> [(還原收盤, 還原最高, 量, 漲幅%)]
    positions = []                                  # {"groups", "entry", "k", "t", "code", "need_open"}
    second = []                                     # 等第 2 根確認 [(code, groups, t, 第1根還原收盤)]
    nextopen = []                                   # 隔天開盤買 [(code, groups, t)]
    nextopen_sell = []                              # 隔天開盤賣（隔日沖）[(code, group, t, entry)]
    res = defaultdict(list)
    for t, ymd in enumerate(days):
        day = {}
        for m in ("twse", "tpex"):
            for r in json.load(open(os.path.join(L.CACHE_DIR, f"{m}_{ymd}.json"), encoding="utf-8")):
                if None not in (r["close"], r["open"], r["high"], r["change"]) and r["close"] - r["change"] > 0:
                    day[r["code"]] = (r["open"], r["high"], r["close"], r["change"], r["volume"])
        prev_idx = dict(idx)
        for code, (o, h, c, ch, v) in day.items():
            ref = c - ch
            g = ch / ref
            if not -0.11 < g < 0.11:
                idx[code] = 100.0
                hist.pop(code, None)
                prev_idx.pop(code, None)
            else:
                idx[code] = idx.get(code, 100.0) * (1 + g)
            hist[code].append((idx[code], idx[code] * h / c, v, g * 100))

        # 隔天開盤買
        for code, groups, t0 in nextopen:
            if code in day and code in prev_idx:
                o, h, c, ch, v = day[code]
                positions.append({"code": code, "groups": groups, "entry": prev_idx[code] * o / (c - ch), "k": 0, "t": t0})
        nextopen = []
        for code, g, t0, ent in nextopen_sell:
            if code in day and code in prev_idx:
                o, h, c, ch, v = day[code]
                ret = (prev_idx[code] * o / (c - ch) / ent - 1) * 100 - COST
                if -30 < ret < 30:
                    for hd in HOLDS:
                        res[(g + "｜隔天開盤賣", hd, t0 >= split)].append(ret)
        nextopen_sell = []
        # 第 2 根確認
        for code, groups, t0, c1 in second:
            if code in day and code in idx and idx[code] > c1:
                positions.append({"code": code, "groups": groups, "entry": idx[code], "k": -1, "t": t0})
        second = []
        # 持有中
        still = []
        for p in positions:
            if p["code"] not in day or p["code"] not in idx:
                still.append(p)
                continue
            p["k"] += 1
            for hd in HOLDS:
                if p["k"] == hd:
                    ret = (idx[p["code"]] / p["entry"] - 1) * 100 - COST
                    if -60 < ret < 200:
                        for g in p["groups"]:
                            res[(g, hd, p["t"] >= split)].append(ret)
            if p["k"] < max(HOLDS):
                still.append(p)
        positions = still
        if t < 61 or t + max(HOLDS) + 2 >= n:
            continue

        for code, (o, h, c, ch, v) in day.items():
            if len(code) != 4 or not code.isdigit() or code[0] == "0" or c < 10:
                continue
            hs = hist[code]
            if len(hs) < 61:
                continue
            past = list(hs)[:-1]
            vol20 = sum(x[2] for x in past[-20:]) / 20
            if vol20 < 500_000:
                continue
            if t % 5 == 0:
                positions.append({"code": code, "groups": ["對照組：任意股票收盤買"], "entry": idx[code], "k": 0, "t": t})
            pct = ch / (c - ch) * 100
            ref = c - ch
            if h / ref >= 1.07 and o / ref < 1.07 and v >= 1_000_000 and idx[code] * h / c >= max(x[1] for x in past[-60:]) * 0.98:
                base = [x[0] for x in past[-20:]]
                first = max(base) / min(base) <= 1.15 and not any(x[3] >= 5 for x in past[-20:]) and v >= vol20 * 2
                g = "D 隔日沖進場(+7%)：" + ("起漲第1根" if first else "非第1根")
                ent = idx[code] * ref * 1.07 / c
                positions.append({"code": code, "groups": [g], "entry": ent, "k": 0, "t": t})
                nextopen_sell.append((code, g, t, ent))
            if pct < 5 or v < vol20 * 2 or idx[code] <= max(x[1] for x in past[-60:]):
                continue
            base = [x[0] for x in past[-20:]]
            if max(base) / min(base) > 1.15 or any(x[3] >= 5 for x in past[-20:]):
                continue
            lvls = ["漲 ≥5%"] + (["漲停 ≥9.5%"] if pct >= 9.5 else [])
            locked = pct >= 9.5 and h == c
            a = [f"{lv}｜A 第1根收盤買" for lv in lvls]
            a += [f"{lv}｜A 第1根收盤買（未鎖漲停，買得到）" for lv in lvls if not locked]
            positions.append({"code": code, "groups": a, "entry": idx[code], "k": 0, "t": t})
            nextopen.append((code, [f"{lv}｜B 隔天開盤買" for lv in lvls], t))
            second.append((code, [f"{lv}｜C 第2根收紅才收盤買" for lv in lvls], t, idx[code]))

    def st(xs):
        if len(xs) < 30:
            return f"n={len(xs):5d} 樣本太少"
        return f"n={len(xs):5d} 平均{sum(xs) / len(xs):+6.2f}% 勝率{sum(x > 0 for x in xs) / len(xs) * 100:5.1f}%"

    print(f"{days[61]} ～ {days[-1]}；訓練期到 {days[split - 1]}，驗證期從 {days[split]}；已扣成本\n")
    names = sorted({g for g, _, _ in res}, key=lambda g: (not g.startswith("對照"), g))
    for hd in HOLDS:
        print(f"===== 持有 {hd} 天 =====")
        for g in names:
            print(f"{g:34s}\t訓練 {st(res[(g, hd, False)])}\t驗證 {st(res[(g, hd, True)])}")
        print()


if __name__ == "__main__":
    main()
