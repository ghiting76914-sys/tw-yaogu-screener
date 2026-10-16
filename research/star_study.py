#!/usr/bin/env python3
"""妖股雷達、起漲前夕：找「可以買」的 ⭐ 子集合（11 年，前 75% 訓練、後 25% 驗證）。

妖股雷達：重算每天的雷達分數（yaogu.analyze 的加分規則；股本用目前的資料，注意股不計），分數 ≥ 6、成交量 ≥ 500 張。
  出場方式比較：隔天開盤買／當天收盤買，持有 5 或 20 個交易日，停損 -10%。
起漲前夕：strategy.pre_passes，照網站的操作計畫（5 日內突破箱頂進場、停損、目標、最多 10 天）模擬。
候選條件：營收創 12 個月新高且年增 > 20%、大盤廣度、族群齊漲（同產業另有 ≥3 檔漲 5%）、起漲第 1 根、
  連續漲停天數、雷達分數、站上季線、收盤鎖漲停、突破 60 日高……
報酬含除權息、已扣成本 0.585%。

  python3 research/star_study.py
"""
import glob
import json
import os
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import revenue_study as R  # noqa: E402
import strategy  # noqa: E402
import study  # noqa: E402
from context_study import industries  # noqa: E402
from yaogu import CACHE_DIR  # noqa: E402

COST = strategy.ROUND_TRIP_COST


def stats(xs):
    if len(xs) < 40:
        return f"n={len(xs):5d} 樣本太少"
    a = np.array(xs)
    return f"n={len(a):5d} 平均{a.mean():+6.2f}% 中位{np.median(a):+6.2f}% 勝率{(a > 0).mean() * 100:5.1f}%"


