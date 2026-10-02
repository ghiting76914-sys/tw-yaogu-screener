#!/usr/bin/env python3
"""營收動能長期驗證（2015 年起）：每月 11 日後第一個交易日收盤選股，隔天開盤買進，持有 20 個交易日。

選股：營收創 12 個月新高、營收年增 > 20%、20 日均量 ≥ 500 張、股價 ≥ 10 元、站上 60 日均線（可選），
依 20 日成交值排序取前 N 檔。對照組為所有流動性足夠的股票。
報酬含除權息還原、已扣交易成本；每月等權重。

為了節省記憶體，逐日讀取行情、只保留最近 60 天，不一次載入全部資料。

  python3 research/revenue_long.py
"""
import glob
import os
import sys
from collections import defaultdict, deque

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import json  # noqa: E402

import revenue_study as R  # noqa: E402
import strategy  # noqa: E402
from yaogu import CACHE_DIR  # noqa: E402

HOLD = 20
COST = strategy.ROUND_TRIP_COST


def trading_days():
    out = []
    for f in sorted(glob.glob(os.path.join(CACHE_DIR, "twse_*.json"))):
        ymd = os.path.basename(f)[5:13]
        tp = os.path.join(CACHE_DIR, f"tpex_{ymd}.json")
        if os.path.exists(tp) and os.path.getsize(f) > 10 and os.path.getsize(tp) > 10:
            out.append(ymd)
    return out


def load_day(ymd):
    day = {}
    for m in ("twse", "tpex"):
        for r in json.load(open(os.path.join(CACHE_DIR, f"{m}_{ymd}.json"), encoding="utf-8")):
            if r["close"] is not None and r["open"] is not None:
                day[r["code"]] = (r["open"], r["close"], r["change"], r["volume"])
    return day


def run(variants):
    days = trading_days()
    rev = R.load_revenue()
    window = deque(maxlen=60)       # 最近 60 天 {代號: (open, close, change, volume)}
    open_pos = []                    # 持有中的部位
    results = defaultdict(list)      # (月份, 組別) -> [報酬]
    seen = set()
    for t, ymd in enumerate(days):
        day = load_day(ymd)
        # 更新持有中部位
        still = []
        for p in open_pos:
            x = day.get(p["code"])
            if x is None:            # 暫停交易：沿用前一天，持有天數照算
                p["left"] -= 1
            else:
                o, c, ch, _ = x
                if p["entry"] is None:
                    p["entry"] = o
                elif ch is not None and p["last"]:
                    gap = p["last"] - (c - ch)
                    if gap > p["last"] * 0.003:
                        p["cum"] += gap   # 除權息：加回價差
                p["last"] = c
                p["value"] = c + p["cum"]
                p["left"] -= 1
            if p["left"] <= 0:
                if p["entry"]:
                    ret = (p["value"] / p["entry"] - 1) * 100
                    if -66 < ret < 160:  # 排除減資、分割等價格斷層
                        for g in p["groups"]:
                            results[(p["month"], g)].append(ret - COST)
            else:
                still.append(p)
        open_pos = still
        window.append(day)

        # 每月 11 日後第一個交易日收盤選股
        if int(ymd[6:]) >= 11 and ymd[:6] not in seen and len(window) == 60 and t + HOLD < len(days):
            seen.add(ymd[:6])
            cands = []
            for code, (o, c, ch, v) in day.items():
                if len(code) != 4 or not code.isdigit() or code[0] == "0" or c < 10:
                    continue
                vols = [w[code][3] for w in list(window)[-20:] if code in w]
                closes = [w[code][1] for w in window if code in w]
                if len(vols) < 20 or sum(vols) / 20 < 500_000 or len(closes) < 60:
                    continue
                f = R.rev_features(rev, code, ymd)
                cands.append({"code": code, "f": f, "liq": sum(w[code][1] * w[code][3] for w in list(window)[-20:]),
                              "above60": c > sum(closes) / 60})
            groups = defaultdict(list)
            for x in cands:
                groups[x["code"]].append("對照組")
            for name, cond, trend, top in variants:
                sel = [x for x in cands if cond(x["f"]) and (not trend or x["above60"])]
                sel.sort(key=lambda x: -x["liq"])
                for x in (sel[:top] if top else sel):
                    groups[x["code"]].append(name)
            for code, gs in groups.items():
                open_pos.append({"code": code, "month": ymd[:6], "groups": gs, "entry": None,
                                 "cum": 0.0, "last": None, "value": None, "left": HOLD})
    return days, results


def main():
    cond = lambda f: f and f["high12"] and f["yoy"] > 20  # noqa: E731
    variants = [
        ("全部符合", cond, False, None),
        ("成交值前20", cond, False, 20),
        ("成交值前10", cond, False, 10),
        ("前20+站上季線", cond, True, 20),
    ]
    days, res = run(variants)
    months = sorted({m for m, _ in res})
    names = ["對照組"] + [v[0] for v in variants]
    print(f"行情 {days[0]} ～ {days[-1]}，{len(months)} 次選股（{months[0]} ～ {months[-1]}）\n")

    def avg(xs):
        return sum(xs) / len(xs) if xs else None

    monthly = {m: {g: avg(res.get((m, g), [])) for g in names} for m in months}
    print(f"{'年度':6s}" + "".join(f"{g:>14s}" for g in names) + "   （月平均報酬；括號內為贏過對照組的月數）")
    by_year = defaultdict(list)
    for m in months:
        by_year[m[:4]].append(m)
    for y, ms in sorted(by_year.items()):
        line = f"{y:6s}"
        for g in names:
            vals = [monthly[m][g] for m in ms if monthly[m][g] is not None]
            if not vals:
                line += f"{'–':>14s}"
                continue
            if g == "對照組":
                line += f"{avg(vals):+13.2f}%"
            else:
                beat = sum(monthly[m][g] > monthly[m]["對照組"] for m in ms if monthly[m][g] is not None)
                line += f"{avg(vals):+8.2f}%({beat:2d}/{len(vals):2d})"
        print(line)
    print()
    for g in names[1:]:
        ms = [m for m in months if monthly[m][g] is not None]
        diff = [monthly[m][g] - monthly[m]["對照組"] for m in ms]
        trades = [x for m in ms for x in res[(m, g)]]
        print(f"{g:12s} 贏過對照組 {sum(d > 0 for d in diff)}/{len(ms)} 個月，平均每月多 {avg(diff):+.2f}%，"
              f"個股勝率 {sum(x > 0 for x in trades) / len(trades) * 100:.1f}%（對照組 "
              f"{sum(x > 0 for m in ms for x in res[(m, '對照組')]) / sum(len(res[(m, '對照組')]) for m in ms) * 100:.1f}%），"
              f"最差月 {min(monthly[m][g] for m in ms):+.1f}%")


if __name__ == "__main__":
    main()
