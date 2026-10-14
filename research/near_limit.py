#!/usr/bin/env python3
"""兩個策略的詳細驗證（11 年，research/win80_scan.py 掃描出來的）：

A. 收盤接近漲停但沒鎖住（盤後定價）：當天收盤漲 ≥ 9.5% 但收盤價低於漲停價（收盤時有人賣、買得到），
   以收盤價（14:00～14:30 盤後定價）買進，隔天開盤賣（另測：+2% 限價、隔天收盤）。
   一般股、股價 ≥ 10、20 日均量 ≥ 500 張、當天量 ≥ 1000 張。分組：是否突破 60 日高、盤中是否碰過漲停、當天開盤跳空。
B. 股災反彈：近 5 日（還原）跌 > 10%、市場廣度 < 30%（流動股中站上月線的比例），當天漲停（收盤 ≥ 9.5%），
   隔天開盤買，持有 5 個交易日收盤賣。逐「事件日」列出。
報酬含除權息、已扣成本 0.585%。

  python3 research/near_limit.py
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
    idx, hist = {}, defaultdict(lambda: deque(maxlen=61))   # 還原指數；(還原收盤, 還原最高, 量)
    pend_a, pend_b, hold_b = [], [], []
    res = defaultdict(list)
    by_year = defaultdict(list)
    events = defaultdict(list)
    for t, ymd in enumerate(days):
        day = {}
        for m in ("twse", "tpex"):
            for r in json.load(open(os.path.join(L.CACHE_DIR, f"{m}_{ymd}.json"), encoding="utf-8")):
                if None not in (r["open"], r["high"], r["low"], r["close"], r["change"]) and r["close"] - r["change"] > 0:
                    day[r["code"]] = (r["open"], r["high"], r["low"], r["close"], r["change"], r["volume"] or 0)
        prev_idx = dict(idx)
        for code, (o, h, l, c, ch, v) in day.items():
            g = ch / (c - ch)
            if not -0.11 < g < 0.11:
                idx[code] = 100.0
                hist.pop(code, None)
                prev_idx.pop(code, None)
            else:
                idx[code] = idx.get(code, 100.0) * (1 + g)
            hist[code].append((idx[code], idx[code] * h / c, v))
        # A：昨天收盤買 → 今天賣
        for code, entry, t0, groups in pend_a:
            x = day.get(code)
            if not x or code not in prev_idx:
                continue
            o, h, l, c, ch, v = x
            ref = c - ch
            open_adj = prev_idx[code] * o / ref
            high_adj = prev_idx[code] * h / ref
            r_open = (open_adj / entry - 1) * 100 - COST
            r_close = (idx[code] / entry - 1) * 100 - COST
            tgt = entry * 1.02
            r_lim = ((open_adj if open_adj >= tgt else tgt if high_adj >= tgt else idx[code]) / entry - 1) * 100 - COST
            for gname in groups:
                for ex, rr in (("隔天開盤賣", r_open), ("+2%限價否則收盤", r_lim), ("隔天收盤賣", r_close)):
                    if -30 < rr < 30:
                        res[(gname, ex, t0 >= split)].append(rr)
            if "A 全部｜突破60日高" in groups and -30 < r_open < 30:
                by_year[days[t0][:4]].append(r_open)
        pend_a = []
        # B：昨天訊號 → 今天開盤買，持有 5 天
        for code, t0 in pend_b:
            x = day.get(code)
            if x and code in prev_idx:
                o, h, l, c, ch, v = x
                hold_b.append({"code": code, "t0": t0, "entry": prev_idx[code] * o / (c - ch), "k": 0})
        pend_b = []
        still = []
        for p in hold_b:
            if p["code"] in day and p["code"] in idx:
                p["k"] += 1
                if p["k"] >= 5:
                    rr = (idx[p["code"]] / p["entry"] - 1) * 100 - COST
                    if -60 < rr < 100:
                        events[days[p["t0"]]].append(rr)
                        res[("B 股災反彈", "隔天開盤買、持有5天", p["t0"] >= split)].append(rr)
                    continue
            still.append(p)
        hold_b = still
        if t < 61 or t + 7 >= n:
            continue
        # 市場廣度：流動股中站上月線的比例
        above = tot = 0
        for code, hs in hist.items():
            if len(hs) >= 20 and code in day and sum(x[2] for x in list(hs)[-20:]) / 20 >= 500_000:
                tot += 1
                above += hs[-1][0] > sum(x[0] for x in list(hs)[-20:]) / 20
        breadth = above / tot if tot else 0.5
        for code, (o, h, l, c, ch, v) in day.items():
            if len(code) != 4 or not code.isdigit() or code[0] == "0" or c < 10:
                continue
            hs = list(hist[code])
            if len(hs) < 61:
                continue
            vol20 = sum(x[2] for x in hs[-20:]) / 20
            if vol20 < 500_000:
                continue
            ref = c - ch
            g = c / ref - 1
            lim = tick_floor(ref * 1.1)
            if g >= 0.095 and c < lim and v >= 1_000_000:
                hi60 = max(x[1] for x in hs[:-1][-60:])
                brk = idx[code] > hi60
                touched = h >= lim
                gap = (o / ref - 1) * 100
                gs = ["A 全部", "A 全部｜突破60日高" if brk else "A 全部｜未突破",
                      "A 盤中碰過漲停" if touched else "A 沒碰過漲停",
                      "A 開盤跳空>3%" if gap > 3 else "A 開盤跳空≤3%"]
                if brk and gap > 3:
                    gs.append("A 突破60日高＋開盤跳空>3%")
                pend_a.append((code, idx[code], t, gs))
            r5 = hs[-1][0] / hs[-6][0] - 1
            if g >= 0.095 and r5 < -0.10 and breadth < 0.30:
                pend_b.append((code, t))

    def st(xs):
        if len(xs) < 30:
            return f"n={len(xs):5d} 樣本太少"
        return f"n={len(xs):5d} 平均{sum(xs) / len(xs):+6.2f}% 勝率{sum(x > 0 for x in xs) / len(xs) * 100:5.1f}%"

    print(f"{days[61]}～{days[-1]}；驗證期從 {days[split]}；已扣成本\n")
    for g in ["A 全部", "A 全部｜突破60日高", "A 全部｜未突破", "A 盤中碰過漲停", "A 沒碰過漲停",
              "A 開盤跳空>3%", "A 開盤跳空≤3%", "A 突破60日高＋開盤跳空>3%"]:
        for ex in ("隔天開盤賣", "+2%限價否則收盤", "隔天收盤賣"):
            print(f"{g:22s} {ex:12s}\t訓練 {st(res[(g, ex, False)])}\t驗證 {st(res[(g, ex, True)])}")
        print()
    print("A（突破 60 日高，隔天開盤賣）逐年：")
    for y, xs in sorted(by_year.items()):
        print(f"  {y}  n={len(xs):4d}  平均 {sum(xs) / len(xs):+.2f}%  勝率 {sum(x > 0 for x in xs) / len(xs) * 100:.0f}%")
    print(f"\nB 股災反彈：訓練 {st(res[('B 股災反彈', '隔天開盤買、持有5天', False)])}  驗證 {st(res[('B 股災反彈', '隔天開盤買、持有5天', True)])}")
    # 依「事件日當天有幾檔觸發」分組：很多檔同時觸發＝全市場反彈
    cnt = {d: len(xs) for d, xs in events.items()}
    for lo, hi_, name in ((1, 3, "當天 1～2 檔"), (3, 10, "當天 3～9 檔"), (10, 10**6, "當天 ≥10 檔")):
        for per, label in ((False, "訓練"), (True, "驗證")):
            xs = [x for d, v in events.items() if lo <= cnt[d] < hi_ and (d >= days[split]) == per for x in v]
            ds = [d for d in events if lo <= cnt[d] < hi_ and (d >= days[split]) == per]
            if xs:
                print(f"  {name} {label}：{len(ds)} 個事件日、{len(xs)} 筆、平均 {sum(xs) / len(xs):+.2f}%、勝率 {sum(x > 0 for x in xs) / len(xs) * 100:.0f}%、"
                      f"事件日勝率 {sum(sum(events[d]) > 0 for d in ds) / len(ds) * 100:.0f}%")
    by_month = defaultdict(list)
    for d, xs in events.items():
        by_month[d[:6]].extend(xs)
    print("逐月（事件集中在哪幾次）：")
    for mth, xs in sorted(by_month.items()):
        print(f"  {mth}  {len(xs):3d} 筆  平均 {sum(xs) / len(xs):+6.2f}%  勝率 {sum(x > 0 for x in xs) / len(xs) * 100:3.0f}%  "
              f"事件日 {sorted(d for d in events if d.startswith(mth))}")


if __name__ == "__main__":
    main()
