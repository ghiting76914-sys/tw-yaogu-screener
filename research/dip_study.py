#!/usr/bin/env python3
"""抄底研究（11 年，前 75% 訓練、後 25% 驗證）：哪些「跌深」的股票之後反彈有優勢？

候選條件（第 t 天收盤後判斷，第 t+1 天開盤買進）：
  A 多頭急跌：站上季線、季線上揚，近 5 日跌 ≥ 10%
  B 多頭超賣：站上季線、季線上揚，RSI(2) < 5
  C 好公司回檔：營收創 12 個月新高且年增 > 20%，近 5 日跌 ≥ 8%，仍在季線之上
  D 好公司跌回季線：營收同上，收盤在季線 ±3% 內、20 日內曾創 60 日新高（強勢股第一次回測季線）
  E 跌深：距 60 日高點跌 ≥ 30%
  F 跌深＋長下影：E 且當天下影線 ≥ 實體以下 4%、收紅、量 ≥ 20 日均量 1.5 倍
  G 連跌 5 天以上、累計跌 ≥ 12%
另依大盤廣度（流動股站上月線比例）分組。範圍：20 日均量 ≥ 1000 張、股價 ≥ 10 的一般股。
出場：持有 5／10／20 個交易日收盤賣，停損 -10%（盤中跌破、跳空用開盤價）；報酬含除權息、已扣成本 0.585%。
對照：同期間所有範圍內股票（每 5 天抽樣）同樣進出場。

  python3 research/dip_study.py
"""
import os
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import revenue_study as R  # noqa: E402
import strategy  # noqa: E402
import study  # noqa: E402

COST = strategy.ROUND_TRIP_COST


def stats(xs):
    if len(xs) < 40:
        return f"n={len(xs):5d} 樣本太少"
    a = np.array(xs)
    return (f"n={len(a):5d} 平均{a.mean():+6.2f}% 中位{np.median(a):+6.2f}% 勝率{(a > 0).mean() * 100:5.1f}%"
            f" 最差10%{np.percentile(a, 10):+6.1f}%")


