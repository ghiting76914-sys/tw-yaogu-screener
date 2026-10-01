"""明日強勢選股：今天收盤後找「剛起漲、尚未過熱」的突破股，隔日觀察進場。

選股條件（全部符合才入選）：
  1. 今日漲幅 ≥ 4%
  2. 收盤價突破前 60 日（資料不足時取可用天數，至少 20 日）最高價
  3. 量比（今日量 ÷ 前 20 日均量）≥ 2
  4. 收盤位於當日高低區間的上方 10% 以內（幾乎收最高，買盤撐到收盤）
  5. 收盤站上 20 日均線，且 20 日均線不下彎
  6. 5 日累計漲幅 ≤ 20%、連續漲停 ≤ 2 天（避免追在噴出末段）
  7. 成交量 ≥ 1000 張（流動性）
  8. 非處置股
"""
from yaogu import is_common_stock, pct_change

# 台股一買一賣成本：手續費 0.1425% × 2 + 證交稅 0.3%
ROUND_TRIP_COST = 0.585

DEFAULTS = {
    "min_pct": 4.0,
    "min_vol_ratio": 2.0,
    "min_close_pos": 0.9,
    "max_r5": 20.0,
    "max_streak": 2,
    "min_lots": 1000,
}


def build_series(history):
    codes = [c for c in history[-1][1] if is_common_stock(c)]
    return {c: [day.get(c) for _, day in history] for c in codes}


def features(s, t):
    r = s[t]
    if r is None or None in (r["close"], r["high"], r["low"]):
        return None
    past = [x for x in s[max(0, t - 60):t] if x and x["close"] is not None and x["high"] is not None]
    if len(past) < 20:
        return None

    closes = [x["close"] for x in past] + [r["close"]]

    def ma(n, end=None):
        arr = closes[:end] if end else closes
        return sum(arr[-n:]) / n if len(arr) >= n else None

    streak = 0
    for x in reversed(s[:t + 1]):
        p = pct_change(x)
        if p is not None and p >= 9.5:
            streak += 1
        else:
            break

    vol20 = sum(x["volume"] for x in past[-20:]) / 20
    rng = r["high"] - r["low"]
    open_ = r["open"] or r["close"]
    return {
        "close": r["close"], "open": open_, "high": r["high"], "low": r["low"],
        "pct": pct_change(r),
        "volume": r["volume"],
        "vol_ratio": r["volume"] / vol20 if vol20 else None,
        "prior_high": max(x["high"] for x in past),
        "lookback": len(past),
        "close_pos": 1.0 if rng == 0 else (r["close"] - r["low"]) / rng,
        "upper_shadow": (r["high"] - max(open_, r["close"])) / r["close"] * 100,
        "ma5": ma(5), "ma10": ma(10), "ma20": ma(20),
        "ma20_prev": ma(20, -5),
        "r5": (r["close"] / closes[-6] - 1) * 100 if len(closes) >= 6 else None,
        "streak": streak,
        "one_price": rng == 0,
    }


def passes(f, opt=DEFAULTS):
    return (
        f["pct"] is not None and f["pct"] >= opt["min_pct"]
        and f["close"] >= f["prior_high"]
        and f["vol_ratio"] is not None and f["vol_ratio"] >= opt["min_vol_ratio"]
        and f["close_pos"] >= opt["min_close_pos"]
        and f["ma20"] is not None and f["close"] > f["ma20"]
        and (f["ma20_prev"] is None or f["ma20"] >= f["ma20_prev"])
        and f["r5"] is not None and f["r5"] <= opt["max_r5"]
        and f["streak"] <= opt["max_streak"]
        and f["volume"] / 1000 >= opt["min_lots"]
    )


def rank_score(f, cap_e, small_cap):
    """0～100 的強度分數，用來排序。"""
    score = min(f["vol_ratio"], 8) / 8 * 25
    score += f["close_pos"] * 20
    score += min(f["pct"], 10) / 10 * 20
    score += min((f["close"] / f["prior_high"] - 1) * 100, 10) / 10 * 10
    if f["ma5"] and f["ma10"] and f["ma5"] > f["ma10"] > f["ma20"]:
        score += 10
    if f["r5"] <= 15:
        score += 5
    if cap_e is not None and cap_e < small_cap:
        score += 10
    return round(min(score, 100))


def tick_floor(price):
    """依台股升降單位，取不高於 price 的有效價格。"""
    for limit, tick in ((10, 0.01), (50, 0.05), (100, 0.1), (500, 0.5), (1000, 1)):
        if price < limit:
            break
    else:
        tick = 5
    return round(int(round(price / tick, 6)) * tick, 2)


def stop_price(f):
    """停損價：訊號日最低價；若距離收盤超過 8%，改用收盤 -7%。"""
    if (f["close"] - f["low"]) / f["close"] > 0.08:
        return tick_floor(f["close"] * 0.93)
    return f["low"]


