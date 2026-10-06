#!/usr/bin/env python3
"""融資融券研究（2 年）：
  1. 朋友的「嘎空」策略加上券資比條件（≥ 5%、≥ 20%），搭配他的出場規則
  2. 融資融券條件能否改善「明日精選 3 檔」（持有 20 天、停損 -10%）與「隔日沖」（+7% 進場、隔天開盤賣）

券資比 = 融券餘額 ÷ 融資餘額；融資使用率 = 融資餘額 ÷ 融資限額；融資 5 日增減 = 今天 ÷ 5 天前 - 1。
訊號日的融資融券為當天收盤後公布的資料（盤後才知道），隔日沖在盤中無法使用當天的融資資料，
所以隔日沖改用「前一天」的融資融券。報酬已扣成本、含除權息。

  python3 research/margin_study.py
"""
import glob
import json
import os
import sys
from collections import defaultdict, deque

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import friend  # noqa: E402
import revenue_long as L  # noqa: E402
import revenue_study as R  # noqa: E402

COST = 0.585


def load_margin(ymd):
    out = {}
    for m in ("twse", "tpex"):
        p = os.path.join(L.CACHE_DIR, f"margin_{m}_{ymd}.json")
        if os.path.exists(p):
            out.update(json.load(open(p)))
    return out or None


