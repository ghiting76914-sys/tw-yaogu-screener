"""每日名單存檔與「前一交易日名單」對照。

每次產生網站時，把當天各分頁的名單與當時價格存到 data/cache/snapshots/{交易日}_{final|preview}.json
（GitHub Actions 會連同行情快取一起保存）。隔天在每個分頁顯示前一交易日的名單，並用今天的價格對照表現：

  隔日沖        前一天 12:30 的價格買進 → 今天開盤賣出
  明日強勢候選  今天開盤是否在進場區間、開盤到收盤的漲跌
  起漲前夕      今天是否漲過突破價
  明日精選 3 檔 今天開盤買進 → 今天收盤

沒有存檔（例如第一天）時，用前一天的資料重新計算名單，並標示「重新計算」。
"""
import json
import os

import strategy
from yaogu import CACHE_DIR

SNAP_DIR = os.path.join(CACHE_DIR, "snapshots")
COST = strategy.ROUND_TRIP_COST


def _slim(lst, extra=()):
    return [{"代號": p["代號"], "名稱": p["名稱"], "價格": p.get("收盤", p.get("現價")),
             **{k: p[k] for k in extra if k in p}} for p in lst]


def save(data):
    """存下當天名單。盤中（12:30 隔日沖）與收盤後分開存。"""
    live = data.get("live")
    mode = "preview" if live and not live.get("final") else "final"
    rev = data.get("revenue") or {}
    snap = {
        "trade_date": data["trade_date"], "mode": mode, "time": (live or {}).get("time"),
        "overnight": _slim(data.get("overnight") or []),
        "picks": [{**s, "進場區間": [p["計畫"]["levels"]["entry_low"], p["計畫"]["levels"]["entry_high"]]}
                  for s, p in zip(_slim(data.get("picks") or []), data.get("picks") or [])],
        "pre": [{**s, "突破價": p["計畫"]["levels"]["trigger"]}
                for s, p in zip(_slim(data.get("pre") or []), data.get("pre") or [])],
        "top3": _slim((rev.get("top3") or {}).get("picks") or []),
        # 營收動能只在換股日存（其他天名單不變）
        "revenue": [{"代號": p["代號"], "名稱": p["名稱"], "價格": p["選股日收盤"]} for p in rev.get("picks") or []]
        if rev.get("rebalance_date") == data["trade_date"] else [],
    }
    os.makedirs(SNAP_DIR, exist_ok=True)
    path = os.path.join(SNAP_DIR, f"{data['trade_date']}_{mode}.json")
    if mode == "preview" and os.path.exists(path):
        return  # 盤中名單只保留當天第一次（12:30 推播的那份），之後的更新不覆蓋
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(snap, fh, ensure_ascii=False)


def pushed_overnight(date):
    """當天盤中推播（第一次盤中存檔）的隔日沖名單；實際績效以這份計算。"""
    snap = _load(date, "preview")
    return {"time": snap.get("time"), "list": snap.get("overnight") or []} if snap else None


def _load(date, mode):
    path = os.path.join(SNAP_DIR, f"{date}_{mode}.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return None


def _ret(buy, sell, cost=False):
    """cost=True：已完成的交易（隔日沖），扣一買一賣成本；還在持有的只算漲跌。"""
    return round((sell / buy - 1) * 100 - (COST if cost else 0), 2) if buy and sell else None


def previous(history, recompute):
    """前一交易日的名單與今天的表現。recompute(kind) 在沒有存檔時回傳用前一天資料重新計算的名單。"""
    if len(history) < 2:
        return None
    prev_date = history[-2][0].isoformat()
    today = history[-1][1]
    final = _load(prev_date, "final")
    preview = _load(prev_date, "preview")
    out = {"date": prev_date, "today": history[-1][0].isoformat(), "sources": {}}

    def lists(kind, snap):
        if snap and snap.get(kind) is not None:
            out["sources"][kind] = "存檔" + (f"（{snap['time'][:5]}）" if snap.get("time") else "")
            return snap[kind]
        out["sources"][kind] = "重新計算"
        return recompute(kind)

    def px(code):
        r = today.get(code)
        return (r["open"], r["close"], r["high"]) if r and r["close"] is not None else (None, None, None)

    # 隔日沖：前一天盤中名單（12:30）買進價 → 今天開盤賣
    rows = []
    for p in lists("overnight", preview or final):
        o, c, _ = px(p["代號"])
        rows.append({**p, "今開": o, "今收": c, "賣出報酬%": _ret(p["價格"], o, cost=True)})
    out["overnight"] = rows

    rows = []
    for p in lists("picks", final):
        o, c, _ = px(p["代號"])
        lo, hi = p.get("進場區間") or (None, None)
        status = None
        if o and lo:
            status = "可進場" if lo <= o <= hi else ("開太高，不追" if o > hi else "開太低，不買")
        rows.append({**p, "今開": o, "今收": c, "開盤狀態": status,
                     "今日報酬%": _ret(o, c) if status == "可進場" else None})
    out["picks"] = rows

    rows = []
    for p in lists("pre", final):
        o, c, h = px(p["代號"])
        trig = p.get("突破價")
        broke = bool(h and trig and h >= trig)
        rows.append({**p, "今收": c, "今高": h, "已突破": broke,
                     "突破後報酬%": _ret(max(o, trig), c) if broke and o else None})
    out["pre"] = rows

    rows = []
    for p in lists("top3", final):
        o, c, _ = px(p["代號"])
        rows.append({**p, "今開": o, "今收": c, "今日報酬%": _ret(o, c)})
    out["top3"] = rows
    return out
