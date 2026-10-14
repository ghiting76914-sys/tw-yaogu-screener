#!/usr/bin/env python3
"""族群強弱與大盤環境，對兩個實際使用中的策略有沒有幫助（11 年）。

環境條件（第 t 天收盤後就知道）：
  大盤廣度：流動股（20 日均量 ≥ 500 張）中站上月線的比例  <30%／30~70%／>70%
  族群強弱：同產業（證交所／櫃買產業別，現行分類）流動股近 5 日平均漲幅的排名  前 20%／中間／後 20%
  族群同漲：同產業今天另有幾檔流動股漲 ≥ 5%  0 檔／1~2 檔／≥3 檔
策略：
  隔日沖：盤中漲到 +7%（開盤未到）、接近 60 日高、量 ≥ 1000 張，以 +7% 買、隔天開盤賣
  精選類：營收創 12 個月新高、年增 > 20%、站上季線、收盤突破 60 日高、均量 ≥ 1000 張（全部候選，不只前 3），
          收盤價買、停損 -10%、持有 20 天
報酬含除權息、已扣成本 0.585%。產業分類存在 data/cache/industry.json。

  python3 research/context_study.py
"""
import json
import os
import sys
from collections import defaultdict, deque

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import revenue_long as L  # noqa: E402
import revenue_study as R  # noqa: E402
from yaogu import fetch_json  # noqa: E402

COST = 0.585
IND_FILE = os.path.join(L.CACHE_DIR, "industry.json")


def industries():
    if os.path.exists(IND_FILE):
        return json.load(open(IND_FILE, encoding="utf-8"))
    out = {r["公司代號"].strip(): r["產業別"].strip() for r in fetch_json("https://openapi.twse.com.tw/v1/opendata/t187ap03_L")}
    out.update({r["SecuritiesCompanyCode"].strip(): r["SecuritiesIndustryCode"].strip()
                for r in fetch_json("https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O")})
    json.dump(out, open(IND_FILE, "w", encoding="utf-8"))
    return out


