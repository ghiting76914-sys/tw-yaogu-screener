"""0050 專區：每日進場參考價位與回測。

資料：證交所 STOCK_DAY（個股每月日成交資訊），每月一次請求，快取在 data/cache/etf_0050_YYYYMM.json。
還原股價：用每日「參考價（收盤 - 漲跌）÷ 昨收」反推除權息與分割（0050 於 2025/06 一拆四），
把之前的價格乘上累積係數，讓報酬計算包含配息、不受分割影響。
"""
import datetime as dt
import json
import os

from yaogu import CACHE_DIR, fetch_json, num

CODE = "0050"
START = dt.date(2012, 1, 1)


def _num(s):
    """STOCK_DAY 的漲跌欄可能是 '+0.85'、'-0.39'、'X0.00'（除權息）。"""
    s = str(s).replace(",", "").strip()
    sign = -1 if s.startswith("-") else 1
    v = num(s.lstrip("+-Xx "))
    return None if v is None else sign * v


def load_month(year, month):
    ymd = f"{year}{month:02d}"
    path = os.path.join(CACHE_DIR, f"etf_{CODE}_{ymd}.json")
    today = dt.date.today()
    current = (year, month) == (today.year, today.month)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            cached = json.load(fh)
        # 當月資料每天收盤後要補上新的一天
        if not current or cached.get("fetched") == today.isoformat() and cached.get("complete"):
            return cached["rows"]
    d = fetch_json(f"https://www.twse.com.tw/exchangeReport/STOCK_DAY?response=json&date={ymd}01&stockNo={CODE}")
    rows = []
    for r in d.get("data", []):
        y, m, dd = r[0].split("/")
        rows.append({
            "date": f"{int(y) + 1911}-{m}-{dd}",
            "open": num(r[3]), "high": num(r[4]), "low": num(r[5]), "close": num(r[6]),
            "change": _num(r[7]), "volume": num(r[1]),
        })
    now = dt.datetime.now()
    complete = not current or (rows and rows[-1]["date"] == today.isoformat() and now.hour >= 14)
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"fetched": today.isoformat(), "complete": bool(complete), "rows": rows}, fh, ensure_ascii=False)
    return rows


def load_history(start=START):
    today = dt.date.today()
    y, m = start.year, start.month
    rows = []
    while (y, m) <= (today.year, today.month):
        rows += load_month(y, m)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    rows = [r for r in rows if r["close"] is not None and r["open"] is not None]
    return adjust(rows)


def adjust(rows):
    """加上還原股價欄位 adj_open/adj_high/adj_low/adj_close（以最新價格為基準往前還原）。"""
    factors = [1.0] * len(rows)
    for i in range(1, len(rows)):
        prev, r = rows[i - 1]["close"], rows[i]
        if r["change"] is None or not prev:
            continue
        ref = r["close"] - r["change"]
        ratio = ref / prev
        # 正常交易日 ratio = 1；除息約 0.95～0.999；一拆四約 0.25
        factors[i] = ratio if ratio < 0.999 else 1.0
    cum = 1.0
    for i in range(len(rows) - 1, -1, -1):
        r = rows[i]
        for k in ("open", "high", "low", "close"):
            r[f"adj_{k}"] = r[k] * cum
        cum *= factors[i]
    return rows


# ================================================================ 指標、回測與每日建議

COST = 0.001425  # 買進手續費（不含折扣）
LEVELS = (("ma20", "月線"), ("ma60", "季線"), ("ma120", "半年線"))


def indicators(rows):
    closes = [r["adj_close"] for r in rows]
    for i, r in enumerate(rows):
        for k in (20, 60, 120):
            r[f"ma{k}"] = sum(closes[i - k + 1:i + 1]) / k if i >= k - 1 else None
        r["dd"] = closes[i] / max(closes[max(0, i - 249):i + 1]) - 1  # 距 52 週高點
        r["bias60"] = closes[i] / r["ma60"] - 1 if r["ma60"] else None
    return rows


def months(rows):
    by = {}
    for i, r in enumerate(rows):
        by.setdefault(r["date"][:7], []).append(i)
    return [by[k] for k in sorted(by)]


def fill(rows, idxs, limit_fn):
    """在 idxs 這些交易日掛限價單（價位用前一天收盤後的資料算），回傳成交價；沒成交回傳 None。"""
    for i in idxs:
        lim = limit_fn(rows[i - 1]) if i > 0 else None
        if lim is None:
            continue
        if rows[i]["adj_low"] <= lim:
            return min(rows[i]["adj_open"], lim)
    return None


def _limit_or_end(rows, idxs, fn):
    px = fill(rows, idxs, fn)
    return px if px is not None else rows[idxs[-1]]["adj_close"]


