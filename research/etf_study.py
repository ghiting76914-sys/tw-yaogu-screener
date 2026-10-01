#!/usr/bin/env python3
"""0050 進場方式研究：每月投入固定金額，比較各種「等價位」買法與定期定額的平均成本。

所有價格皆用還原股價（含配息、分割）。限價單以前一天收盤後算出的價位掛單，
當天最低價碰到就成交（開盤已低於限價則以開盤價成交）；整個月都沒成交，月底收盤補買，
確保每種買法每月投入的金額相同、沒有閒置資金。

  python3 research/etf_study.py
"""
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import etf  # noqa: E402

COST = etf.COST
indicators, months, fill = etf.indicators, etf.months, etf.fill


def run(rows, strategy, start_i, end_i):
    """回傳 (投入金額, 買到的單位數)。每月投入 1 單位金額。"""
    invested = units = 0.0
    for idxs in months(rows):
        idxs = [i for i in idxs if start_i <= i <= end_i]
        if not idxs:
            continue
        for w, px in strategy(rows, idxs):
            invested += w
            units += w * (1 - COST) / px
    return invested, units


def month_end(rows, idxs):
    return rows[idxs[-1]]["adj_close"]


def limit_or_end(rows, idxs, fn):
    px = fill(rows, idxs, fn)
    return px if px is not None else month_end(rows, idxs)


STRATEGIES = {
    **etf.STRATEGIES,
    "前日收盤 -2% 限價": lambda rows, idxs: [(1, limit_or_end(rows, idxs, lambda p: p["adj_close"] * 0.98))],
    "過熱才等（乖離>8% 掛月線）": lambda rows, idxs: [(1, limit_or_end(
        rows, idxs, lambda p: p["ma20"] if p["ma60"] and p["adj_close"] / p["ma60"] - 1 > 0.08 else p["adj_close"] * 10))],
}


def main():
    rows = indicators(etf.load_history())
    start = 120  # 半年線需要 120 日
    n = len(rows)
    print(f"0050：{rows[start]['date']} ～ {rows[-1]['date']}，{n - start} 個交易日\n")
    thirds = [start, start + (n - start) // 3, start + 2 * (n - start) // 3, n - 1]
    periods = [("全期", start, n - 1)] + [
        (f"{rows[a]['date'][:7]}～{rows[b - 1]['date'][:7]}", a, b - 1) for a, b in zip(thirds, thirds[1:])]
    base = {}
    header = "".join(f"{p[0]:>22s}" for p in periods)
    print(f"{'買法（平均成本相對定期定額月初開盤）':28s}{header}")
    for name, fn in STRATEGIES.items():
        cells = []
        for pname, a, b in periods:
            inv, units = run(rows, fn, a, b)
            cost = inv / units
            if name.startswith("定期定額（月初"):
                base[pname] = cost
            cells.append(f"{(cost / base[pname] - 1) * 100:+21.2f}%")
        print(f"{name:28s}{''.join(cells)}")
    # 限價單成交率
    for name, fn in (("月線", lambda p: p["ma20"]), ("季線", lambda p: p["ma60"]), ("半年線", lambda p: p["ma120"])):
        ms = [m for m in months(rows) if m[0] >= start]
        hit = sum(fill(rows, m, fn) is not None for m in ms)
        print(f"\n{name}限價：{len(ms)} 個月中有 {hit} 個月成交（{hit / len(ms) * 100:.0f}%）", end="")
    print()


if __name__ == "__main__":
    main()
