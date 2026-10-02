#!/usr/bin/env python3
"""月營收（基本面）研究：營收成長能不能提高選股勝率？

時間規則（避免偷看未來）：每個交易日只使用「當時已公布」的營收——
每月 11 日起才使用上個月營收，10 日以前只能用上上個月的。

測試 A：在現有三套策略（明日強勢候選、起漲前夕、個股均值回歸）加上營收條件
測試 B：營收動能策略——每月 11 日後第一個交易日收盤選股，隔天開盤買進、持有 20 個交易日
兩者都分訓練期（前 75%）與驗證期（後 25%），並與同期間對照組比較。

  python3 research/revenue_study.py
"""
import datetime as dt
import glob
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import meanrev  # noqa: E402
import strategy  # noqa: E402
import study  # noqa: E402
from yaogu import CACHE_DIR  # noqa: E402

COST = strategy.ROUND_TRIP_COST


def load_revenue():
    """{代號: {YYYYMM: {rev, yoy, mom, cum_yoy}}}"""
    rev = defaultdict(dict)
    for f in glob.glob(os.path.join(CACHE_DIR, "rev_*_*.json")):
        ym = os.path.basename(f).split("_")[2][:6]
        for code, row in json.load(open(f, encoding="utf-8")).items():
            rev[code][ym] = row
    return rev


def avail_month(ymd):
    """第 ymd 這天已公布的最新營收月份（YYYYMM）。"""
    d = dt.datetime.strptime(ymd, "%Y%m%d").date()
    first = d.replace(day=1)
    back = 1 if d.day >= 11 else 2
    for _ in range(back):
        first = (first - dt.timedelta(days=1)).replace(day=1)
    return first.strftime("%Y%m")


def prev_months(ym, k):
    y, m = int(ym[:4]), int(ym[4:])
    out = []
    for _ in range(k):
        out.append(f"{y}{m:02d}")
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return out  # 由新到舊，含 ym


def rev_features(rev, code, ymd, cache={}):
    key = (code, ymd[:6], int(ymd[6:]) >= 11)
    if key in cache:
        return cache[key]
    hist = rev.get(code)
    out = None
    if hist:
        ym = avail_month(ymd)
        last12 = [hist.get(x) for x in prev_months(ym, 12)]
        cur = last12[0]
        if cur and cur["rev"] is not None and cur["yoy"] is not None:
            revs = [x["rev"] for x in last12 if x and x["rev"] is not None]
            yoys = [x["yoy"] for x in last12[:3] if x and x["yoy"] is not None]
            prev = last12[1]
            out = {
                "yoy": cur["yoy"], "mom": cur["mom"], "cum_yoy": cur["cum_yoy"],
                "high12": len(revs) >= 12 and cur["rev"] >= max(revs),
                "grow3": len(yoys) == 3 and all(y > 0 for y in yoys),
                "accel": bool(prev and prev["yoy"] is not None and cur["yoy"] > prev["yoy"]),
            }
    cache[key] = out
    return out


REV_FILTERS = [
    ("（不加營收條件）", lambda f: True),
    ("營收年增 > 0", lambda f: f and f["yoy"] > 0),
    ("營收年增 > 20%", lambda f: f and f["yoy"] > 20),
    ("營收年增 > 50%", lambda f: f and f["yoy"] > 50),
    ("營收衰退（年增 < 0）", lambda f: f and f["yoy"] < 0),
    ("連續 3 個月年增", lambda f: f and f["grow3"]),
    ("營收創 12 個月新高", lambda f: f and f["high12"]),
    ("年增加速", lambda f: f and f["accel"] and f["yoy"] > 0),
    ("累計年增 > 20%", lambda f: f and f["cum_yoy"] is not None and f["cum_yoy"] > 20),
    ("創新高 + 年增 > 20%", lambda f: f and f["high12"] and f["yoy"] > 20),
]