def main():
    days, series, _ = study.load()
    ref_files = sorted(glob.glob(os.path.join(CACHE_DIR, "ref_*.json")))
    capital = json.load(open(ref_files[-1]))["capital"] if ref_files else {}
    ind = industries()
    rev = R.load_revenue()
    codes = [c for c in series if len(c) == 4 and c.isdigit() and c[0] != "0"]
    T, N = len(days), len(codes)
    split = int(T * 0.75)
    print(f"{days[0]}～{days[-1]}，{N} 檔；驗證期從 {days[split]}", flush=True)

    A = {k: np.full((T, N), np.nan) for k in ("o", "h", "l", "c", "ch", "v")}
    for j, code in enumerate(codes):
        for t, x in enumerate(series[code]):
            if x and None not in (x["open"], x["high"], x["low"], x["close"], x["change"]):
                A["o"][t, j], A["h"][t, j], A["l"][t, j] = x["open"], x["high"], x["low"]
                A["c"][t, j], A["ch"][t, j], A["v"][t, j] = x["close"], x["change"], x["volume"] or 0
    o, h, c, ch, v = A["o"], A["h"], A["c"], A["ch"], A["v"]
    ref = c - ch
    with np.errstate(all="ignore"):
        pct = np.where(ref > 0, ch / ref * 100, np.nan)

    def shift(x, k):
        out = np.full_like(x, np.nan)
        out[k:] = x[:-k]
        return out

    def roll_mean(x, w):
        cs = np.nancumsum(np.nan_to_num(x), axis=0)
        return (cs - np.vstack([np.zeros((w, x.shape[1])), cs[:-w]])) / w

    vol20p = shift(roll_mean(v, 20), 1)                # 前 20 天均量（不含今天）
    vol20 = roll_mean(v, 20)
    with np.errstate(all="ignore"):
        vr = v / vol20p
    hi60p = np.full_like(h, np.nan)
    for t in range(61, T):
        with np.errstate(all="ignore"):
            hi60p[t] = np.nanmax(h[t - 60:t], axis=0)
    c5 = shift(c, 5)
    with np.errstate(all="ignore"):
        r5 = (c / c5 - 1) * 100
    lim = np.nan_to_num(pct) >= 9.5
    streak = np.zeros((T, N))
    for t in range(T):
        streak[t] = np.where(lim[t], (streak[t - 1] if t else 0) + 1, 0)
    cap = np.array([capital.get(code) or np.nan for code in codes]) / 1e8
    shares = cap * 1e8 / 10
    turnover = v / shares * 100

    # 雷達分數（yaogu.analyze）
    sc = np.zeros((T, N))
    sc += np.where(cap < 10, 2, np.where(cap < 20, 1, 0))
    sc += np.where(vr >= 3, 2, np.where(vr >= 2, 1, 0))
    sc += np.where(streak >= 1, 2 + np.minimum(streak - 1, 3), np.where(np.nan_to_num(pct) >= 5, 1, 0))
    sc += np.where(r5 >= 20, 2, np.where(r5 >= 10, 1, 0))
    sc += np.where(c > hi60p, 2, 0)
    sc += np.where(turnover >= 10, 1, 0)
    radar = (sc >= 6) & (v >= 500_000) & ~np.isnan(c) & ~np.isnan(hi60p)
    radar[:61] = False
    radar[T - 22:] = False

    # 環境：大盤廣度、族群同漲
    ma20 = roll_mean(c, 20)
    liquid = vol20 >= 500_000
    with np.errstate(all="ignore"):
        breadth = np.nanmean(np.where(liquid, c > ma20, np.nan), axis=1)
    sec = np.array([ind.get(code, "") for code in codes])
    up5 = liquid & (np.nan_to_num(pct) >= 5)
    sec_ids = {s: i for i, s in enumerate(sorted(set(sec)))}
    sec_idx = np.array([sec_ids[s] for s in sec])
    heat = np.zeros((T, N))
    for t in range(T):
        cnt = np.bincount(sec_idx[up5[t]], minlength=len(sec_ids))
        heat[t] = cnt[sec_idx] - up5[t]
    heat[:, sec == ""] = 0
    ma60 = roll_mean(c, 60)

    def first_bar(t, j):
        base_c = c[t - 20:t, j]
        base_p = pct[t - 20:t, j]
        if np.isnan(base_c).any() or vol20p[t, j] < 500_000 or not vr[t, j] >= 2:
            return False
        return base_c.max() / base_c.min() <= 1.15 and not (base_p >= 5).any()

    def tags(t, j, extra=()):
        code = codes[t and j] if False else codes[j]
        f = R.rev_features(rev, code, days[t])
        g = ["全部"]
        g.append("營收創新高＋年增>20%" if f and f["high12"] and f["yoy"] > 20 else "營收沒有創新高")
        b = breadth[t]
        g.append("大盤廣度<30%" if b < 0.3 else "大盤廣度>70%" if b > 0.7 else "大盤廣度30~70%")
        g.append("族群齊漲（另有≥3檔漲5%）" if heat[t, j] >= 3 else "族群沒齊漲")
        g.append("站上季線" if c[t, j] > ma60[t, j] else "季線下")
        g += list(extra)
        return g

    # ---------------------------------------------------------------- 妖股雷達
    res = defaultdict(list)
    ts, js = np.nonzero(radar)
    print(f"妖股雷達訊號 {len(ts):,} 筆", flush=True)
    for t, j in zip(ts, js):
        s = series[codes[j]]
        ext = [f"分數{'≥9' if sc[t, j] >= 9 else '7~8' if sc[t, j] >= 7 else '6'}",
               "連續漲停0" if streak[t, j] == 0 else "漲停1根" if streak[t, j] == 1 else "連續漲停≥2",
               "起漲第1根" if first_bar(t, j) else "不是起漲第1根",
               "突破60日高" if c[t, j] > hi60p[t, j] else "沒突破60日高",
               "5日漲≥20%" if r5[t, j] >= 20 else "5日漲<20%"]
        g = tags(t, j, ext)
        per = t >= split
        for name, kw in (("隔天開盤買 持有5天", dict(hold=5)), ("隔天開盤買 持有20天", dict(hold=20)),
                         ("收盤買 持有20天", dict(hold=20, at_close=True))):
            r = strategy.hold_return_stop(s, t, T, kw["hold"], 0.10, at_close=kw.get("at_close", False))
            if r is None or not r[1]:
                continue
            x = r[0] - COST
            if not -60 < x < 300:
                continue
            for k in g:
                res[(name, k, per)].append(x)
    order = ["全部", "營收創新高＋年增>20%", "營收沒有創新高", "大盤廣度<30%", "大盤廣度30~70%", "大盤廣度>70%",
             "族群齊漲（另有≥3檔漲5%）", "族群沒齊漲", "站上季線", "季線下", "分數6", "分數7~8", "分數≥9",
             "連續漲停0", "漲停1根", "連續漲停≥2", "起漲第1根", "不是起漲第1根", "突破60日高", "沒突破60日高",
             "5日漲≥20%", "5日漲<20%"]
    for name in ("隔天開盤買 持有5天", "隔天開盤買 持有20天", "收盤買 持有20天"):
        print(f"\n===== 妖股雷達：{name}，停損 -10% =====")
        for k in order:
            print(f"{k:22s}\t訓練 {stats(res[(name, k, False)])}\t驗證 {stats(res[(name, k, True)])}")

    # 雷達：組合條件
    print("\n===== 妖股雷達：組合條件（隔天開盤買 持有20天）=====")
    combos = defaultdict(list)
    for t, j in zip(ts, js):
        s = series[codes[j]]
        r = strategy.hold_return_stop(s, t, T, 20, 0.10)
        if r is None or not r[1]:
            continue
        x = r[0] - COST
        if not -60 < x < 300:
            continue
        f = R.rev_features(rev, codes[j], days[t])
        revok = bool(f and f["high12"] and f["yoy"] > 20)
        above = c[t, j] > ma60[t, j]
        brk = c[t, j] > hi60p[t, j]
        per = t >= split
        for nm, ok in (("營收＋站上季線", revok and above), ("營收＋突破60日高", revok and brk),
                       ("營收＋站上季線＋突破60日高", revok and above and brk),
                       ("營收＋站上季線＋廣度≥30%", revok and above and breadth[t] >= 0.3),
                       ("營收＋突破60日高＋均量≥1000張", revok and brk and vol20[t, j] >= 1_000_000),
                       ("營收＋突破＋沒連續漲停", revok and brk and streak[t, j] <= 1)):
            if ok:
                combos[(nm, per)].append(x)
    for nm in ("營收＋站上季線", "營收＋突破60日高", "營收＋站上季線＋突破60日高", "營收＋站上季線＋廣度≥30%",
               "營收＋突破60日高＋均量≥1000張", "營收＋突破＋沒連續漲停"):
        print(f"{nm:28s}\t訓練 {stats(combos[(nm, False)])}\t驗證 {stats(combos[(nm, True)])}")

    # ---------------------------------------------------------------- 起漲前夕
    print("\n===== 起漲前夕（網站操作計畫：5 日內突破箱頂進場）=====", flush=True)
    pres = defaultdict(list)
    last_t = T - strategy.PRE["trigger_days"] - strategy.PRE["max_hold"] - 1
    for t in range(61, last_t + 1):
        for j, code in enumerate(codes):
            if not v[t, j] >= 500_000:
                continue
            s = series[code]
            f = strategy.pre_features(s, t)
            if not f or not strategy.pre_passes(f):
                continue
            r = strategy.pre_simulate(s, t, f, T)
            if r is None:
                continue
            st = strategy.pre_strength(f)
            ext = ["強度≥70" if st >= 70 else "強度50~69" if st >= 50 else "強度<50",
                   "量縮≤0.7" if f["dry"] <= 0.7 else "量縮0.7~1",
                   "均量≥1000張" if f["v20"] >= 1_000_000 else "均量<1000張",
                   "股本<10億" if cap[j] < 10 else "股本≥10億"]
            for k in tags(t, j, ext):
                pres[(k, t >= split)].append(r)
    for k in ["全部", "營收創新高＋年增>20%", "營收沒有創新高", "大盤廣度<30%", "大盤廣度30~70%", "大盤廣度>70%",
              "族群齊漲（另有≥3檔漲5%）", "族群沒齊漲", "站上季線", "季線下", "強度≥70", "強度50~69", "強度<50",
              "量縮≤0.7", "量縮0.7~1", "均量≥1000張", "均量<1000張", "股本<10億", "股本≥10億"]:
        print(f"{k:22s}\t訓練 {stats(pres[(k, False)])}\t驗證 {stats(pres[(k, True)])}")


if __name__ == "__main__":
    main()
