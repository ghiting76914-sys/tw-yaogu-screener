"""實際績效追蹤：用每天存下的名單（snapshots.py）與之後的實際股價，計算「照建議操作」的真實結果。

  明日精選 3 檔  選出隔天開盤買進 → 第 20 個交易日收盤賣出；盤中跌破進場價 -10% 就停損
  隔日沖        12:30 名單當時的價格買進 → 隔天開盤賣出
  營收動能      換股隔天開盤買進 → 第 20 個交易日收盤賣出

已出場的交易扣一買一賣成本；還在持有的只顯示目前漲跌。只計算真正存檔的名單（不含「重新計算」補上的）。
"""
import glob
import json
import os

import snapshots
import strategy

COST = strategy.ROUND_TRIP_COST


def _snaps():
    out = []
    for f in sorted(glob.glob(os.path.join(snapshots.SNAP_DIR, "*.json"))):
        with open(f, encoding="utf-8") as fh:
            out.append(json.load(fh))
    return out


def _summary(trades):
    closed = [x["報酬%"] for x in trades if x["狀態"] == "已出場" and x["報酬%"] is not None]
    opened = [x["報酬%"] for x in trades if x["狀態"] == "持有中" and x["報酬%"] is not None]
    return {
        "total": len(trades), "closed": len(closed), "open": len(opened),
        "win": round(sum(r > 0 for r in closed) / len(closed) * 100, 1) if closed else None,
        "avg": round(sum(closed) / len(closed), 2) if closed else None,
        "open_avg": round(sum(opened) / len(opened), 2) if opened else None,
    }


def compute(history):
    dates = [d.isoformat() for d, _ in history]
    idx = {d: i for i, d in enumerate(dates)}
    n = len(history)

    def bar(i, code):
        r = history[i][1].get(code) if 0 <= i < n else None
        return r if r and r["close"] is not None and r["open"] is not None else None

    def hold_trade(sig_date, code, name, hold, stop=None):
        """隔天開盤買、持有 hold 天；stop 為停損比例（例如 0.10）。"""
        i = idx.get(sig_date)
        if i is None or i + 1 >= n:
            return {"日期": sig_date, "代號": code, "名稱": name, "進場": None, "出場": None,
                    "報酬%": None, "狀態": "等待進場", "天數": 0}
        b = bar(i + 1, code)
        if not b:
            return None
        entry = b["open"]
        series = [history[j][1].get(code) for j in range(n)]
        if stop:
            r = strategy.hold_return_stop(series, i, n, hold, stop)
            if not r:
                return None
            ret, closed, days = r
            return {"日期": sig_date, "代號": code, "名稱": name, "進場": entry,
                    "出場": round(entry * (1 + ret / 100), 2), "報酬%": round(ret - (COST if closed else 0), 2),
                    "狀態": "已出場" if closed else "持有中", "天數": days}
        last_i = min(i + hold, n - 1)
        bars = strategy.held_bars(series, i + 1, n, last_i - i)
        if not bars:
            return None
        value = bars[-1][1]["close"]
        closed = i + hold <= n - 1
        ret = (value / entry - 1) * 100 - (COST if closed else 0)
        return {"日期": sig_date, "代號": code, "名稱": name, "進場": entry, "出場": bars[-1][1]["close"],
                "報酬%": round(ret, 2), "狀態": "已出場" if closed else "持有中", "天數": len(bars)}

    top3, overnight, overnight_limit, rev = [], [], [], []
    for s in _snaps():
        d = s["trade_date"]
        if s["mode"] == "final":
            for p in s.get("top3") or []:
                t = hold_trade(d, p["代號"], p["名稱"], 20, stop=0.10)
                if t:
                    top3.append(t)
            for p in s.get("revenue") or []:
                t = hold_trade(d, p["代號"], p["名稱"], 20)
                if t:
                    rev.append(t)
        if s["mode"] == "preview":
            i = idx.get(d)
            for p in s.get("overnight") or []:
                b = bar(i + 1, p["代號"]) if i is not None else None
                sell = b["open"] if b else None
                overnight.append({"日期": d, "代號": p["代號"], "名稱": p["名稱"], "進場": p["價格"], "出場": sell,
                                  "報酬%": round((sell / p["價格"] - 1) * 100 - COST, 2) if sell and p["價格"] else None,
                                  "狀態": "已出場" if sell else "等待隔天開盤", "天數": 1 if sell else 0})
                # 另一種賣法：隔天掛 +2% 限價，沒成交收盤賣
                lim = strategy.limit_sell_fill(p["價格"], b["open"], b["high"], b["close"]) \
                    if b and p["價格"] and b.get("high") is not None else None
                overnight_limit.append({"日期": d, "代號": p["代號"], "名稱": p["名稱"], "進場": p["價格"], "出場": lim,
                                        "報酬%": round((lim / p["價格"] - 1) * 100 - COST, 2) if lim else None,
                                        "狀態": "已出場" if lim else "等待隔天", "天數": 1 if lim else 0})
    out = {}
    for key, trades in (("top3", top3), ("overnight", overnight), ("overnight_limit", overnight_limit), ("revenue", rev)):
        trades.sort(key=lambda x: (x["日期"], x["代號"]), reverse=True)
        out[key] = {"summary": _summary(trades), "trades": trades}
    out["since"] = min((s["trade_date"] for s in _snaps()), default=None)
    return out
