#!/usr/bin/env python3
"""隔日沖「開盤前觸價單」研究（11 年）：開盤前用前一天的資料列出候選股與觸發價（昨收 × 1.07），
盤中漲到觸發價就自動買進（開盤就高於觸發價則以開盤價成交），隔天開盤賣。

開盤前就知道的條件：一般股、股價 ≥ 10、20 日均量 ≥ 1000 張、觸發價 ≥ 60 日最高價 × 0.98（漲到 +7% 就接近或突破 60 日高）。
候選依 20 日均成交值排序，取前 N 檔；比較 N = 10、20、30、50、全部，以及「開盤就跳空到 +7% 以上也買」與否。
報酬已扣成本 0.585%。

  python3 research/overnight_premarket.py
"""
import json
import os
import sys
from collections import defaultdict, deque

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import revenue_long as L  # noqa: E402

COST = 0.585
TOPS = (10, 20, 30, 50, None)


def main():
    days = L.trading_days()
    n = len(days)
    split = int(n * 0.75)
    window = deque(maxlen=61)
    cands = []          # 今天開盤前的候選 [(code, trigger, rank)]
    pending = []        # 今天買進、等明天開盤賣 [(code, entry, t, groups)]
    res = defaultdict(list)
    per_day = defaultdict(list)
    for t, ymd in enumerate(days):
        day = {}
        for m in ("twse", "tpex"):
            for r in json.load(open(os.path.join(L.CACHE_DIR, f"{m}_{ymd}.json"), encoding="utf-8")):
                if None not in (r["close"], r["open"], r["high"], r["low"], r["change"]):
                    day[r["code"]] = (r["open"], r["high"], r["low"], r["close"], r["change"], r["volume"])
        # 昨天買進的，今天開盤賣
        for code, entry, t0, groups in pending:
            x = day.get(code)
            if not x or (x[3] - x[4]) / entry < 0.85:
                continue
            ret = (x[0] / entry - 1) * 100 - COST
            for g in groups:
                res[(g, t0 >= split)].append(ret)
        pending = []
        # 今天盤中：候選觸價成交
        counts = defaultdict(int)
        for code, trig, rank in cands:
            x = day.get(code)
            if not x:
                continue
            o, h, l, c, ch, v = x
            if h < trig:
                continue
            gap = o >= trig
            groups = []
            for top in TOPS:
                if top is None or rank < top:
                    name = f"前{top}檔" if top else "全部候選"
                    groups.append(f"{name}｜開盤已過觸發價也買")
                    if not gap:
                        groups.append(f"{name}｜只買盤中才漲到的")
                        counts[name] += 1
            pending.append((code, o if gap else trig, t, groups))
        for name, k in counts.items():
            per_day[name].append(k)
        window.append(day)
        # 收盤後：列出明天的候選
        cands = []
        if len(window) < 61:
            continue
        rows = []
        for code, (o, h, l, c, ch, v) in day.items():
            if len(code) != 4 or not code.isdigit() or code[0] == "0" or c < 10:
                continue
            hist = [w[code] for w in window if code in w]
            if len(hist) < 60:
                continue
            vol20 = sum(x[5] for x in hist[-20:]) / 20
            if vol20 < 1_000_000:
                continue
            trig = c * 1.07
            if trig < max(x[1] for x in hist[-60:]) * 0.98:
                continue
            rows.append((sum(x[3] * x[5] for x in hist[-20:]), code, trig))
        rows.sort(reverse=True)
        cands = [(code, trig, i) for i, (_, code, trig) in enumerate(rows)]

    def st(xs):
        if not xs:
            return "–"
        return f"n={len(xs):5d} 平均{sum(xs) / len(xs):+6.2f}% 勝率{sum(x > 0 for x in xs) / len(xs) * 100:5.1f}%"

    print(f"{days[60]} ～ {days[-1]}；訓練期到 {days[split - 1]}，驗證期從 {days[split]}；已扣成本\n")
    for top in TOPS:
        name = f"前{top}檔" if top else "全部候選"
        for kind in ("只買盤中才漲到的", "開盤已過觸發價也買"):
            g = f"{name}｜{kind}"
            print(f"{g:24s}\t訓練 {st(res[(g, False)])}\t驗證 {st(res[(g, True)])}")
        k = per_day[name]
        print(f"   平均每天觸發 {sum(k) / len(days):.1f} 檔（盤中才漲到的）")


if __name__ == "__main__":
    main()
