"""實際績效追蹤：用每天存下的名單（snapshots.py）與之後的實際股價，計算「照建議操作」的真實結果。

  明日精選 3 檔  選出當天收盤價買進（盤後定價）→ 第 20 個交易日收盤賣出；盤中跌破進場價 -10% 就停損
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
MAX_TRADES = 200  # 網站只附最近 200 筆明細；統計仍用全部交易
TOP3_CLOSE_FROM = "2026-10-13"  # 從這天起精選 3 檔改為當天收盤價買（13:40 推播、盤後定價）；之前照當時建議的隔天開盤買


LEDGER = os.path.join(snapshots.SNAP_DIR, "perf_ledger.json")


def _snaps():
    """每日名單存檔（檔名 YYYY-MM-DD_mode.json；不含已出場帳本）。"""
    out = []
    for f in sorted(glob.glob(os.path.join(snapshots.SNAP_DIR, "20*_*.json"))):
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


def compute(history, today_final=True):
    """today_final=False：最後一天是盤中即時資料（還沒收盤），以收盤價出場的交易先標「盤中（未收盤）」不計入統計。"""
    dates = [d.isoformat() for d, _ in history]
    idx = {d: i for i, d in enumerate(dates)}
    n = len(history)

    def bar(i, code):
        r = history[i][1].get(code) if 0 <= i < n else None
        return r if r and r["close"] is not None and r["open"] is not None else None

    def hold_trade(sig_date, code, name, hold, stop=None, at_close=False):
        """隔天開盤買（at_close：當天收盤價買）、持有 hold 天；stop 為停損比例（例如 0.10）。"""
        i = idx.get(sig_date)
        if at_close:
            b0 = bar(i, code) if i is not None else None
            if not b0:
                return None
            series = [history[j][1].get(code) for j in range(n)]
            r = strategy.hold_return_stop(series, i, n, hold, stop, at_close=True)
            if not r:
                return None
            ret, closed, days = r
            entry = b0["close"]
            return {"日期": sig_date, "代號": code, "名稱": name, "進場": entry,
                    "出場": round(entry * (1 + ret / 100), 2), "報酬%": round(ret - (COST if closed else 0), 2),
                    "狀態": "已出場" if closed else "持有中", "天數": days}
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

    top3, overnight, overnight_limit, rev, daytrade, overnight_rec = [], [], [], [], [], []
    for s in _snaps():
        d = s["trade_date"]
        if d < dates[0]:
            continue  # 比行情資料還舊的名單算不出來，結果來自已出場帳本
        if s["mode"] == "final":
            for p in s.get("top3") or []:
                t = hold_trade(d, p["代號"], p["名稱"], 20, stop=0.10, at_close=d >= TOP3_CLOSE_FROM)
                if t:
                    top3.append(t)
            # 當沖觀察：隔天依開盤方向，開盤進、收盤出（停損 5%）；沒有跳空 3% 的不交易
            i = idx.get(d)
            for p in s.get("daytrade") or []:
                b = bar(i + 1, p["代號"]) if i is not None else None
                if b is None:
                    if i is not None and i + 1 >= n:
                        daytrade.append({"日期": d, "代號": p["代號"], "名稱": p["名稱"], "進場": None, "出場": None,
                                         "報酬%": None, "狀態": "等待明天開盤", "天數": 0})
                    continue
                side, ret = strategy.daytrade_result(b["open"], b["high"], b["low"], b["close"], p["價格"])
                if side and not today_final and i + 1 == n - 1:
                    # 今天還沒收盤：已經碰到停損的算出場，其餘標盤中、顯示目前損益但不計入統計
                    stopped = ret <= -strategy.DAYTRADE["stop"] - strategy.DAYTRADE["cost"] + 1e-9
                    if not stopped:
                        cur = (b["open"] / b["close"] - 1) * 100 if side == "做空" else (b["close"] / b["open"] - 1) * 100
                        daytrade.append({"日期": history[i + 1][0].isoformat(), "代號": p["代號"], "名稱": f"{p['名稱']}（{side}）",
                                         "進場": b["open"], "出場": b["close"], "報酬%": round(cur, 2), "狀態": "持有中", "天數": 0})
                        continue
                if side:
                    daytrade.append({"日期": history[i + 1][0].isoformat(), "代號": p["代號"], "名稱": f"{p['名稱']}（{side}）",
                                     "進場": b["open"], "出場": b["close"], "報酬%": ret, "狀態": "已出場", "天數": 1})
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
                if p.get("推薦"):  # 2026-10-14 起存檔才有這個標記
                    overnight_rec.append(dict(overnight[-1]))
                # 另一種賣法：隔天掛 +2% 限價，沒成交收盤賣
                lim = strategy.limit_sell_fill(p["價格"], b["open"], b["high"], b["close"]) \
                    if b and p["價格"] and b.get("high") is not None else None
                # 盤中還沒碰到限價：要等收盤才知道出場價
                pending = lim is not None and not today_final and i + 1 == n - 1 \
                    and lim < strategy.limit_sell_price(p["價格"]) and b["open"] < strategy.limit_sell_price(p["價格"])
                overnight_limit.append({"日期": d, "代號": p["代號"], "名稱": p["名稱"], "進場": p["價格"],
                                        "出場": None if pending else lim,
                                        "報酬%": None if pending or not lim else round((lim / p["價格"] - 1) * 100 - COST, 2),
                                        "狀態": "盤中（未收盤）" if pending else "已出場" if lim else "等待隔天", "天數": 1 if lim else 0})
    # 已出場帳本：出場的交易結果永久記下；行情只保留約 2 年（LOOKBACK），更早的名單算不出來時改用帳本
    try:
        with open(LEDGER, encoding="utf-8") as fh:
            ledger = json.load(fh)
    except (OSError, ValueError):
        ledger = {}
    out = {}
    for key, trades in (("top3", top3), ("overnight", overnight), ("overnight_limit", overnight_limit), ("overnight_rec", overnight_rec),
                        ("revenue", rev), ("daytrade", daytrade)):
        book = ledger.setdefault(key, {})
        tkey = lambda x: f"{x['日期']}|{x['代號']}|{x['名稱']}"  # noqa: E731
        seen = {tkey(x) for x in trades}
        for x in trades:
            if x["狀態"] == "已出場":
                book[tkey(x)] = x
        trades += [x for k, x in book.items() if k not in seen]
        trades.sort(key=lambda x: (x["日期"], x["代號"]), reverse=True)
        out[key] = {"summary": _summary(trades), "trades": trades[:MAX_TRADES]}
    try:
        with open(LEDGER, "w", encoding="utf-8") as fh:
            json.dump(ledger, fh, ensure_ascii=False)
    except OSError as e:
        print(f"已出場帳本寫入失敗：{e}")
    out["since"] = min((s["trade_date"] for s in _snaps()), default=None)
    return out