def main():
    mdays = {os.path.basename(f)[12:20] for f in glob.glob(os.path.join(L.CACHE_DIR, "margin_twse_*.json"))}
    days = [d for d in L.trading_days() if d >= min(mdays)]
    # 往前多讀 70 天，讓均線、60 日高點等指標有資料
    all_days = L.trading_days()
    start = max(0, all_days.index(days[0]) - 70)
    days = all_days[start:]
    n = len(days)
    first_sig = 70
    split = first_sig + int((n - first_sig) * 0.75)
    rev = R.load_revenue()
    window = deque(maxlen=61)
    margins = deque(maxlen=6)
    kd, khist = {}, defaultdict(lambda: deque(maxlen=10))
    positions, res = [], defaultdict(list)

    for t, ymd in enumerate(days):
        day = {}
        for m in ("twse", "tpex"):
            for r in json.load(open(os.path.join(L.CACHE_DIR, f"{m}_{ymd}.json"), encoding="utf-8")):
                if None not in (r["close"], r["open"], r["high"], r["low"], r["change"]):
                    day[r["code"]] = (r["open"], r["high"], r["low"], r["close"], r["change"], r["volume"], r.get("amount") or 0)
        window.append(day)
        mg_prev = margins[-1] if margins else None
        margins.append(load_margin(ymd))
        mg = margins[-1]
        for code, x in day.items():  # KD
            hs = [w[code][1] for w in list(window)[-9:] if code in w]
            ls = [w[code][2] for w in list(window)[-9:] if code in w]
            if len(hs) < 9:
                continue
            hi, lo = max(hs), min(ls)
            rsv = 50 if hi == lo else (x[3] - lo) / (hi - lo) * 100
            k0, d0 = kd.get(code, (50, 50))
            k = k0 * 2 / 3 + rsv / 3
            kd[code] = (k, d0 * 2 / 3 + k / 3)
            khist[code].append(k)
        # 部位結算
        still = []
        for p in positions:
            x = day.get(p["code"])
            if not x:
                still.append(p)
                continue
            o, h, l, c, ch, v, a = x
            first = p["entry"] is None
            if first:
                p["entry"] = o
            elif p["last"] and p["last"] - (c - ch) > p["last"] * 0.003:
                p["cum"] += p["last"] - (c - ch)
            p["last"] = c
            p["k"] += 1
            adj, ret, out = p["cum"], None, None
            if p["kind"] == "overnight":
                out = o + adj  # 隔天開盤賣
            elif p["kind"] == "top3":
                stop = p["entry"] * 0.9
                if l + adj <= stop:
                    out = stop if first or o + adj > stop else o + adj
                elif p["k"] >= 20:
                    out = c + adj
            else:  # 朋友的出場
                r_now = ((c + adj) / p["entry"] - 1) * 100
                if friend.friend_exit(p["k"], r_now, kd.get(p["code"], (None,))[0]):
                    out = c + adj
            if out is None:
                still.append(p)
                continue
            ret = (out / p["entry"] - 1) * 100
            if -70 < ret < 300:
                for g in p["groups"]:
                    res[(g, p["t"] >= split)].append(ret - COST)
        positions = still
        if t < first_sig or t + 25 >= n or not mg:
            continue

        top3c = []
        for code, (o, h, l, c, ch, v, a) in day.items():
            if len(code) != 4 or not code.isdigit() or code[0] == "0" or c < 10:
                continue
            past = [w[code] for w in list(window)[:-1] if code in w]
            if len(past) < 59:
                continue
            m = mg.get(code)
            m5 = margins[0].get(code) if margins[0] else None
            ratio = m[1] / m[0] * 100 if m and m[0] else None          # 券資比
            usage = m[0] / m[2] * 100 if m and m[2] else None          # 融資使用率
            chg5 = (m[0] / m5[0] - 1) * 100 if m and m5 and m5[0] else None  # 融資 5 日增減
            last20 = past[-19:] + [day[code]]
            v20 = sum(x[5] for x in last20)
            vwap20 = sum(x[6] for x in last20) / v20 if v20 else None
            # ---- 1. 朋友：嘎空（加上券資比）
            k = kd.get(code, (None,))[0]
            last10 = past[-9:] + [day[code]]
            rng = max(x[1] for x in last10) / min(x[2] for x in last10) - 1
            if (a >= 6e7 and rng >= 0.20 and k is not None and k < 50 and len(khist[code]) == 10
                    and max(khist[code]) > 80 and vwap20 and c > vwap20):
                gs = ["嘎空：不看券資比"]
                if ratio is not None and ratio >= 5:
                    gs.append("嘎空：券資比 ≥ 5%（朋友的版本）")
                if ratio is not None and ratio >= 20:
                    gs.append("嘎空：券資比 ≥ 20%")
                positions.append({"code": code, "kind": "friend", "groups": gs, "t": t,
                                  "entry": c, "cum": 0.0, "last": c, "k": 0})
            # ---- 2. 隔日沖（+7% 進場，開盤前尚未到 +7%，接近 60 日高）；融資用前一天的
            prev = c - ch
            if prev > 0 and v >= 1_000_000 and (h / prev - 1) * 100 >= 7 and (o / prev - 1) * 100 < 7 \
                    and h >= max(x[1] for x in past[-60:]) * 0.98:
                mp = mg_prev.get(code) if mg_prev else None
                pr = mp[1] / mp[0] * 100 if mp and mp[0] else None
                pu = mp[0] / mp[2] * 100 if mp and mp[2] else None
                gs = ["隔日沖：全部"]
                if pr is not None:
                    gs.append("隔日沖：前一天券資比 ≥ 10%" if pr >= 10 else "隔日沖：前一天券資比 < 10%")
                if pu is not None:
                    gs.append("隔日沖：前一天融資使用率 ≥ 10%" if pu >= 10 else "隔日沖：前一天融資使用率 < 10%")
                entry = prev * 1.07
                positions.append({"code": code, "kind": "overnight", "groups": gs, "t": t,
                                  "entry": entry, "cum": 0.0, "last": c, "k": 0})
            # ---- 3. 明日精選候選
            if v20 / 20 >= 1_000_000 and c > (sum(x[3] for x in past[-59:]) + c) / 60 and c > max(x[1] for x in past[-60:]):
                f = R.rev_features(rev, code, ymd)
                if f and f["high12"] and f["yoy"] > 20:
                    top3c.append({"code": code, "value": sum(x[3] * x[5] for x in last20),
                                  "ratio": ratio, "usage": usage, "chg5": chg5})
        top3c.sort(key=lambda x: -x["value"])
        variants = {
            "精選3檔：原本": top3c,
            "精選3檔：排除融資5日增加>10%": [x for x in top3c if x["chg5"] is None or x["chg5"] <= 10],
            "精選3檔：排除融資使用率>20%": [x for x in top3c if x["usage"] is None or x["usage"] <= 20],
            "精選3檔：只選券資比≥5%": [x for x in top3c if x["ratio"] is not None and x["ratio"] >= 5],
        }
        for name, lst in variants.items():
            for x in lst[:3]:
                positions.append({"code": x["code"], "kind": "top3", "groups": [name], "t": t,
                                  "entry": None, "cum": 0.0, "last": None, "k": 0})

    def st(xs):
        if len(xs) < 20:
            return f"n={len(xs):4d} 樣本太少"
        return f"n={len(xs):4d} 勝率{sum(x > 0 for x in xs) / len(xs) * 100:5.1f}% 平均{sum(xs) / len(xs):+6.2f}%"

    print(f"融資融券 {days[first_sig]} ～ {days[-1]}；訓練期到 {days[split - 1]}，驗證期從 {days[split]}；已扣成本\n")
    names = sorted({g for g, _ in res}, key=lambda g: (g.split("：")[0], g))
    for g in names:
        print(f"{g:30s}\t訓練 {st(res[(g, False)])}\t驗證 {st(res[(g, True)])}")


if __name__ == "__main__":
    main()
