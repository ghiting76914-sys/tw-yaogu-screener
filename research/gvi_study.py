#!/usr/bin/env python3
"""GVI（成長價值指標，葉怡成教授）回測，並與營收動能比較（11 年）。

  GVI = (每股淨值 ÷ 股價) × (1 + ROE)^5 = (1 ÷ PB) × (1 + PB ÷ PE)^5
  （ROE 用近四季 EPS ÷ 目前每股淨值 = PB ÷ PE；虧損、PB ≤ 0 的股票不計）

本益比、淨值比為換股日當天證交所／櫃買公布的數字（財報公布後才更新，不會偷看未來）。
選股日與 research/revenue_long.py 相同（每月 11 日後第一個交易日收盤），隔天開盤買、持有 20 天，
股票範圍：一般股、股價 ≥ 10、20 日均量 ≥ 500 張。報酬含除權息、已扣成本。

需先執行 research/download_pe.py twse / tpex。

  python3 research/gvi_study.py
"""
import json
import os
import sys
from collections import defaultdict, deque

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import revenue_long as L  # noqa: E402
import revenue_study as R  # noqa: E402

HOLD = int(sys.argv[1]) if len(sys.argv) > 1 else L.HOLD  # 可指定持有天數，例如 60
COST = L.COST


def load_pe(ymd):
    out = {}
    for m in ("twse", "tpex"):
        p = os.path.join(L.CACHE_DIR, f"pe_{m}_{ymd}.json")
        if os.path.exists(p):
            out.update(json.load(open(p, encoding="utf-8")))
    return out or None


def rev_ok(x):
    f = x["f"]
    return bool(f and f["high12"] and f["yoy"] > 20 and x["above60"])


def by_gvi(xs, top):
    return sorted((x for x in xs if x["gvi"] is not None), key=lambda x: -x["gvi"])[:top]


def by_liq(xs, top):
    return sorted(xs, key=lambda x: -x["liq"])[:top]


VARIANTS = [
    ("GVI最高20", lambda xs: by_gvi(xs, 20)),
    ("GVI最高20+季線", lambda xs: by_gvi([x for x in xs if x["above60"]], 20)),
    ("GVI前10%取成交值20", lambda xs: by_liq([x for x in xs if x["gpct"] is not None and x["gpct"] >= 90], 20)),
    ("GVI最高20排除金融", lambda xs: by_gvi([x for x in xs if not x["code"].startswith("28")], 20)),
    ("營收動能(現行)", lambda xs: by_liq([x for x in xs if rev_ok(x)], 20)),
    ("營收動能+GVI前50%", lambda xs: by_liq([x for x in xs if rev_ok(x) and (x["gpct"] or 0) >= 50], 20)),
    ("營收動能+GVI前30%", lambda xs: by_liq([x for x in xs if rev_ok(x) and (x["gpct"] or 0) >= 70], 20)),
    ("營收動能取GVI最高10", lambda xs: by_gvi([x for x in xs if rev_ok(x)], 10)),
]


