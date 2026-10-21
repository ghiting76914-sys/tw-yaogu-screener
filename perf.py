"""實際績效追蹤：用每天存下的名單（snapshots.py）與之後的實際股價，計算「照建議操作」的真實結果。

  明日精選 3 檔  選出當天收盤價買進（盤後定價）→ 第 20 個交易日收盤賣出；盤中跌破進場價 -10% 就停損
  隔日沖        12:30 名單當時的價格買進 → 隔天開盤賣出
  營收動能      換股隔天開盤買進 → 第 20 個交易日收盤賣出

已出場的交易扣一買一賣成本；還在持有的只顯示目前漲跌。只計算真正存檔的名單（不含「重新計算」補上的）。
"""
import glob
import json
import os

import bigorders
import snapshots
import strategy

COST = strategy.ROUND_TRIP_COST
MAX_TRADES = 200  # 網站只附最近 200 筆明細；統計仍用全部交易
TOP3_CLOSE_FROM = "2026-10-13"
GAP_FROM = "2026-10-16"  # 當沖「開盤跳空 ≥5% 放空」從這天起上線  # 從這天起精選 3 檔改為當天收盤價買（13:40 推播、盤後定價）；之前照當時建議的隔天開盤買


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


def _by_big(trades):
    """當沖交易依大單買賣比分組（bigorders.py 記錄，2026-10-16 起）。"""
    out = {}
    for x in trades:
        g = x.get("大單")
        if g and x["狀態"] == "已出場" and x["報酬%"] is not None:
            out.setdefault(g, []).append(x["報酬%"])
    return {g: {"n": len(v), "avg": round(sum(v) / len(v), 2), "win": round(sum(r > 0 for r in v) / len(v) * 100, 1)}
            for g, v in out.items()}


def daytrade_codes(history):
    """今天（最後一天）有交易的當沖股票：前一天鎖漲停且開盤跳空 ≥3%（開盤漲停不空），以及開盤跳空 ≥5% 的放空。"""
    res = compute(history, today_final=True, big=False)
    day = history[-1][0].isoformat()
    return [x["代號"] for k in ("daytrade", "daytrade_gap") for x in res[k]["trades"] if x["日期"] == day]


