#!/usr/bin/env python3
"""系統性掃描：有沒有「短線勝率 ≥ 80%、扣成本後平均為正、訓練與驗證期都成立、平均每週至少 1 次」的條件組合（11 年）。

需要 numpy（研究用，網站不需要）：pip3 install --user numpy

做法：
  股票範圍：一般股、股價 ≥ 10、20 日均量 ≥ 500 張。價格以漲跌（change）還原，含除權息。
  10 種條件（第 t 天收盤後就知道）分箱：當天漲跌、收盤在當天區間的位置、量比、距 60 日高、月線／季線趨勢、
    近 5 日漲跌、開盤跳空、流動性、大盤當天漲跌（全體中位數）、市場廣度（站上月線比例）
  6 種進出場：
    C→O1 收盤買、隔天開盤賣        C→C1 收盤買、隔天收盤賣
    C→C3 收盤買、3 天後收盤賣      C→C5 收盤買、5 天後收盤賣
    O→C1 隔天開盤買、當天收盤賣（當沖，成本 0.435%）   O→C5 隔天開盤買、5 天後收盤賣
  掃描單一條件、任兩種、任三種條件的所有組合，列出符合標準者與最接近的組合。

  python3 research/win80_scan.py
"""
import itertools
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import revenue_long as L  # noqa: E402

COST, COST_DAY = 0.585, 0.435
MIN_TRAIN, MIN_VAL, WIN = 400, 100, 80.0


def load():
    days = L.trading_days()
    idx = {}
    T = len(days)
    arr = {k: np.full((T, 2600), np.nan, dtype=np.float32) for k in ("o", "h", "l", "c", "ch", "v")}
    for t, ymd in enumerate(days):
        for m in ("twse", "tpex"):
            for r in json.load(open(os.path.join(L.CACHE_DIR, f"{m}_{ymd}.json"), encoding="utf-8")):
                code = r["code"]
                if len(code) != 4 or not code.isdigit() or code[0] == "0":
                    continue
                if None in (r["open"], r["high"], r["low"], r["close"], r["change"]):
                    continue
                j = idx.setdefault(code, len(idx))
                if j >= 2600:
                    continue
                arr["o"][t, j], arr["h"][t, j], arr["l"][t, j] = r["open"], r["high"], r["low"]
                arr["c"][t, j], arr["ch"][t, j], arr["v"][t, j] = r["close"], r["change"], r["volume"] or 0
        if t % 500 == 0:
            print(f"  載入 {ymd}（{t}/{T}）", flush=True)
    n = len(idx)
    return days, {k: a[:, :n] for k, a in arr.items()}