def main():
    days, series, _ = study.load()
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
    o, h, l, c, ch, v = (A[k] for k in ("o", "h", "l", "c", "ch", "v"))
    ref = c - ch
    with np.errstate(all="ignore"):
        g = np.where(ref > 0, ch / ref, np.nan)
    g[(g < -0.11) | (g > 0.11)] = np.nan
    idx = np.nancumprod(np.where(np.isnan(g), 0, g) + 1, axis=0)   # 還原指數
    idx[np.isnan(c)] = np.nan

    def shift(x, k):
        out = np.full_like(x, np.nan)
        out[k:] = x[:-k]
        return out

    def roll_mean(x, w):
        cs = np.nancumsum(np.nan_to_num(x), axis=0)
        return (cs - np.vstack([np.zeros((w, x.shape[1])), cs[:-w]])) / w

    vol20 = roll_mean(v, 20)
    ma5, ma20, ma60 = roll_mean(idx, 5), roll_mean(idx, 20), roll_mean(idx, 60)
    ma60_5 = shift(ma60, 5)
    with np.errstate(all="ignore"):
        r5 = idx / shift(idx, 5) - 1
    adj_h = idx * h / c
    hi60 = np.full_like(idx, np.nan)
    for t in range(60, T):
        with np.errstate(all="ignore"):
            hi60[t] = np.nanmax(adj_h[t - 59:t + 1], axis=0)
    new_hi = np.nan_to_num(adj_h) >= np.nan_to_num(shift(hi60, 1)) - 1e-9    # 當天創 60 日新高
    recent_hi = np.zeros_like(new_hi)
    for k in range(20):
        recent_hi |= shift(new_hi.astype(float), k).astype(bool) if k else new_hi
    # RSI(2)
    up = np.maximum(np.nan_to_num(g), 0)
    dn = np.maximum(-np.nan_to_num(g), 0)
    rsi = np.full_like(idx, np.nan)
    au, ad = up[0].copy(), dn[0].copy()
    for t in range(1, T):
        au, ad = (au + up[t]) / 2, (ad + dn[t]) / 2
        with np.errstate(all="ignore"):
            rsi[t] = np.where(ad == 0, 100, 100 - 100 / (1 + au / ad))
    # 連跌天數
    down_run = np.zeros((T, N))
    for t in range(1, T):
        down_run[t] = np.where(np.nan_to_num(g[t]) < 0, down_run[t - 1] + 1, 0)
    liquid = (vol20 >= 1_000_000) & (c >= 10) & ~np.isnan(ma60) & ~np.isnan(hi60)
    liquid[:61] = False
    liquid[T - 22:] = False
    with np.errstate(all="ignore"):
        breadth = np.nanmean(np.where(vol20 >= 500_000, idx > ma20, np.nan), axis=1)
        up_trend = (idx > ma60) & (ma60 > ma60_5)
        dd60 = idx / hi60 - 1
        body_low = np.minimum(o, c)
        lower = (body_low - l) / ref
    print("計算候選訊號…", flush=True)
    rules = {
        "A 多頭急跌（5日跌≥10%）": liquid & up_trend & (r5 <= -0.10),
        "B 多頭超賣（RSI2<5）": liquid & up_trend & (rsi < 5),
        "E 跌深（距60日高≥30%）": liquid & (dd60 <= -0.30),
        "F 跌深＋長下影收紅爆量": liquid & (dd60 <= -0.30) & (lower >= 0.04) & (c > o) & (v >= vol20 * 1.5),
        "G 連跌≥5天、累計≥12%": liquid & (down_run >= 5) & (r5 <= -0.12),
    }
    rev_ok_cache = {}

    def rev_ok(t, j):
        key = (codes[j], days[t][:6], int(days[t][6:]) >= 11)
        if key not in rev_ok_cache:
            f = R.rev_features(rev, codes[j], days[t])
            rev_ok_cache[key] = bool(f and f["high12"] and f["yoy"] > 20)
        return rev_ok_cache[key]

    cand_c = liquid & (idx > ma60) & (r5 <= -0.08)
    with np.errstate(all="ignore"):
        cand_d = liquid & (np.abs(idx / ma60 - 1) <= 0.03) & recent_hi & (ma60 > ma60_5)
    events = defaultdict(list)
    for name, m in rules.items():
        events[name] = list(zip(*np.nonzero(m)))
    events["C 好公司回檔（營收＋5日跌≥8%）"] = [(t, j) for t, j in zip(*np.nonzero(cand_c)) if rev_ok(t, j)]
    events["D 好公司第一次回測季線"] = [(t, j) for t, j in zip(*np.nonzero(cand_d)) if rev_ok(t, j)]
    # 對照組
    base = [(t, j) for t in range(61, T - 22, 5) for j in np.nonzero(liquid[t])[0]]
    events["對照：所有流動股"] = base

    for name in sorted(events):
        evs = events[name]
        res = defaultdict(list)
        for t, j in evs:
            s = series[codes[j]]
            bname = "廣度<30%" if breadth[t] < 0.3 else "廣度>70%" if breadth[t] > 0.7 else "廣度30~70%"
            per = "驗證" if t >= split else "訓練"
            for hold in (5, 10, 20):
                r = strategy.hold_return_stop(s, t, T, hold, 0.10)
                if r is None or not r[1]:
                    continue
                x = r[0] - COST
                if not -60 < x < 200:
                    continue
                res[(hold, "全部", per)].append(x)
                res[(hold, bname, per)].append(x)
        print(f"\n===== {name}（{len(evs):,} 筆訊號）=====")
        for hold in (5, 10, 20):
            for gname in ("全部", "廣度<30%", "廣度30~70%", "廣度>70%"):
                print(f"持有{hold:2d}天 {gname:10s}\t訓練 {stats(res[(hold, gname, '訓練')])}\t驗證 {stats(res[(hold, gname, '驗證')])}")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
