"""營收動能：每月 11 日營收公布後換股，買進「營收創 12 個月新高、年增 > 20%、股價站上季線」的股票，
依 20 日成交值取前 20 檔，持有 20 個交易日。

11 年研究（research/revenue_long.py，2015/04～2026/08，137 次選股）：137 個月中 90 個月贏過所有股票，
平均每月多 2.4 個百分點，每一年都贏，包括 2018、2022 空頭年。

月營收來源：公開資訊觀測站月營收彙總表，快取在 data/cache/rev_{sii|otc}_YYYYMM.json。
時間規則（避免偷看未來）：每月 11 日起才使用上個月的營收，10 日以前只能用上上個月的。
"""
import datetime as dt
import html
import json
import os
import re
import urllib.request

import strategy
from yaogu import CACHE_DIR, UA, fetch_json, is_common_stock, num  # noqa: F401

URL = "https://mopsov.twse.com.tw/nas/t21/{market}/t21sc03_{roc}_{month}_0.html"
TOP = 20
HOLD = 20
MIN_LOTS = 500
RESEARCH = {  # research/revenue_long.py 的結果，網頁說明用
    "period": "2015/04～2026/08", "months": 137, "beat": 90, "excess": 2.38,
    "win": 51.3, "base_win": 46.8, "worst": -18.8,
    "bear": "2018 年每月平均 +0.06%（大盤 -0.99%）、2022 年 -0.52%（大盤 -1.58%）",
}

_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S | re.I)


# ---------------------------------------------------------------- 下載

def parse(page):
    out = {}
    for row in _ROW.findall(page):
        cells = [html.unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in _CELL.findall(row)]
        if len(cells) < 10 or not re.fullmatch(r"\d{4}", cells[0]):
            continue
        out[cells[0]] = {"rev": num(cells[2]), "mom": num(cells[5]), "yoy": num(cells[6]),
                         "cum_yoy": num(cells[9]), "name": cells[1]}
    return out


def fetch(market, year, month):
    url = URL.format(market=market, roc=year - 1911, month=month)
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("big5", errors="replace")


