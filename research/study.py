#!/usr/bin/env python3
"""用長期歷史資料研究「哪些額外條件能提高明日強勢候選的勝率與報酬」。

做法：
  1. 讀取 data/cache 的所有交易日，納入期間內出現過的所有股票（含已下市），避免存活者偏差
  2. 找出符合現行選股條件（strategy.passes）的訊號，照現行操作計畫（strategy.simulate）模擬交易
  3. 為每筆訊號加上候選濾網：大盤趨勢、法人買賣超、量比、漲幅、股本……
  4. 前 75% 期間為訓練期（用來挑濾網），最後 25% 為驗證期（只用來確認，不拿來調整）

  python3 research/study.py
"""
import datetime as dt
import glob
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import strategy  # noqa: E402
import yaogu  # noqa: E402
from yaogu import CACHE_DIR, pct_change  # noqa: E402

TRAIN_RATIO = 0.75
KEYS = ("open", "high", "low", "close", "change", "volume")


def load():
    days = []
    for f in sorted(glob.glob(os.path.join(CACHE_DIR, "twse_*.json"))):
        ymd = os.path.basename(f)[5:13]
        tp = os.path.join(CACHE_DIR, f"tpex_{ymd}.json")
        # 假日也會留下空的快取檔，只有兩個市場都有資料才算交易日
        if os.path.exists(tp) and os.path.getsize(f) > 10 and os.path.getsize(tp) > 10:
            days.append(ymd)
    n = len(days)
    series = defaultdict(lambda: [None] * n)
    for t, ymd in enumerate(days):
        for market in ("twse", "tpex"):
            with open(os.path.join(CACHE_DIR, f"{market}_{ymd}.json"), encoding="utf-8") as fh:
                for r in json.load(fh):
                    if r["close"] is None:
                        continue
                    series[r["code"]][t] = {k: r[k] for k in KEYS}
    taiex_path = os.path.join(CACHE_DIR, "taiex.json")
    taiex = json.load(open(taiex_path)) if os.path.exists(taiex_path) else {}
    return days, dict(series), taiex


def load_insti(ymd):
    out = {}
    for market in ("twse", "tpex"):
        p = os.path.join(CACHE_DIR, f"insti_{market}_{ymd}.json")
        if os.path.exists(p):
            out.update(json.load(open(p)))
    return out if out else None


def market_state(days, taiex, t):
    """加權指數是否在 20 日均線之上、5 日漲跌幅。"""
    closes = [taiex.get(d) for d in days[max(0, t - 19):t + 1]]
    closes = [c for c in closes if c]
    if len(closes) < 20 or not taiex.get(days[t]):
        return None, None
    ma20 = sum(closes) / 20
    prev5 = taiex.get(days[t - 5]) if t >= 5 else None
    r5 = (taiex[days[t]] / prev5 - 1) * 100 if prev5 else None
    return taiex[days[t]] > ma20, r5


def collect(days, series, taiex, capital):
    n = len(days)
    insti_cache = {}
    rows = []
    for t in range(20, n - strategy.MAX_HOLD - 1):
        for code, s in series.items():
            r = s[t]
            if r is None or r["volume"] < 1_000_000:
                continue
            p = pct_change(r)
            if p is None or p < strategy.DEFAULTS["min_pct"]:
                continue
            f = strategy.features(s, t)
            if not f or not strategy.passes(f):
                continue
            nxt = s[t + 1]
            if not nxt or not nxt["open"]:
                continue
            res = strategy.simulate(s, t, f, n)
            if res is None:
                continue  # 開盤不在進場區間，沒有進場
            if days[t] not in insti_cache:
                insti_cache[days[t]] = load_insti(days[t])
            ins = insti_cache[days[t]]
            up, mr5 = market_state(days, taiex, t)
            vol_shares = r["volume"]
            iv = ins.get(code) if ins else None
            cap = capital.get(code)
            rows.append({
                "t": t, "date": days[t], "code": code, "ret": res,
                "pct": f["pct"], "vol_ratio": f["vol_ratio"], "r5": f["r5"],
                "close_pos": f["close_pos"], "limit": f["pct"] >= 9.5, "streak": f["streak"],
                "ma_bull": bool(f["ma5"] and f["ma10"] and f["ma5"] > f["ma10"] > f["ma20"]),
                "breakout_pct": (f["close"] / f["prior_high"] - 1) * 100,
                "cap_e": cap / 1e8 if cap else None,
                "price": f["close"],
                "mkt_up": up, "mkt_r5": mr5,
                "has_insti": iv is not None,
                "foreign": iv[0] / vol_shares if iv else None,
                "trust": iv[1] / vol_shares if iv else None,
                "insti_total": iv[2] / vol_shares if iv else None,
            })
    return rows