# 每月投入固定金額的買法：回傳 [(資金比例, 成交價)]；限價單整個月沒成交就月底收盤補買
STRATEGIES = {
    "定期定額（月初開盤）": lambda rows, idxs: [(1, rows[idxs[0]]["adj_open"])],
    "兩檔分批（一半月初、一半掛月線）": lambda rows, idxs: [
        (0.5, rows[idxs[0]]["adj_open"]), (0.5, _limit_or_end(rows, idxs, lambda p: p["ma20"]))],
    "掛月線限價": lambda rows, idxs: [(1, _limit_or_end(rows, idxs, lambda p: p["ma20"]))],
    "掛季線限價": lambda rows, idxs: [(1, _limit_or_end(rows, idxs, lambda p: p["ma60"]))],
    "三檔分批（月線／季線／半年線）": lambda rows, idxs: [
        (1 / 3, _limit_or_end(rows, idxs, lambda p: p["ma20"])),
        (1 / 3, _limit_or_end(rows, idxs, lambda p: p["ma60"])),
        (1 / 3, _limit_or_end(rows, idxs, lambda p: p["ma120"]))],
    "定期定額（月底收盤）": lambda rows, idxs: [(1, rows[idxs[-1]]["adj_close"])],
}


def avg_cost(rows, strategy, start_i, end_i):
    invested = units = 0.0
    for idxs in months(rows):
        idxs = [i for i in idxs if start_i <= i <= end_i]
        if not idxs:
            continue
        for w, px in strategy(rows, idxs):
            invested += w
            units += w * (1 - COST) / px
    return invested / units


def backtest(rows, start=120):
    """各買法的平均成本相對「定期定額（月初開盤）」的差異 %，分全期與三個子期間。"""
    n = len(rows)
    cuts = [start, start + (n - start) // 3, start + 2 * (n - start) // 3, n]
    periods = [("全期", start, n - 1)] + [
        (f"{rows[a]['date'][:7]}～{rows[b - 1]['date'][:7]}", a, b - 1) for a, b in zip(cuts, cuts[1:])]
    base = {p: avg_cost(rows, STRATEGIES["定期定額（月初開盤）"], a, b) for p, a, b in periods}
    table = []
    for name, fn in STRATEGIES.items():
        table.append({"name": name, "diff": [round((avg_cost(rows, fn, a, b) / base[p] - 1) * 100, 2)
                                             for p, a, b in periods]})
    return {"periods": [p for p, _, _ in periods], "rows": table,
            "from": rows[start]["date"], "to": rows[-1]["date"]}


def hit_rates(rows, start=120, horizon=20):
    """任一天收盤後掛單，未來 horizon 個交易日內碰到各均線價位的歷史機率。"""
    out = {}
    for key, label in LEVELS:
        tries = hits = 0
        for i in range(start, len(rows) - horizon):
            lim = rows[i][key]
            if lim is None or lim >= rows[i]["adj_close"]:
                continue  # 價位已在現價之上，掛單當下就會成交，不列入
            tries += 1
            hits += any(rows[j]["adj_low"] <= lim for j in range(i + 1, i + 1 + horizon))
        out[key] = round(hits / tries * 100) if tries else None
    return out


def summary():
    """0050 專區需要的所有資料。價位皆以還原股價計算，最新價格即實際成交價。"""
    rows = indicators(load_history())
    last = rows[-1]
    hist_bias = sorted(r["bias60"] for r in rows[120:] if r["bias60"] is not None)
    pct_rank = round(sum(b <= last["bias60"] for b in hist_bias) / len(hist_bias) * 100)
    month_idx = [i for i, r in enumerate(rows) if r["date"][:7] == last["date"][:7]]
    rates = hit_rates(rows)
    levels = []
    for key, label in LEVELS:
        price = round(last[key], 2)
        levels.append({"key": key, "label": label, "price": price,
                       "diff": round((price / last["close"] - 1) * 100, 2),
                       "hit": rates[key], "above": price >= last["close"]})
    temp = "偏熱" if pct_rank >= 80 else "偏冷" if pct_rank <= 20 else "正常"
    return {
        "date": last["date"], "close": last["close"], "change": last["change"],
        "pct": round(last["change"] / (last["close"] - last["change"]) * 100, 2) if last["change"] is not None else None,
        "levels": levels, "bias60": round(last["bias60"] * 100, 2), "bias_rank": pct_rank, "temp": temp,
        "dd": round(last["dd"] * 100, 2), "trading_day_of_month": len(month_idx),
        "backtest": backtest(rows),
        "bars": [{"date": r["date"], "open": round(r["adj_open"], 2), "high": round(r["adj_high"], 2),
                  "low": round(r["adj_low"], 2), "close": round(r["adj_close"], 2),
                  "volume": int((r["volume"] or 0) / 1000)} for r in rows[-61:]],
    }


if __name__ == "__main__":
    import sys
    hist = load_history()
    print(f"{len(hist)} 個交易日：{hist[0]['date']} ～ {hist[-1]['date']}", file=sys.stderr)
    splits = [(r["date"], round(r["close"] - r["change"], 2)) for i, r in enumerate(hist[1:], 1)
              if r["change"] is not None and (r["close"] - r["change"]) / hist[i - 1]["close"] < 0.9]
    print("分割：", splits, file=sys.stderr)