def main():
    days, A = load()
    T, N = A["c"].shape
    split = int(T * 0.75)
    o, h, l, c, ch, v = (A[k].astype(np.float64) for k in ("o", "h", "l", "c", "ch", "v"))
    ref = c - ch
    g = c / ref - 1                                   # 當天漲跌（以參考價計，已排除除權息）
    g[(g < -0.11) | (g > 0.11)] = np.nan               # 減資、恢復交易等斷層
    # 還原指數：逐日累乘漲跌
    idx = np.nancumprod(np.where(np.isnan(g), 0, g) + 1, axis=0)
    idx[np.isnan(c)] = np.nan
    prev_idx = np.vstack([np.full((1, N), np.nan), idx[:-1]])
    adj_o = prev_idx * (o / ref)                      # 還原開盤
    gap = o / ref - 1

    def shift(x, k):                                  # x[t + k]
        out = np.full_like(x, np.nan)
        out[:-k] = x[k:]
        return out

    def roll_mean(x, w):
        cs = np.nancumsum(np.nan_to_num(x), axis=0)
        cnt = np.cumsum(~np.isnan(x), axis=0)
        s = cs - np.vstack([np.zeros((w, x.shape[1])), cs[:-w]])
        k = cnt - np.vstack([np.zeros((w, x.shape[1])), cnt[:-w]])
        out = s / np.where(k == 0, np.nan, k)
        out[k < w * 0.9] = np.nan
        return out

    def roll_max_prev(x, w):                          # 前 w 天（不含今天）最大值
        out = np.full_like(x, np.nan)
        for t in range(w, x.shape[0]):
            out[t] = np.nanmax(x[t - w:t], axis=0)
        return out

    print("計算特徵…", flush=True)
    vol20 = roll_mean(v, 20)
    vol20_prev = np.vstack([np.full((1, N), np.nan), vol20[:-1]])
    ma20, ma60 = roll_mean(idx, 20), roll_mean(idx, 60)
    adj_h = idx * (h / c)
    hi60 = roll_max_prev(adj_h, 60)
    ret5 = idx / np.vstack([np.full((5, N), np.nan), idx[:-5]]) - 1
    rng = h - l
    pos = np.where(rng > 0, (c - l) / np.where(rng > 0, rng, 1), np.nan)   # 一字線（沒有高低區間）另外分組
    mkt = np.nanmedian(np.where(vol20 >= 500_000, g, np.nan), axis=1)            # 大盤當天（流動股中位數）
    breadth = np.nanmean(np.where(vol20 >= 500_000, idx > ma20, np.nan), axis=1)  # 站上月線比例

    univ = (c >= 10) & (vol20 >= 500_000) & ~np.isnan(g) & ~np.isnan(ma60) & ~np.isnan(hi60)
    univ[:61] = False
    lim = g >= 0.095

    def bins(x, edges):
        return np.digitize(x, edges).astype(np.int16)

    F = {
        "當天漲跌": (np.where(lim, 6, bins(g * 100, [-7, -3, 0, 3, 7])), ["≤-7%", "-7~-3%", "-3~0%", "0~3%", "3~7%", "7~9.5%", "漲停"]),
        "收盤位置": (np.where(np.isnan(pos), 3, bins(pos, [0.2, 0.8])), ["收在低檔", "收在中間", "收在高檔", "一字線"]),
        "量比": (bins(v / vol20_prev, [0.7, 1.5, 3]), ["<0.7", "0.7~1.5", "1.5~3", ">3"]),
        "距60日高": (bins(idx / hi60 - 1, [-0.15, -0.05, 0]), ["<-15%", "-15~-5%", "-5~0%", "突破"]),
        "趨勢": ((idx > ma20).astype(np.int16) * 2 + (idx > ma60).astype(np.int16), ["月季線下", "季線上月線下", "月線上季線下", "月季線上"]),
        "近5日": (bins(ret5 * 100, [-10, -3, 3, 10]), ["<-10%", "-10~-3%", "-3~3%", "3~10%", ">10%"]),
        "開盤跳空": (bins(gap * 100, [-3, 0, 3]), ["<-3%", "-3~0%", "0~3%", ">3%"]),
        "流動性": ((vol20 >= 3_000_000).astype(np.int16), ["均量500~3000張", "均量≥3000張"]),
        "大盤當天": (np.repeat(bins(mkt * 100, [-1, 1])[:, None], N, axis=1), ["大盤跌>1%", "大盤平", "大盤漲>1%"]),
        "市場廣度": (np.repeat(bins(breadth, [0.3, 0.7])[:, None], N, axis=1), ["廣度<30%", "廣度30~70%", "廣度>70%"]),
    }
    nxt_o, nxt_c = shift(adj_o, 1), shift(idx, 1)
    E = {
        "收盤買→隔天開盤賣": (nxt_o / idx - 1) * 100 - COST,
        "收盤買→隔天收盤賣": (nxt_c / idx - 1) * 100 - COST,
        "收盤買→3天後收盤賣": (shift(idx, 3) / idx - 1) * 100 - COST,
        "收盤買→5天後收盤賣": (shift(idx, 5) / idx - 1) * 100 - COST,
        "隔天開盤買→當天收盤賣": (nxt_c / nxt_o - 1) * 100 - COST_DAY,
        "隔天開盤買→5天後收盤賣": (shift(idx, 5) / nxt_o - 1) * 100 - COST,
    }
    # 收盤鎖在漲停價：收盤排隊多半買不到，「收盤買」的做法不算這些（隔天開盤買的不受影響）
    locked_close = np.zeros_like(lim)
    tick = np.select([ref < 10, ref < 50, ref < 100, ref < 500, ref < 1000], [0.01, 0.05, 0.1, 0.5, 1], 5)
    limit_px = np.floor(ref * 1.1 / tick + 1e-6) * tick
    locked_close = c >= limit_px - 1e-6
    for k in list(E):
        if k.startswith("收盤買"):
            E[k] = np.where(locked_close, np.nan, E[k])
    period = np.repeat((np.arange(T) >= split)[:, None], N, axis=1)
    ok_any = univ.copy()
    for r in E.values():
        ok_any &= np.isnan(r) | (np.abs(r) < 40)
    sel = np.nonzero(ok_any)
    names = list(F)
    feat = {k: F[k][0][sel].astype(np.int64) for k in names}
    nb = {k: len(F[k][1]) for k in names}
    per = period[sel]
    rets = {k: r[sel] for k, r in E.items()}
    tday = sel[0]
    year = np.array([int(d[:4]) for d in days])[tday]
    years_train = split / 245
    print(f"樣本 {len(per):,} 筆（{days[61]}～{days[-1]}；驗證期從 {days[split]}）", flush=True)

    results = []
    for size in (1, 2, 3):
        for combo in itertools.combinations(names, size):
            key = np.zeros(len(per), dtype=np.int64)
            mult = 1
            for k in combo:
                key += feat[k] * mult
                mult *= nb[k]
            for en, r in rets.items():
                stats = []
                valid = ~np.isnan(r)
                for pv in (False, True):
                    m = (per == pv) & valid
                    kk = key[m]
                    cnt = np.bincount(kk, minlength=mult)
                    win = np.bincount(kk, weights=(r[m] > 0).astype(np.float64), minlength=mult)
                    sm = np.bincount(kk, weights=r[m], minlength=mult)
                    stats.append((cnt, win, sm))
                (c0, w0, s0), (c1, w1, s1) = stats
                good = (c0 >= MIN_TRAIN) & (c1 >= MIN_VAL)
                for cell in np.nonzero(good)[0]:
                    wr0, wr1 = w0[cell] / c0[cell] * 100, w1[cell] / c1[cell] * 100
                    a0, a1 = s0[cell] / c0[cell], s1[cell] / c1[cell]
                    if a0 <= 0 or a1 <= 0:
                        continue
                    label, rem = [], int(cell)
                    for k in combo:
                        label.append(f"{k}:{F[k][1][rem % nb[k]]}")
                        rem //= nb[k]
                    if min(wr0, wr1) >= 58:  # 只對接近的候選計算出現天數與年度分布
                        mm = (key == cell) & valid & ~per
                        ndays = len(np.unique(tday[mm]))
                        nyears = len(np.unique(year[mm]))
                    else:
                        ndays = nyears = 0
                    results.append((min(wr0, wr1), wr0, wr1, a0, a1, int(c0[cell]), int(c1[cell]), en, "＋".join(label), ndays, nyears))
        print(f"  {size} 種條件組合完成，累計候選 {len(results):,}", flush=True)

    results.sort(reverse=True)
    show = lambda x: (f"  勝率 {x[1]:.1f}%／{x[2]:.1f}%  平均 {x[3]:+.2f}%／{x[4]:+.2f}%  n={x[5]}/{x[6]}  "  # noqa: E731
                      f"訓練期出現 {x[9]} 天、{x[10]} 個年度  {x[7]}｜{x[8]}")
    hits = [x for x in results if x[0] >= WIN and x[9] >= 300 and x[10] >= 6]
    print(f"\n符合（勝率 ≥ {WIN:.0f}%、平均 > 0、兩段都成立、訓練期 ≥ 300 個交易日有訊號、分散在 ≥ 6 個年度）：{len(hits)} 組")
    for x in hits[:30]:
        print(show(x))
    near = [x for x in results if x[9] >= 300 and x[10] >= 6]
    print("\n頻率與分散度都夠、勝率最高的 20 組：")
    for x in near[:20]:
        print(show(x))
    print("\n只看勝率（不管頻率）最高的 10 組：")
    for x in results[:10]:
        print(show(x))


if __name__ == "__main__":
    main()