def stats(rs):
    if not rs:
        return "  n=   0"
    rets = [r["ret"] for r in rs]
    avg = sum(rets) / len(rets)
    win = sum(x > 0 for x in rets) / len(rets) * 100
    wins = [x for x in rets if x > 0]
    losses = [-x for x in rets if x <= 0]
    pf = sum(wins) / sum(losses) if losses and sum(losses) > 0 else float("inf")
    return f"n={len(rs):4d}  平均 {avg:+.2f}%  勝率 {win:4.1f}%  獲利因子 {pf:4.2f}"


FILTERS = [
    ("現行規則（無額外濾網）", lambda r: True),
    ("大盤站上 20 日均線", lambda r: r["mkt_up"] is True),
    ("大盤在 20 日均線下", lambda r: r["mkt_up"] is False),
    ("大盤 5 日上漲", lambda r: r["mkt_r5"] is not None and r["mkt_r5"] > 0),
    ("投信買超", lambda r: r["trust"] is not None and r["trust"] > 0),
    ("外資買超", lambda r: r["foreign"] is not None and r["foreign"] > 0),
    ("三大法人合計買超", lambda r: r["insti_total"] is not None and r["insti_total"] > 0),
    ("三大法人買超 ≥ 成交量 5%", lambda r: r["insti_total"] is not None and r["insti_total"] >= 0.05),
    ("三大法人賣超", lambda r: r["insti_total"] is not None and r["insti_total"] < 0),
    ("漲停", lambda r: r["limit"]),
    ("未漲停（4～9.5%）", lambda r: not r["limit"]),
    ("首根漲停", lambda r: r["limit"] and r["streak"] == 1),
    ("量比 2～5", lambda r: r["vol_ratio"] <= 5),
    ("量比 > 5", lambda r: r["vol_ratio"] > 5),
    ("5 日漲幅 ≤ 10%", lambda r: r["r5"] <= 10),
    ("均線多頭排列", lambda r: r["ma_bull"]),
    ("突破幅度 ≤ 3%", lambda r: r["breakout_pct"] <= 3),
    ("股本 < 10 億", lambda r: r["cap_e"] is not None and r["cap_e"] < 10),
    ("股本 ≥ 30 億", lambda r: r["cap_e"] is not None and r["cap_e"] >= 30),
    ("股價 < 50 元", lambda r: r["price"] < 50),
    ("股價 ≥ 100 元", lambda r: r["price"] >= 100),
    ("大盤多頭 + 法人買超", lambda r: r["mkt_up"] is True and r["insti_total"] is not None and r["insti_total"] > 0),
    ("大盤多頭 + 投信買超", lambda r: r["mkt_up"] is True and r["trust"] is not None and r["trust"] > 0),
]


def main():
    days, series, taiex = load()
    ref_files = sorted(glob.glob(os.path.join(CACHE_DIR, "ref_*.json")))
    capital = json.load(open(ref_files[-1]))["capital"] if ref_files else {}
    print(f"資料：{days[0]} ～ {days[-1]}，{len(days)} 個交易日，{len(series)} 檔股票（含期間內下市）")
    insti_days = sum(1 for d in days if os.path.exists(os.path.join(CACHE_DIR, f"insti_twse_{d}.json")))
    print(f"加權指數 {len(taiex)} 天、法人資料 {insti_days} 天")

    rows = collect(days, series, taiex, capital)
    split_t = int(len(days) * TRAIN_RATIO)
    train = [r for r in rows if r["t"] < split_t]
    test = [r for r in rows if r["t"] >= split_t]
    print(f"訓練期 {days[20]} ～ {days[split_t - 1]}，驗證期 {days[split_t]} ～ {days[-1]}")
    print(f"有進場的交易：訓練 {len(train)} 筆、驗證 {len(test)} 筆\n")

    print(f"{'濾網':26s} {'訓練期':48s} 驗證期")
    for name, fn in FILTERS:
        print(f"{name:20s}\t{stats([r for r in train if fn(r)])}\t{stats([r for r in test if fn(r)])}")

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "signals.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"days": days, "split_t": split_t, "rows": rows}, fh)
    print(f"\n訊號明細已存到 {out}")


if __name__ == "__main__":
    main()