def load_month(year, month):
    """某月的上市櫃營收 {代號: {...}}。公布期間（次月 10 日前）的資料可能不完整，不寫入快取。"""
    ym = f"{year}{month:02d}"
    out = {}
    for market in ("sii", "otc"):
        path = os.path.join(CACHE_DIR, f"rev_{market}_{ym}.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                out.update(json.load(fh))
            continue
        rows = parse(fetch(market, year, month))
        nxt = dt.date(year + (month == 12), month % 12 + 1, 11)
        if rows and dt.date.today() >= nxt:
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(rows, fh, ensure_ascii=False)
        out.update(rows)
    return out


def load_revenue(dates):
    """dates 期間內選股需要的所有月份：每個換股日可用的月份，再往前 11 個月（判斷 12 個月新高）。"""
    need = set()
    for d in dates:
        need.update(prev_months(avail_month(d), 12))
    rev = {}
    for ym in sorted(need):
        try:
            rows = load_month(int(ym[:4]), int(ym[4:]))
        except Exception as e:
            print(f"  {ym} 月營收讀取失敗：{e}")
            continue
        for code, row in rows.items():
            rev.setdefault(code, {})[ym] = row
    return rev


# ---------------------------------------------------------------- 特徵

def avail_month(d):
    """第 d 天已公布的最新營收月份（YYYYMM）：11 日起為上個月，10 日以前為上上個月。"""
    first = d.replace(day=1)
    for _ in range(1 if d.day >= 11 else 2):
        first = (first - dt.timedelta(days=1)).replace(day=1)
    return first.strftime("%Y%m")


def prev_months(ym, k):
    y, m = int(ym[:4]), int(ym[4:])
    out = []
    for _ in range(k):
        out.append(f"{y}{m:02d}")
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return out  # 由新到舊，含 ym


def features(rev, code, d):
    hist = rev.get(code)
    if not hist:
        return None
    ym = avail_month(d)
    last12 = [hist.get(x) for x in prev_months(ym, 12)]
    cur = last12[0]
    if not cur or cur["rev"] is None or cur["yoy"] is None:
        return None
    revs = [x["rev"] for x in last12 if x and x["rev"] is not None]
    yoys = [x["yoy"] for x in last12[:3] if x and x["yoy"] is not None]
    prev = last12[1]
    return {
        "ym": ym, "rev": cur["rev"], "yoy": cur["yoy"], "mom": cur["mom"], "cum_yoy": cur["cum_yoy"],
        "high12": len(revs) >= 12 and cur["rev"] >= max(revs),
        "grow3": len(yoys) == 3 and all(y > 0 for y in yoys),
        "accel": bool(prev and prev["yoy"] is not None and cur["yoy"] > prev["yoy"]),
    }


def qualifies(f):
    return bool(f and f["high12"] and f["yoy"] > 20)


# ---------------------------------------------------------------- 選股與回測

def rebalance_points(dates):
    """每月 11 日之後的第一個交易日（history 的索引）。"""
    out = []
    for t, d in enumerate(dates):
        if d.day >= 11 and (t == 0 or dates[t - 1].month != d.month or dates[t - 1].day < 11):
            out.append(t)
    return out


def candidates(series, t, dates, rev):
    """第 t 天收盤後的候選股（已依 20 日成交值排序）。"""
    out = []
    for code, s in series.items():
        r = s[t]
        if not r or r["close"] is None or r["close"] < 10:
            continue
        win = [x for x in s[max(0, t - 59):t + 1] if x and x["close"] is not None]
        if len(win) < 60:
            continue
        last20 = win[-20:]
        if sum(x["volume"] for x in last20) / 20 < MIN_LOTS * 1000:
            continue
        ma60 = sum(x["close"] for x in win) / 60
        out.append({"code": code, "s": s, "close": r["close"], "ma60": ma60, "above60": r["close"] > ma60,
                    "value": sum(x["close"] * x["volume"] for x in last20) / 20,
                    "f": features(rev, code, dates[t])})
    out.sort(key=lambda x: -x["value"])
    return out


def picks(history, rev):
    """本期名單：最近一個換股日收盤後選出的前 TOP 檔，附上換股後到今天的表現。"""
    dates = [d for d, _ in history]
    points = rebalance_points(dates)
    if not points:
        return None
    t = points[-1]
    series = strategy.build_series(history)
    cands = candidates(series, t, dates, rev)
    chosen = [c for c in cands if c["above60"] and qualifies(c["f"])][:TOP]
    n = len(history)
    out = []
    for rank, c in enumerate(chosen, 1):
        s, f = c["s"], c["f"]
        entry = s[t + 1]["open"] if t + 1 < n and s[t + 1] and s[t + 1]["open"] else None
        bars = strategy.held_bars(s, t + 1, n, HOLD) if entry else []
        now = bars[-1][1]["close"] if bars else None
        reasons = [
            f"{f['ym'][:4]}/{f['ym'][4:]} 營收 {f['rev'] / 1e5:,.1f} 億，創近 12 個月新高",
            f"營收年增 {f['yoy']:+.1f}%" + (f"，累計年增 {f['cum_yoy']:+.1f}%" if f["cum_yoy"] is not None else ""),
            f"股價 {c['close']:g} 站上季線（60 日均線 {c['ma60']:.2f}），趨勢向上",
            f"20 日平均成交值 {c['value'] / 1e8:,.1f} 億，流動性排名第 {cands.index(c) + 1}",
        ]
        if f["grow3"]:
            reasons.append("連續 3 個月營收年增")
        out.append({
            "排名": rank, "代號": c["code"], "名稱": s[t]["name"], "市場": s[t]["market"],
            "選股日收盤": c["close"], "進場價": entry, "現價": now,
            "報酬%": round((now / entry - 1) * 100, 2) if entry and now else None,
            "營收年增%": f["yoy"], "月增%": f["mom"], "營收月份": f["ym"],
            "成交值(億)": round(c["value"] / 1e8, 2), "理由": reasons,
        })
    held = min(n - 1 - t, HOLD)
    return {
        "rebalance_date": dates[t].isoformat(),
        "entry_date": dates[t + 1].isoformat() if t + 1 < n else None,
        "days_held": max(held, 0), "hold": HOLD, "rev_month": avail_month(dates[t]),
        "qualified": sum(1 for c in cands if c["above60"] and qualifies(c["f"])),
        "picks": out,
    }


def backtest(history, rev):
    """用 history 期間每個換股日回測：前 TOP 檔等權重 vs 所有流動性足夠的股票。"""
    dates = [d for d, _ in history]
    series = strategy.build_series(history)
    n = len(history)
    months = []
    for t in rebalance_points(dates):
        if t < 60 or t + HOLD >= n:
            continue
        cands = candidates(series, t, dates, rev)
        rets = {}
        for c in cands:
            r = strategy.hold_return(c["s"], t, n, HOLD)
            if r is not None:
                rets[c["code"]] = r - strategy.ROUND_TRIP_COST
        chosen = [c["code"] for c in cands if c["above60"] and qualifies(c["f"]) and c["code"] in rets][:TOP]
        if not chosen or not rets:
            continue
        port = [rets[c] for c in chosen]
        months.append({"month": dates[t].strftime("%Y-%m"), "port": round(sum(port) / len(port), 2),
                       "base": round(sum(rets.values()) / len(rets), 2),
                       "wins": sum(1 for x in port if x > 0), "n": len(port)})
    if not months:
        return None
    trades = sum(m["n"] for m in months)
    return {
        "months": months,
        "beat": sum(m["port"] > m["base"] for m in months),
        "avg": round(sum(m["port"] for m in months) / len(months), 2),
        "base_avg": round(sum(m["base"] for m in months) / len(months), 2),
        "win": round(sum(m["wins"] for m in months) / trades * 100, 1),
        "worst": min(m["port"] for m in months),
        "cost": strategy.ROUND_TRIP_COST,
    }


# ---------------------------------------------------------------- 明日精選 3 檔

TOP3_RESEARCH = {  # research/top3.py 的結果（2015/04～2026/10），網頁說明用
    "period": "2015/04～2026/10", "hold": 20,
    "train": {"avg": 3.42, "win": 46.8, "base": 0.09}, "test": {"avg": 4.09, "win": 49.6, "base": 1.11},
    "years": "12/12", "hold10": "持有 10 天：訓練期 +1.61%、驗證期 +1.75%（對照 -0.40%、+0.03%）",
}
TOP3_MIN_LOTS = 1000


def _top3_at(series, t, dates, rev):
    """第 t 天收盤後的精選名單：營收動能 + 今天收盤突破前 60 日最高價，依 20 日成交值排序取前 3。"""
    out = []
    for code, s in series.items():
        r = s[t]
        if not r or r["close"] is None or r["close"] < 10 or r["high"] is None:
            continue
        past = [x for x in s[max(0, t - 60):t] if x and x["high"] is not None and x["close"] is not None]
        if len(past) < 59 or r["close"] <= max(x["high"] for x in past[-60:]):
            continue  # 先檢查最便宜的條件：今天收盤是否突破前 60 日高點
        last20 = past[-19:] + [r]
        if sum(x["volume"] for x in last20) / 20 < TOP3_MIN_LOTS * 1000:
            continue
        ma60 = (sum(x["close"] for x in past[-59:]) + r["close"]) / 60
        if r["close"] <= ma60:
            continue
        f = features(rev, code, dates[t])
        if not qualifies(f):
            continue
        out.append({"code": code, "s": s, "f": f, "ma60": ma60, "prior_high": max(x["high"] for x in past[-60:]),
                    "value": sum(x["close"] * x["volume"] for x in last20) / 20})
    out.sort(key=lambda x: -x["value"])
    return out


def top3(history, rev):
    dates = [d for d, _ in history]
    series = strategy.build_series(history)
    t = len(history) - 1
    chosen = _top3_at(series, t, dates, rev)
    picks = []
    for rank, c in enumerate(chosen[:3], 1):
        s, f, r = c["s"], c["f"], c["s"][t]
        pct = r["change"] / (r["close"] - r["change"]) * 100 if r["change"] is not None and r["close"] > r["change"] else None
        picks.append({
            "排名": rank, "代號": c["code"], "名稱": r["name"], "市場": r["market"], "收盤": r["close"],
            "漲跌%": round(pct, 2) if pct is not None else None, "營收年增%": f["yoy"], "營收月份": f["ym"],
            "成交值(億)": round(c["value"] / 1e8, 2),
            "理由": [
                f"{f['ym'][:4]}/{f['ym'][4:]} 營收創近 12 個月新高，年增 {f['yoy']:+.1f}%",
                f"今天收盤 {r['close']:g} 突破前 60 日高點 {c['prior_high']:g}",
                f"站上季線（{c['ma60']:.2f}），20 日平均成交值 {c['value'] / 1e8:,.1f} 億",
            ],
        })
    return {"date": dates[t].isoformat(), "qualified": len(chosen), "picks": picks, "hold": TOP3_RESEARCH["hold"]}


def top3_backtest(history, rev):
    """網站 2 年資料：每天精選前 3 檔，隔天開盤買、持有 20 個交易日（含除權息、已扣成本），對照站上季線的流動股。"""
    dates = [d for d, _ in history]
    series = strategy.build_series(history)
    n = len(history)
    H = TOP3_RESEARCH["hold"]
    rs, base = [], []
    for t in range(60, n - H):
        for c in _top3_at(series, t, dates, rev)[:3]:
            r = strategy.hold_return(c["s"], t, n, H)
            if r is not None:
                rs.append(r - strategy.ROUND_TRIP_COST)
        if t % 5 == 0:  # 對照組每 5 天抽樣
            for s in series.values():
                x = s[t]
                if not x or x["close"] is None or x["close"] < 10 or x["volume"] < TOP3_MIN_LOTS * 1000:
                    continue
                win = [y["close"] for y in s[max(0, t - 59):t + 1] if y and y["close"] is not None]
                if len(win) == 60 and x["close"] > sum(win) / 60:
                    r = strategy.hold_return(s, t, n, H)
                    if r is not None:
                        base.append(r - strategy.ROUND_TRIP_COST)
    return {"from": dates[60].isoformat(), "to": dates[n - H - 1].isoformat(),
            "plan": strategy._stats(rs), "baseline": strategy._stats(base), "hold": H}


def summary(history):
    """營收動能分頁需要的資料；營收抓不到時回傳 None，不影響其他分頁。"""
    dates = [d for d, _ in history]
    points = rebalance_points(dates)
    rev = load_revenue([dates[t] for t in points])
    p = picks(history, rev)
    if p is None:
        return None
    p["backtest"] = backtest(history, rev)
    p["research"] = RESEARCH
    p["top3"] = top3(history, rev)
    p["top3_backtest"] = top3_backtest(history, rev)
    p["top3_research"] = TOP3_RESEARCH
    return p
