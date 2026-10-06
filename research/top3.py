#!/usr/bin/env python3
"""每日精選 3 檔研究：從營收動能股（營收創 12 個月新高、年增 > 20%、站上季線）中，
每天再用技術面條件挑 3 檔，隔天開盤買進、持有 H 天收盤賣出。

候選：一般股、股價 ≥ 10、20 日均量 ≥ 1000 張、收盤站上 60 日均線、營收動能條件成立。
報酬含除權息還原、已扣成本 0.585%。逐日讀取行情、只保留最近 61 天。

  python3 research/top3.py
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
HOLDS = (5, 10, 20)
TOP = 3


def main():
    days = L.trading_days()
    n = len(days)
    split = int(n * 0.75)
    rev = R.load_revenue()
    window = deque(maxlen=61)
    rsi_state = {}   # 代號 -> (平均漲, 平均跌)，RSI(2) 用
    positions = []   # 持有中的部位
    res = defaultdict(list)          # (規則, H, 期間) -> [報酬]
    daily = defaultdict(list)        # (規則, H) -> [(t, 組合平均, 對照平均)]
    base_today = {}                  # (t, H) -> [對照組報酬]

    for t, ymd in enumerate(days):
        day = {}
        for m in ("twse", "tpex"):
            for r in json.load(open(os.path.join(L.CACHE_DIR, f"{m}_{ymd}.json"), encoding="utf-8")):
                if None not in (r["close"], r["open"], r["high"], r["low"], r["change"]):
                    day[r["code"]] = (r["open"], r["high"], r["low"], r["close"], r["change"], r["volume"])
        # 更新 RSI(2)
        for code, (o, h, l, c, ch, v) in day.items():
            g, ls = max(ch, 0), max(-ch, 0)
            up, dn = rsi_state.get(code, (g, ls))
            rsi_state[code] = ((up + g) / 2, (dn + ls) / 2)
        # 更新部位
        still = []
        for p in positions:
            x = day.get(p["code"])
            if x:
                o, h, l, c, ch, v = x
                if p["entry"] is None:
                    p["entry"] = o
                elif p["last"]:
                    gap = p["last"] - (c - ch)
                    if gap > p["last"] * 0.003:
                        p["cum"] += gap
                p["last"] = c
                p["value"] = c + p["cum"]
            p["left"] -= 1
            if p["left"] > 0:
                still.append(p)
                continue
            if p["entry"]:
                ret = (p["value"] / p["entry"] - 1) * 100
                if -70 < ret < 200:
                    ret -= COST
                    if p["rule"] == "對照組":
                        base_today.setdefault((p["t"], p["H"]), []).append(ret)
                    else:
                        res[(p["rule"], p["H"], p["t"] >= split)].append(ret)
                        daily[(p["rule"], p["H"])].append((p["t"], ret))
        positions = still
        window.append(day)
        if len(window) < 61 or t + max(HOLDS) >= n:
            continue

        cands = []
        for code, (o, h, l, c, ch, v) in day.items():
            if len(code) != 4 or not code.isdigit() or code[0] == "0" or c < 10:
                continue
            past = [w[code] for w in list(window)[:-1] if code in w]
            if len(past) < 59:
                continue
            last20 = past[-19:] + [day[code]]
            if sum(x[5] for x in last20) / 20 < 1_000_000:
                continue
            closes = [x[3] for x in past[-59:]] + [c]
            if c <= sum(closes) / 60:
                continue
            base = {"code": code, "value": sum(x[3] * x[5] for x in last20)}
            if t % 3 == 0:
                for H in HOLDS:
                    positions.append({"code": code, "rule": "對照組", "H": H, "t": t, "entry": None,
                                      "cum": 0.0, "last": None, "value": None, "left": H})
            f = R.rev_features(rev, code, ymd)
            if not (f and f["high12"] and f["yoy"] > 20):
                continue
            prev = c - ch
            pct = ch / prev * 100 if prev > 0 else 0
            vol20 = sum(x[5] for x in past[-20:]) / 20
            up, dn = rsi_state[code]
            base.update({
                "breakout": c > max(x[1] for x in past[-60:]),
                "strong": pct >= 4 and (h == l or (c - l) / (h - l) >= 0.9),
                "vr": v / vol20 if vol20 else 0,
                "rsi2": 100.0 if dn == 0 else 100 - 100 / (1 + up / dn),
                "pct": pct,
            })
            cands.append(base)

        rules = {
            "A 營收動能、成交值前3": sorted(cands, key=lambda x: -x["value"]),
            "B 營收動能＋今天突破60日高": sorted([x for x in cands if x["breakout"]], key=lambda x: -x["value"]),
            "C 營收動能＋今天強勢收高": sorted([x for x in cands if x["strong"]], key=lambda x: -x["vr"]),
            "D 營收動能＋短線拉回 RSI2<10": sorted([x for x in cands if x["rsi2"] < 10], key=lambda x: -x["value"]),
        }
        for name, lst in rules.items():
            for x in lst[:TOP]:
                for H in HOLDS:
                    positions.append({"code": x["code"], "rule": name, "H": H, "t": t, "entry": None,
                                      "cum": 0.0, "last": None, "value": None, "left": H})

    def st(xs):
        if len(xs) < 30:
            return f"n={len(xs):5d} 樣本太少"
        return f"n={len(xs):5d} 勝率{sum(x > 0 for x in xs) / len(xs) * 100:5.1f}% 平均{sum(xs) / len(xs):+.2f}%"

    print(f"行情 {days[60]} ～ {days[-1]}；訓練期到 {days[split - 1]}，驗證期從 {days[split]}；已扣成本\n")
    for H in HOLDS:
        print(f"===== 持有 {H} 天 =====")
        b_tr = [x for (t, h), v in base_today.items() if h == H and t < split for x in v]
        b_te = [x for (t, h), v in base_today.items() if h == H and t >= split for x in v]
        print(f"{'對照：站上季線、流動性足夠的股票':26s}\t訓練 {st(b_tr)}\t驗證 {st(b_te)}")
        for name in ("A 營收動能、成交值前3", "B 營收動能＋今天突破60日高", "C 營收動能＋今天強勢收高", "D 營收動能＋短線拉回 RSI2<10"):
            print(f"{name:26s}\t訓練 {st(res[(name, H, False)])}\t驗證 {st(res[(name, H, True)])}")
            # 逐年：精選 3 檔的平均 vs 同年對照組
            by_year = defaultdict(lambda: [[], []])
            for t, r in daily[(name, H)]:
                by_year[days[t][:4]][0].append(r)
            for (t, h), v in base_today.items():
                if h == H:
                    by_year[days[t][:4]][1].extend(v)
            wins = sum(1 for y, (a, b) in by_year.items() if a and b and sum(a) / len(a) > sum(b) / len(b))
            print(f"{'':26s}\t逐年贏過對照組：{wins}/{sum(1 for a, b in by_year.values() if a and b)} 年")
        print()


if __name__ == "__main__":
    main()