def compute(history, today_final=True, big=True):
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

    top3, overnight, overnight_limit, rev, daytrade, overnight_rec, panic = [], [], [], [], [], [], []
    radar_star, day_gap = [], []
    # 精選 3 檔：同一檔還在持有中又再次入選時不重複買（2026-10-21 起的規則，套用到全部紀錄）
    top3_hold, top3_dup = {}, set()   # 代號 → (出場日索引或 None＝持有中, 第一次選出日)
    # 當沖：開盤跳空 ≥ 5% 放空（strategy.DAYTRADE_GAP）。名單完全由行情決定（前一天均量 ≥ 1000 張、股價 ≥ 10），直接用行情計算
    for i in range(max(21, idx.get(GAP_FROM, n)), n):
        for code in history[i][1]:
            b, p0 = bar(i, code), bar(i - 1, code)
            if not b or not p0 or len(code) != 4 or code[0] == "0" or not code.isdigit() or b["open"] is None \
                    or b["change"] is None or b["close"] - b["change"] <= 0:
                continue
            prev = b["close"] - b["change"]
            if prev < strategy.DAYTRADE["min_price"] or b["open"] / prev - 1 < strategy.DAYTRADE_GAP["gap"] / 100 - 1e-9:
                continue
            vols = [history[j][1].get(code) for j in range(i - 20, i)]
            if any(x is None or x["volume"] is None for x in vols) or sum(x["volume"] for x in vols) / 20 < strategy.DAYTRADE["min_lots"] * 1000:
                continue
            side, ret = strategy.daytrade_result(b["open"], b["high"], b["low"], b["close"], prev)
            if side != "做空":
                continue  # 開盤就漲停：不空
            stopped = ret <= -strategy.DAYTRADE["stop"] - strategy.DAYTRADE["cost"] + 1e-9
            live = not today_final and i == n - 1 and not stopped
            day_gap.append({"日期": history[i][0].isoformat(), "代號": code, "名稱": f"{b['name']}（開高 {(b['open'] / prev - 1) * 100:.1f}%）",
                            "進場": b["open"], "出場": b["close"],
                            "報酬%": round((b["open"] / b["close"] - 1) * 100, 2) if live else ret,
                            "狀態": "持有中" if live else "已出場", "天數": 0 if live else 1})
    for s in _snaps():
        d = s["trade_date"]
        if d < dates[0]:
            continue  # 比行情資料還舊的名單算不出來，結果來自已出場帳本
        if s["mode"] == "final":
            for p in s.get("top3") or []:
                t = hold_trade(d, p["代號"], p["名稱"], 20, stop=0.10, at_close=d >= TOP3_CLOSE_FROM)
                if not t:
                    continue
                i = idx.get(d)
                held = top3_hold.get(p["代號"])
                if held and (held[0] is None or held[0] > i):
                    top3_dup.add(f"{d}|{p['代號']}|{p['名稱']}")
                    continue
                if t["狀態"] == "已出場" and t["天數"] < 20:
                    # 停損出場的：繼續追蹤「如果沒停損、抱滿 20 天」會怎樣（只供比較，不計入統計）
                    alt = hold_trade(d, p["代號"], p["名稱"], 20, stop=0.999, at_close=d >= TOP3_CLOSE_FROM)
                    if alt:
                        t["不停損%"], t["不停損天數"] = alt["報酬%"], alt["天數"]
                        t["不停損狀態"] = "已滿 20 天" if alt["狀態"] == "已出場" else "追蹤中"
                top3.append(t)
                top3_hold[p["代號"]] = (i + t["天數"] if t["狀態"] == "已出場" else None, d)
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
            for p in s.get("radar_star") or []:  # 妖股雷達 ⭐：隔天開盤買、持有 20 天、停損 -10%
                t = hold_trade(d, p["代號"], p["名稱"], strategy.RADAR_STAR["hold"], stop=strategy.RADAR_STAR["stop"] / 100)
                if t:
                    radar_star.append(t)
            for p in s.get("panic") or []:  # 股災反彈：隔天開盤買、持有 5 天
                t = hold_trade(d, p["代號"], p["名稱"], strategy.PANIC["hold"])
                if t:
                    panic.append(t)
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
    if big:  # 當沖交易標上當天的大單買賣比分組
        cache = {}
        for x in daytrade + day_gap:
            if x["日期"] not in cache:
                cache[x["日期"]] = bigorders.load(x["日期"])
            g = bigorders.bucket(cache[x["日期"]].get(x["代號"]))
            if g:
                x["大單"] = g
    out = {}
    for key, trades in (("top3", top3), ("overnight", overnight), ("overnight_limit", overnight_limit), ("overnight_rec", overnight_rec),
                        ("revenue", rev), ("daytrade", daytrade), ("panic", panic),
                        ("radar_star", radar_star), ("daytrade_gap", day_gap)):
        book = ledger.setdefault(key, {})
        if key == "top3":
            for k in top3_dup:
                book.pop(k, None)  # 規則改成不重複買之前記下的重複交易
        tkey = lambda x: f"{x['日期']}|{x['代號']}|{x['名稱']}"  # noqa: E731
        seen = {tkey(x) for x in trades}
        for x in trades:
            if x["狀態"] == "已出場":
                book[tkey(x)] = x
        trades += [x for k, x in book.items() if k not in seen]
        trades.sort(key=lambda x: (x["日期"], x["代號"]), reverse=True)
        out[key] = {"summary": _summary(trades), "trades": trades[:MAX_TRADES]}
        if key == "top3":
            alt = [x for x in trades if x.get("不停損%") is not None]
            if alt:
                out[key]["no_stop"] = {"n": len(alt), "done": sum(x["不停損狀態"] == "已滿 20 天" for x in alt),
                                       "stop_avg": round(sum(x["報酬%"] for x in alt) / len(alt), 2),
                                       "avg": round(sum(x["不停損%"] for x in alt) / len(alt), 2),
                                       "better": sum(x["不停損%"] > x["報酬%"] for x in alt)}
        if key in ("daytrade", "daytrade_gap"):
            out[key]["by_big"] = _by_big(trades)
    try:
        with open(LEDGER, "w", encoding="utf-8") as fh:
            json.dump(ledger, fh, ensure_ascii=False)
    except OSError as e:
        print(f"已出場帳本寫入失敗：{e}")
    out["top3_holding"] = {c: h[1] for c, h in top3_hold.items() if h[0] is None}
    out["since"] = min((s["trade_date"] for s in _snaps()), default=None)
    return out
