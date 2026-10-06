#!/usr/bin/env python3
"""明日精選 3 檔：比較不同停損方式（11 年回測）。

選股與 research/top3.py 的 B 規則相同（營收動能＋今天突破 60 日新高、日均量 ≥ 1000 張、成交值前 3），
隔天開盤買進，最多持有 20 個交易日。出場方式：
  固定持有 20 天／停損 -8%、-10%、-15%（盤中跌破就出場，跳空跌破用開盤價）／收盤跌破 20 日均線出場
報酬含除權息還原、已扣成本 0.585%。

  python3 research/top3_stop.py
"""
import json
import os
import sys
from collections import defaultdict, deque

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import revenue_long as L  # noqa: E402
import revenue_study as R  # noqa: E402

COST = 0.585
HOLD = 20
EXITS = tuple(sys.argv[1].split(",")) if len(sys.argv) > 1 else ("固定持有20天", "停損-8%", "停損-10%", "停損-15%", "跌破月線出場")


def main():
    days = L.trading_days()
    n = len(days)
    split = int(n * 0.75)
    rev = R.load_revenue()
    window = deque(maxlen=61)
    positions = []
    res = defaultdict(list)  # (出場方式, 期間) -> [報酬]

    for t, ymd in enumerate(days):
        day = {}
        for m in ("twse", "tpex"):
            for r in json.load(open(os.path.join(L.CACHE_DIR, f"{m}_{ymd}.json"), encoding="utf-8")):
                if None not in (r["close"], r["open"], r["high"], r["low"], r["change"]):
                    day[r["code"]] = (r["open"], r["high"], r["low"], r["close"], r["change"], r["volume"])
        window.append(day)
        still = []
        for p in positions:
            x = day.get(p["code"])
            if x:
                o, h, l, c, ch, v = x
                if p["entry"] is None:
                    p["entry"], first = o, True
                else:
                    first = False
                    if p["last"] and p["last"] - (c - ch) > p["last"] * 0.003:
                        p["cum"] += p["last"] - (c - ch)
                p["last"] = c
                p["k"] += 1
                adj = p["cum"]
                closes = [w[p["code"]][3] for w in list(window)[-20:] if p["code"] in w]
                ma20 = sum(closes) / len(closes) if len(closes) == 20 else None
                ex = p["exit"]
                out = None
                if ex.startswith("停損"):
                    stop = p["entry"] * (1 - int(ex[3:].split("%")[0]) / 100)
                    if l + adj <= stop:
                        out = stop if first or o + adj > stop else o + adj
                    elif "停利" in ex:  # 例如「停損-10%停利+5%」：盤中漲到目標就賣
                        tp = p["entry"] * (1 + int(ex.split("停利+")[1].rstrip("%")) / 100)
                        if h + adj >= tp:
                            out = tp if first or o + adj < tp else o + adj
                elif ex == "跌破月線出場" and ma20 and c < ma20:
                    out = c + adj
                if out is None and p["k"] >= HOLD:
                    out = c + adj
                if out is not None:
                    ret = (out / p["entry"] - 1) * 100
                    if -70 < ret < 300:
                        res[(ex, p["t"] >= split)].append(ret - COST)
                    continue
            still.append(p)
        positions = still
        if len(window) < 61 or t + HOLD + 1 >= n:
            continue
        chosen = []
        for code, (o, h, l, c, ch, v) in day.items():
            if len(code) != 4 or not code.isdigit() or code[0] == "0" or c < 10:
                continue
            past = [w[code] for w in list(window)[:-1] if code in w]
            if len(past) < 59 or c <= max(x[1] for x in past[-60:]):
                continue
            last20 = past[-19:] + [day[code]]
            if sum(x[5] for x in last20) / 20 < 1_000_000:
                continue
            if c <= (sum(x[3] for x in past[-59:]) + c) / 60:
                continue
            f = R.rev_features(rev, code, ymd)
            if f and f["high12"] and f["yoy"] > 20:
                chosen.append((sum(x[3] * x[5] for x in last20), code))
        chosen.sort(reverse=True)
        for _, code in chosen[:3]:
            for ex in EXITS:
                positions.append({"code": code, "exit": ex, "t": t, "entry": None, "cum": 0.0, "last": None, "k": 0})

    def st(xs):
        xs = sorted(xs)
        big = sum(x <= -15 for x in xs) / len(xs) * 100
        return (f"n={len(xs):5d} 勝率{sum(x > 0 for x in xs) / len(xs) * 100:5.1f}% 平均{sum(xs) / len(xs):+6.2f}% "
                f"最差{xs[0]:+6.1f}% 最差5%平均{sum(xs[:len(xs) // 20]) / (len(xs) // 20):+6.1f}% 賠超過15%:{big:4.1f}%")

    print(f"行情 {days[60]} ～ {days[-1]}；訓練期到 {days[split - 1]}，驗證期從 {days[split]}；已扣成本\n")
    for ex in EXITS:
        print(ex)
        print(f"   訓練 {st(res[(ex, False)])}")
        print(f"   驗證 {st(res[(ex, True)])}")


if __name__ == "__main__":
    main()