def main():
    ind = industries()
    days = L.trading_days()
    n = len(days)
    split = int(n * 0.75)
    rev = R.load_revenue()
    idx, hist = {}, defaultdict(lambda: deque(maxlen=61))   # (還原收盤, 還原最高, 量)
    pend_on, hold_top = [], []
    res = defaultdict(list)
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
        # 隔日沖：昨天 +7% 買 → 今天開盤賣
        for code, entry, t0, gs in pend_on:
            x = day.get(code)
            if x and code in prev_idx:
                rr = (prev_idx[code] * x[0] / (x[3] - x[4]) / entry - 1) * 100 - COST
                if -30 < rr < 30:
                    for g in gs:
                        res[("隔日沖", g, t0 >= split)].append(rr)
        pend_on = []
        # 精選類：持有 20 天、停損 -10%
        still = []
        for p in hold_top:
            x = day.get(p["code"])
            if not x or p["code"] not in prev_idx:
                still.append(p)
                continue
            o, h, l, c, ch, v = x
            k = idx[p["code"]] / c                      # 還原比例
            p["k"] += 1
            stop = p["entry"] * 0.9
            out = None
            if l * k <= stop:
                out = min(o * k, stop)
            elif p["k"] >= 20:
                out = idx[p["code"]]
            if out is None:
                still.append(p)
                continue
            rr = (out / p["entry"] - 1) * 100 - COST
            if -40 < rr < 200:
                for g in p["gs"]:
                    res[("精選類", g, p["t0"] >= split)].append(rr)
        hold_top = still
        if t < 61 or t + 22 >= n:
            continue

        liquid = {}
        above = 0
        for code, hs in hist.items():
            if code in day and len(hs) >= 61 and len(code) == 4 and code[0] != "0":
                vol20 = sum(x[2] for x in list(hs)[-20:]) / 20
                if vol20 >= 500_000:
                    liquid[code] = hs
                    above += hs[-1][0] > sum(x[0] for x in list(hs)[-20:]) / 20
        if not liquid:
            continue
        breadth = above / len(liquid)
        bname = "大盤廣度<30%" if breadth < 0.3 else "大盤廣度>70%" if breadth > 0.7 else "大盤廣度30~70%"
        sec5, sec_up = defaultdict(list), defaultdict(int)
        for code, hs in liquid.items():
            s = ind.get(code)
            if not s:
                continue
            sec5[s].append(hs[-1][0] / hs[-6][0] - 1)
            o, h, l, c, ch, v = day[code]
            if ch / (c - ch) >= 0.05:
                sec_up[s] += 1
        ranked = sorted((sum(xs) / len(xs), s) for s, xs in sec5.items() if len(xs) >= 3)
        pct_rank = {s: i / max(1, len(ranked) - 1) for i, (_, s) in enumerate(ranked)}

        def ctx(code, self_up):
            s = ind.get(code)
            pr = pct_rank.get(s)
            sname = "族群強度：無資料" if pr is None else "族群近5日：前20%" if pr >= 0.8 else "族群近5日：後20%" if pr <= 0.2 else "族群近5日：中間"
            others = sec_up.get(s, 0) - (1 if self_up else 0)
            uname = "同族群另有 ≥3 檔漲 5%" if others >= 3 else "同族群另有 1~2 檔漲 5%" if others >= 1 else "同族群沒有其他大漲"
            return ["全部", bname, sname, uname]

        for code, hs in liquid.items():
            o, h, l, c, ch, v = day[code]
            if c < 10:
                continue
            prev = c - ch
            hi60 = max(x[1] for x in list(hs)[:-1][-60:])
            vol20 = sum(x[2] for x in list(hs)[-20:]) / 20
            if v >= 1_000_000 and h / prev >= 1.07 and o / prev < 1.07 and idx[code] * h / c >= hi60 * 0.98:
                gs = ctx(code, ch / prev >= 0.05)
                # ⭐ 推薦（strategy.overnight_picks）：起漲第 1 根 2 分、量比 < 2 一分、突破 60 日高一分，≥ 2 分
                past = list(hs)[:-1]
                vr = v / (sum(x[2] for x in past[-20:]) / 20 or 1)
                closes20 = [x[0] for x in past[-20:]]
                quiet = max(closes20) / min(closes20) <= 1.15
                first = quiet and vr >= 2
                star = 2 * first + (vr < 2) + (idx[code] * h / c > hi60) >= 2
                heat = gs[3]
                gs.append(("⭐推薦" if star else "非推薦") + "＋" + ("族群齊漲" if heat.startswith("同族群另有 ≥3") else "族群沒齊漲"))
                pend_on.append((code, idx[code] * prev * 1.07 / c, t, gs))
            if vol20 >= 1_000_000 and idx[code] > hi60 and idx[code] > sum(x[0] for x in list(hs)[-60:]) / 60:
                f = R.rev_features(rev, code, ymd)
                if f and f["high12"] and f["yoy"] > 20:
                    hold_top.append({"code": code, "entry": idx[code], "k": 0, "t0": t, "gs": ctx(code, ch / prev >= 0.05)})

    def st(xs):
        if len(xs) < 50:
            return f"n={len(xs):5d} 樣本太少"
        return f"n={len(xs):5d} 平均{sum(xs) / len(xs):+6.2f}% 勝率{sum(x > 0 for x in xs) / len(xs) * 100:5.1f}%"

    print(f"{days[61]}～{days[-1]}；驗證期從 {days[split]}；已扣成本\n")
    order = ["全部", "大盤廣度<30%", "大盤廣度30~70%", "大盤廣度>70%", "族群近5日：前20%", "族群近5日：中間", "族群近5日：後20%",
             "族群強度：無資料", "同族群另有 ≥3 檔漲 5%", "同族群另有 1~2 檔漲 5%", "同族群沒有其他大漲",
             "⭐推薦＋族群齊漲", "⭐推薦＋族群沒齊漲", "非推薦＋族群齊漲", "非推薦＋族群沒齊漲"]
    for strat in ("隔日沖", "精選類"):
        print(f"===== {strat} =====")
        for g in order:
            print(f"{g:20s}\t訓練 {st(res[(strat, g, False)])}\t驗證 {st(res[(strat, g, True)])}")
        print()


if __name__ == "__main__":
    main()