def stats(rs):
    if len(rs) < 20:
        return f"n={len(rs):5d}  （樣本太少）"
    avg = sum(rs) / len(rs)
    win = sum(x > 0 for x in rs) / len(rs) * 100
    return f"n={len(rs):5d}  勝率 {win:4.1f}%  平均 {avg:+.2f}%"


def table(title, items, split_t):
    """items: [(t, 報酬, 營收特徵)]"""
    print(f"\n==================== {title} ====================")
    print(f"{'營收條件':22s}{'訓練期':36s}驗證期")
    for name, cond in REV_FILTERS:
        tr = [r for t, r, f in items if t < split_t and cond(f)]
        te = [r for t, r, f in items if t >= split_t and cond(f)]
        print(f"{name:18s}\t{stats(tr)}\t{stats(te)}")


def main():
    days, series, taiex = study.load()
    rev = load_revenue()
    n = len(days)
    split_t = int(n * study.TRAIN_RATIO)
    months_loaded = sorted({ym for h in rev.values() for ym in h})
    print(f"行情 {days[0]} ～ {days[-1]}；營收 {months_loaded[0]} ～ {months_loaded[-1]}，{len(rev)} 家公司")
    print(f"訓練期到 {days[split_t - 1]}，驗證期從 {days[split_t]}")
    capital = {}

    # ---------- 測試 A：現有策略 + 營收條件
    picks = study.collect(days, series, taiex, capital)
    table("明日強勢候選 + 營收條件", [(r["t"], r["ret"], rev_features(rev, r["code"], days[r["t"]])) for r in picks], split_t)
    pre = study.collect_pre(days, series, taiex, capital)
    pre = [r for r in pre if r["mkt_up"] is not False]  # 與正式版一致：大盤弱勢暫停
    table("起漲前夕（大盤多頭）+ 營收條件", [(r["t"], r["ret"], rev_features(rev, r["code"], days[r["t"]])) for r in pre], split_t)
    mr = [r for r in meanrev.signals(days, series, taiex) if r["rsi"] < 10]
    items = []
    for r in mr:
        res = meanrev.trade(r["s"], r["t"], n, hold=5)
        if res is not None:
            items.append((r["t"], res, rev_features(rev, r["code"], days[r["t"]])))
    table("個股均值回歸（RSI2<10）+ 營收條件", items, split_t)

    # ---------- 測試 B：營收動能（每月一次選股，持有 20 個交易日）
    rebal = []
    seen = set()
    for t, d in enumerate(days):
        if int(d[6:]) >= 11 and d[:6] not in seen and t >= 60 and t + 21 < n:
            seen.add(d[:6])
            rebal.append(t)
    items = []
    for t in rebal:
        for code, s in series.items():
            r = s[t]
            if not r or r["close"] is None or r["close"] < 10:
                continue
            vols = [s[x]["volume"] for x in range(t - 19, t + 1) if s[x]]
            if len(vols) < 20 or sum(vols) / 20 < 500_000:
                continue
            ret = strategy.hold_return(s, t, n, 20)
            if ret is None:
                continue
            closes = [x["close"] for x in s[t - 59:t + 1] if x and x["close"]]
            above60 = len(closes) == 60 and r["close"] > sum(closes) / 60
            f = rev_features(rev, code, d) if (d := days[t]) else None
            items.append((t, ret - COST, f, above60))
    print("\n==================== 營收動能：每月選股、持有 20 個交易日 ====================")
    print(f"每月調整 {len(rebal)} 次；對照組＝所有流動性足夠的股票")
    print(f"{'營收條件':22s}{'訓練期':36s}驗證期")
    for name, cond in REV_FILTERS:
        for trend_name, trend in (("", lambda a: True), ("＋站上季線", lambda a: a)):
            tr = [r for t, r, f, a in items if t < split_t and cond(f) and trend(a)]
            te = [r for t, r, f, a in items if t >= split_t and cond(f) and trend(a)]
            print(f"{name + trend_name:18s}\t{stats(tr)}\t{stats(te)}")


if __name__ == "__main__":
    main()
