#!/usr/bin/env python3
"""產生「歷史相似情況」對照表 web/similar.json（搜尋卡片用；分組定義見 similar.py）。

需要 numpy（研究用）。用本機 11 年行情快取計算，結果提交到 repo；建議每季重跑一次：
  python3 research/build_similar.py

每格統計：[訓練期筆數, 5 日平均, 5 日上漲機率, 20 日平均, 20 日上漲機率, 20 日最差 10% 的分界,
          驗證期筆數, 5 日平均, 5 日上漲機率, 20 日平均, 20 日上漲機率, 20 日最差 10% 的分界]
進場為隔天開盤、出場為 5／20 個交易日後收盤，含除權息、已扣成本 0.585%。
"""
import datetime as dt
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import similar  # noqa: E402
import win80_scan  # noqa: E402

COST = 0.585
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "similar.json")


def main():
    days, A = win80_scan.load()
    T, N = A["c"].shape
    split = int(T * 0.75)
    o, h, c, ch, v = (A[k].astype(np.float64) for k in ("o", "h", "c", "ch", "v"))
    ref = c - ch
    g = c / ref - 1
    g[(g < -0.11) | (g > 0.11)] = np.nan
    idx = np.nancumprod(np.where(np.isnan(g), 0, g) + 1, axis=0)
    idx[np.isnan(c)] = np.nan
    prev_idx = np.vstack([np.full((1, N), np.nan), idx[:-1]])
    adj_o = prev_idx * (o / ref)

    def shift(x, k):
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

    print("計算特徵…", flush=True)
    vol20 = roll_mean(v, 20)
    vol20_prev = np.vstack([np.full((1, N), np.nan), vol20[:-1]])
    ma20, ma60 = roll_mean(idx, 20), roll_mean(idx, 60)
    adj_h = idx * (h / c)
    hi60 = np.full_like(idx, np.nan)
    for t in range(60, T):
        with np.errstate(all="ignore"):
            hi60[t] = np.nanmax(adj_h[t - 60:t], axis=0)
    ret5 = idx / np.vstack([np.full((5, N), np.nan), idx[:-5]]) - 1
    liquid = vol20 >= 500_000
    with np.errstate(all="ignore"):
        breadth = np.nanmean(np.where(liquid, idx > ma20, np.nan), axis=1)

    univ = (c >= 10) & liquid & ~np.isnan(g) & ~np.isnan(ma60) & ~np.isnan(hi60) & ~np.isnan(ret5) & (vol20_prev > 0)
    univ[:61] = False
    entry = shift(adj_o, 1)
    r5 = (shift(idx, 5) / entry - 1) * 100 - COST
    r20 = (shift(idx, 20) / entry - 1) * 100 - COST
    ok = univ & ~np.isnan(r5) & ~np.isnan(r20) & (np.abs(r5) < 60) & (np.abs(r20) < 150)
    sel = np.nonzero(ok)
    E = similar.EDGES
    b = {
        "chg": np.where(g[sel] >= 0.095, 6, np.digitize(g[sel] * 100, E["chg"])),
        "trend": (idx[sel] > ma20[sel]) * 2 + (idx[sel] > ma60[sel]) * 1,
        "dist60": np.digitize((idx[sel] / hi60[sel] - 1) * 100, E["dist60"]),
        "ret5": np.digitize(ret5[sel] * 100, E["ret5"]),
        "vr": np.digitize(v[sel] / vol20_prev[sel], E["vr"]),
        "breadth": np.digitize(breadth[sel[0]], E["breadth"]),
    }
    per = sel[0] >= split
    x5, x20 = r5[sel], r20[sel]
    print(f"樣本 {len(per):,} 筆；驗證期從 {days[split]}", flush=True)

    def stats(mask_keys, keys):
        out = {}
        for pv in (False, True):
            m = mask_keys & (per == pv)
            kk, a5, a20 = keys[m], x5[m], x20[m]
            order = np.lexsort((a20, kk))
            kk_s, a20_s = kk[order], a20[order]
            uniq, start, cnt = np.unique(kk_s, return_index=True, return_counts=True)
            s5 = np.bincount(kk, weights=a5, minlength=uniq.max() + 1 if len(uniq) else 1)
            w5 = np.bincount(kk, weights=(a5 > 0).astype(float), minlength=len(s5))
            s20 = np.bincount(kk, weights=a20, minlength=len(s5))
            w20 = np.bincount(kk, weights=(a20 > 0).astype(float), minlength=len(s5))
            for u, st, n in zip(uniq, start, cnt):
                p10 = a20_s[st + int(0.1 * (n - 1))]
                out.setdefault(int(u), [0, 0, 0, 0, 0, 0] * 2)
                base = 6 if pv else 0
                out[int(u)][base:base + 6] = [int(n), round(s5[u] / n, 2), round(w5[u] / n * 100, 1),
                                              round(s20[u] / n, 2), round(w20[u] / n * 100, 1), round(float(p10), 2)]
        return out

    table = {}
    for li, level in enumerate(similar.LEVELS):
        keys = np.zeros(len(per), dtype=np.int64)
        mult, sizes = 1, []
        for f in level:
            nb = len(similar.LABELS[f])
            keys += b[f].astype(np.int64) * mult
            sizes.append((f, mult, nb))
            mult *= nb
        res = stats(np.ones(len(per), bool), keys)
        cells = {}
        for k, st in res.items():
            if st[0] < 50:
                continue
            parts = [str((k // m) % nb) for f, m, nb in sizes]
            cells["-".join(parts)] = st
        table[str(li)] = cells
        print(f"  第 {li} 層 {level}：{len(cells)} 格", flush=True)
    out = {"generated": dt.date.today().isoformat(), "period": f"{days[61]}～{days[-1]}", "split": days[split],
           "levels": [list(lv) for lv in similar.LEVELS], "cells": table}
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"已輸出 {OUT}（{os.path.getsize(OUT) / 1024:.0f} KB）")


if __name__ == "__main__":
    main()