def explain(f, cap_e, small_cap, attention):
    lim = f["pct"] >= 9.5
    reasons = []
    head = "漲停" if lim else f"上漲 {f['pct']:.1f}%"
    if f["close"] >= f["high"]:
        reasons.append(f"今日{head}，收在最高價，買盤一路撐到收盤")
    else:
        reasons.append(f"今日{head}，收在接近最高價（當日區間 {f['close_pos'] * 100:.0f}% 位置），收盤前沒有明顯賣壓")
    reasons.append(f"成交量 {f['volume'] / 1000:,.0f} 張，是前 20 日均量的 {f['vol_ratio']:.1f} 倍，有資金明顯進場")
    reasons.append(f"收盤 {f['close']:g} 突破前 {f['lookback']} 日最高價 {f['prior_high']:g}，上方沒有近期套牢賣壓")
    if f["ma5"] and f["ma10"] and f["ma5"] > f["ma10"] > f["ma20"]:
        reasons.append("5、10、20 日均線多頭排列，短中期趨勢同步向上")
    else:
        reasons.append(f"股價站上 20 日均線（{f['ma20']:.2f}），且 20 日均線開始走平或上揚")
    if f["r5"] <= 15:
        reasons.append(f"5 日累計漲幅 {f['r5']:.1f}%，仍在起漲初期，尚未過熱")
    else:
        reasons.append(f"5 日累計漲幅 {f['r5']:.1f}%，漲勢已展開但還沒進入過熱區")
    if cap_e is not None and cap_e < small_cap:
        reasons.append(f"股本僅 {cap_e:.1f} 億，籌碼輕、股性活潑")

    risks = []
    if f["one_price"] and lim:
        risks.append("今日一字漲停，明日可能直接跳空開高，不易以理想價格買到")
    if f["vol_ratio"] >= 10:
        risks.append(f"爆量 {f['vol_ratio']:.0f} 倍，要留意是否有短線大戶趁機出貨")
    if f["upper_shadow"] >= 2:
        risks.append(f"上影線 {f['upper_shadow']:.1f}%，盤中高點有賣壓")
    if f["r5"] > 15:
        risks.append(f"短線已漲 {f['r5']:.0f}%，追價空間較小、回檔風險較高")
    if f["streak"] == 2:
        risks.append("已連續 2 根漲停，再漲停可能被列入注意股")
    if attention:
        risks.append("已被列為注意股，若持續異常可能被處置（分盤交易、流動性變差）")
    if not risks:
        risks.append("型態健康，但仍可能遇到大盤回檔或突破失敗")

    entry_max = tick_floor(f["close"] * 1.03)
    stop = stop_price(f)
    stop_pct = (stop / f["close"] - 1) * 100
    plan = {
        "entry": f"明日開盤不高於 {entry_max:g}（今日收盤 +3%）再考慮進場，跳空太高不追",
        "stop": f"收盤跌破 {stop:g}（約 {stop_pct:.1f}%）停損，代表突破失敗",
        "exit": "持有期間收盤跌破 5 日均線，或出現爆量長黑，分批獲利了結",
    }
    return reasons, risks, plan


def tomorrow_picks(history, ref, small_cap=10, opt=DEFAULTS):
    series = build_series(history)
    t = len(history) - 1
    picks = []
    for code, s in series.items():
        if code in ref["disposal"]:
            continue
        f = features(s, t)
        if not f or not passes(f, opt):
            continue
        cap = ref["capital"].get(code)
        if cap is None and s[t].get("shares"):
            cap = s[t]["shares"] * 10
        cap_e = cap / 1e8 if cap else None
        reasons, risks, plan = explain(f, cap_e, small_cap, code in ref["attention"])
        picks.append({
            "代號": code, "名稱": s[t]["name"], "市場": s[t]["market"],
            "收盤": f["close"], "漲跌%": round(f["pct"], 2),
            "量比": round(f["vol_ratio"], 2), "5日%": round(f["r5"], 1),
            "成交張數": int(f["volume"] / 1000),
            "股本(億)": round(cap_e, 2) if cap_e is not None else None,
            "強度": rank_score(f, cap_e, small_cap),
            "理由": reasons, "風險": risks, "計畫": plan,
        })
    picks.sort(key=lambda p: p["強度"], reverse=True)
    return picks


def backtest(history, opt=DEFAULTS):
    """用同一套條件回測：訊號日隔天開盤買進（跳空超過 +3% 不買），
    持有 3 日後收盤賣出，期間收盤跌破停損價則隔天開盤出場。報酬已扣交易成本。
    另附「持有 1 日」與「持有 3 日不設停損」作為對照。"""
    series = build_series(history)
    n = len(history)
    r1, r3, r3s = [], [], []
    signal_days, skipped = set(), 0
    for t in range(20, n - 1):
        for s in series.values():
            f = features(s, t)
            if not f or not passes(f, opt):
                continue
            nxt = s[t + 1]
            if not nxt or not nxt["open"] or nxt["close"] is None:
                continue
            entry = nxt["open"]
            if entry > f["close"] * 1.03:
                skipped += 1  # 跳空太高（含一字漲停）不追
                continue
            signal_days.add(t)
            r1.append((nxt["close"] / entry - 1) * 100 - ROUND_TRIP_COST)
            hold = s[t + 1:t + 4]
            if len(hold) < 3 or any(x is None or x["close"] is None for x in hold) or t + 4 > n:
                continue
            r3.append((hold[2]["close"] / entry - 1) * 100 - ROUND_TRIP_COST)
            stop, exit_ = stop_price(f), hold[2]["close"]
            for i, x in enumerate(hold):
                if x["close"] < stop:
                    nxt_day = s[t + 2 + i] if t + 2 + i < n else None
                    exit_ = nxt_day["open"] if i < 2 and nxt_day and nxt_day["open"] else x["close"]
                    break
            r3s.append((exit_ / entry - 1) * 100 - ROUND_TRIP_COST)

    def stats(rs):
        if not rs:
            return None
        return {"trades": len(rs), "avg": round(sum(rs) / len(rs), 2),
                "win": round(sum(1 for x in rs if x > 0) / len(rs) * 100, 1),
                "best": round(max(rs), 1), "worst": round(min(rs), 1)}

    return {
        "from": history[20][0].isoformat() if n > 20 else None,
        "to": history[-2][0].isoformat() if n > 1 else None,
        "signal_days": len(signal_days),
        "skipped_gap": skipped,
        "hold3_stop": stats(r3s),
        "hold1": stats(r1),
        "hold3": stats(r3),
        "cost": ROUND_TRIP_COST,
    }
