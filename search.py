"""個股搜尋（網站「🔍 搜尋股票」）：替所有一般股算好健檢需要的資料，輸出 site/data/search.json。

網頁搜尋時才載入這份檔案。「適不適合買」只用回測驗證過的規則判斷（判斷邏輯在 web/index.html 的 searchVerdict）：
  精選 3 檔／營收動能（營收創 12 個月新高、年增 > 20%、站上季線）／隔日沖 ⭐推薦／當沖觀察，
  以及處置股、注意股、營收衰退且跌破季線等風險。
點位：波段用精選 3 檔的回測規則（停損 -10%、持有 20 天），短線用明日樞紐點與 1.5 倍 ATR。
"""
import revenue
import strategy


def _r(x, k=2):
    return None if x is None else round(x, k)


def build(history, ref, data):
    series = strategy.build_series(history)
    t = len(history) - 1
    day = history[t][0]
    try:
        rev = revenue.load_revenue([day])
    except Exception as e:
        print(f"搜尋資料：營收讀取失敗 {e}")
        rev = {}
    rv = data.get("revenue") or {}
    lists = {
        "top3": {p["代號"] for p in (rv.get("top3") or {}).get("picks", [])},
        "rev": {p["代號"] for p in rv.get("picks", [])},
        "overnight": {p["代號"] for p in data.get("overnight") or []},
        "rec": {p["代號"] for p in data.get("overnight") or [] if p.get("推薦")},
        "day": {p["代號"] for p in (data.get("daytrade") or {}).get("list", [])},
        "picks": {p["代號"] for p in data.get("picks") or []},
        "pre": {p["代號"] for p in data.get("pre") or []},
        "radar": {r["代號"] for r in data.get("results") or []},
    }
    attention, disposal = (ref or {}).get("attention") or {}, (ref or {}).get("disposal") or {}
    out = {}
    for code, s in series.items():
        r = s[t]
        if not r or r["close"] is None or r["change"] is None or r["high"] is None:
            continue
        past = [x for x in s[max(0, t - 60):t] if x and x["close"] is not None and x["high"] is not None]
        if len(past) < 20:
            continue
        c = r["close"]
        closes = [x["close"] for x in past] + [c]
        ma20 = sum(closes[-20:]) / 20
        ma60 = sum(closes[-60:]) / 60 if len(closes) >= 60 else None
        hi60 = max(x["high"] for x in past)
        vols = [x["volume"] or 0 for x in past[-19:]] + [r["volume"] or 0]
        f = revenue.features(rev, code, day) if rev else None
        pv = strategy.pivot_levels(r["high"], r["low"], c)
        atr = strategy._atr(past + [r])
        base = past[-20:]
        quiet = (len(base) == 20 and max(x["close"] for x in base) / min(x["close"] for x in base) <= 1.15
                 and not any(x["change"] is not None and x["close"] - x["change"] > 0
                             and x["change"] / (x["close"] - x["change"]) >= 0.05 for x in base))
        prev = c - r["change"]
        tags = [k for k, v in lists.items() if code in v]
        out[code] = {
            "n": r["name"], "m": r["market"], "c": c, "p": _r(r["change"] / prev * 100) if prev > 0 else None,
            "v": round((r["volume"] or 0) / 1000), "v20": round(sum(vols) / 20 / 1000),
            "ma20": _r(ma20), "ma60": _r(ma60), "hi60": hi60, "h": r["high"],
            "atr": _r(atr), "pv": {k: _r(v) for k, v in pv.items() if k != "P"},
            "rev": {"ym": f["ym"], "yoy": _r(f["yoy"], 1), "h12": f["high12"]} if f else None,
            "tags": tags,
            "warn": ("處置股" if code in disposal else "注意股" if code in attention else None),
            "spark": [_r(x, 2) for x in closes[-20:]],
            "quiet": quiet,  # 前 20 天盤整（波動 ≤ 15%、沒有大漲）：盤中判斷「起漲第 1 根」用
        }
    live = data.get("live")
    # 盤中預覽產生的資料：價格是盤中即時價，不是收盤價
    return {"date": day.isoformat(), "live": live["time"][:5] if live and not live.get("final") else None, "stocks": out}