def main():
    days = L.trading_days()
    rev = R.load_revenue()
    window = deque(maxlen=60)
    open_pos, results, seen = [], defaultdict(list), set()
    counts = defaultdict(list)
    for t, ymd in enumerate(days):
        day = L.load_day(ymd)
        still = []
        for p in open_pos:
            x = day.get(p["code"])
            if x is None:
                p["left"] -= 1
            else:
                o, c, ch, _ = x
                if p["entry"] is None:
                    p["entry"] = o
                elif ch is not None and p["last"]:
                    gap = p["last"] - (c - ch)
                    if gap > p["last"] * 0.003:
                        p["cum"] += gap
                p["last"] = c
                p["value"] = c + p["cum"]
                p["left"] -= 1
            if p["left"] <= 0:
                if p["entry"]:
                    ret = (p["value"] / p["entry"] - 1) * 100
                    if -66 < ret < 160:
                        for g in p["groups"]:
                            results[(p["month"], g)].append(ret - COST)
            else:
                still.append(p)
        open_pos = still
        window.append(day)

        if int(ymd[6:]) >= 11 and ymd[:6] not in seen and len(window) == 60 and t + HOLD < len(days):
            seen.add(ymd[:6])
            pe = load_pe(ymd)
            if not pe:
                continue
            cands = []
            for code, (o, c, ch, v) in day.items():
                if len(code) != 4 or not code.isdigit() or code[0] == "0" or c < 10:
                    continue
                vols = [w[code][3] for w in list(window)[-20:] if code in w]
                closes = [w[code][1] for w in window if code in w]
                if len(vols) < 20 or sum(vols) / 20 < 500_000 or len(closes) < 60:
                    continue
                gvi = None
                per, pb = pe.get(code) or (None, None)
                if per and pb and per > 0 and pb > 0:
                    gvi = (1 / pb) * (1 + pb / per) ** 5
                cands.append({"code": code, "f": R.rev_features(rev, code, ymd), "gvi": gvi,
                              "liq": sum(w[code][1] * w[code][3] for w in list(window)[-20:]),
                              "above60": c > sum(closes) / 60})
            ranked = sorted((x for x in cands if x["gvi"] is not None), key=lambda x: x["gvi"])
            for i, x in enumerate(ranked):
                x["gpct"] = i / len(ranked) * 100
            groups = defaultdict(list)
            for x in cands:
                x.setdefault("gpct", None)
                groups[x["code"]].append("對照組")
            for name, fn in VARIANTS:
                sel = fn(cands)
                counts[name].append(len(sel))
                for x in sel:
                    groups[x["code"]].append(name)
            for code, gs in groups.items():
                open_pos.append({"code": code, "month": ymd[:6], "groups": gs, "entry": None,
                                 "cum": 0.0, "last": None, "value": None, "left": HOLD})

    months = sorted({m for m, _ in results})
    split = months[int(len(months) * 0.75)]
    names = ["對照組"] + [v[0] for v in VARIANTS]

    def avg(xs):
        return sum(xs) / len(xs) if xs else None

    monthly = {m: {g: avg(results.get((m, g), [])) for g in names} for m in months}
    print(f"{len(months)} 次選股（{months[0]} ～ {months[-1]}），驗證期從 {split} 起；月報酬已扣成本\n")
    by_year = defaultdict(list)
    for m in months:
        by_year[m[:4]].append(m)
    print("年度平均月報酬（括號：贏過對照組的月數）")
    print(f"{'年度':6s}" + "".join(f"{g:>16s}" for g in names))
    for y, ms in sorted(by_year.items()):
        line = f"{y:6s}"
        for g in names:
            vals = [monthly[m][g] for m in ms if monthly[m][g] is not None]
            if not vals:
                line += f"{'–':>16s}"
            elif g == "對照組":
                line += f"{avg(vals):+15.2f}%"
            else:
                beat = sum(monthly[m][g] > monthly[m]["對照組"] for m in ms if monthly[m][g] is not None)
                line += f"{avg(vals):+9.2f}%({beat:2d}/{len(vals):2d})"
        print(line)
    print()
    for g in names:
        for label, ms in (("訓練", [m for m in months if m < split]), ("驗證", [m for m in months if m >= split])):
            ms = [m for m in ms if monthly[m][g] is not None]
            if not ms:
                continue
            vals = [monthly[m][g] for m in ms]
            trades = [x for m in ms for x in results[(m, g)]]
            beat = sum(monthly[m][g] > monthly[m]["對照組"] for m in ms)
            print(f"{g:16s} {label} 月平均{avg(vals):+6.2f}% 贏對照組{beat:3d}/{len(ms):3d} "
                  f"月勝率{sum(v > 0 for v in vals) / len(vals) * 100:5.1f}% 個股勝率{sum(x > 0 for x in trades) / len(trades) * 100:5.1f}% "
                  f"最差月{min(vals):+6.1f}% 平均檔數{len(trades) / len(ms):4.1f}")


if __name__ == "__main__":
    main()
