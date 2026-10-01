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

# 操作計畫（回測比較過 1R～3R 停利、持有 3～10 日；持有越久越差，停利倍數影響不大）
TP1_R = 1.5     # 第一目標：風險的 1.5 倍，先賣一半，剩下的停損移到成本價
TP2_R = 3.0     # 第二目標：風險的 3 倍，全部出場
MAX_HOLD = 3    # 最多持有交易日數，期滿收盤出場

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


def levels(f):
    """進場區間、停損、兩段停利價位（以今日收盤為基準，皆為有效升降單位）。"""
    close = f["close"]
    stop = stop_price(f)
    risk = close - stop
    entry_high = tick_floor(close * 1.03)
    # 理想買點：回測突破點附近，但不低於收盤 -3%，也要明顯高於停損
    entry_low = tick_floor(max(f["prior_high"], close * 0.97, stop * 1.01))
    return {
        "entry_low": min(entry_low, close), "entry_high": entry_high, "stop": stop,
        "tp1": tick_floor(close + TP1_R * risk), "tp2": tick_floor(close + TP2_R * risk),
    }


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

    lv = levels(f)
    pct = lambda x: (x / f["close"] - 1) * 100
    plan = {
        "levels": lv,
        "entry": f"明日開盤落在 {lv['entry_low']:g}～{lv['entry_high']:g} 之間才進場；開太高不追、開太低代表轉弱不買",
        "stop": f"盤中跌破 {lv['stop']:g}（{pct(lv['stop']):+.1f}%）立即停損，代表突破失敗",
        "tp1": f"漲到 {lv['tp1']:g}（{pct(lv['tp1']):+.1f}%）先賣一半，剩下的停損移到成本價",
        "tp2": f"漲到 {lv['tp2']:g}（{pct(lv['tp2']):+.1f}%）全部出場",
        "exit": f"最多持有 {MAX_HOLD} 個交易日，期滿沒到目標就收盤出場",
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


def simulate(s, t, f, n):
    """照操作計畫模擬一筆交易，回傳扣成本後報酬 %；隔天開盤不在進場區間則回傳 None。
    同一天同時碰到停損與目標時，保守假設先碰到停損。"""
    lv = levels(f)
    o = s[t + 1]["open"]
    if not (lv["entry_low"] <= o <= lv["entry_high"]):
        return None
    entry, stop = o, lv["stop"]
    targets = [lv["tp1"], lv["tp2"]]
    pos, pnl, last_close = 1.0, 0.0, None
    for d in range(t + 1, min(t + 1 + MAX_HOLD, n)):
        x = s[d]
        if not x or x["close"] is None:
            continue
        gap_open = d > t + 1  # 進場日以開盤價買進，之後的日子可能跳空越過價位
        if x["low"] <= stop:
            px = min(x["open"], stop) if gap_open else stop
            pnl += pos * (px / entry - 1)
            pos = 0
            break
        while targets and x["high"] >= targets[0]:
            px = max(x["open"], targets[0]) if gap_open else targets[0]
            part = 0.5 if len(targets) == 2 else pos
            pnl += part * (px / entry - 1)
            pos -= part
            targets.pop(0)
            stop = max(stop, entry)  # 達第一目標後停損移到成本價
        if pos <= 0:
            break
        last_close = x["close"]
    if pos > 0:
        if last_close is None:
            return None
        pnl += pos * (last_close / entry - 1)
    return pnl * 100 - ROUND_TRIP_COST


def backtest(history, opt=DEFAULTS):
    """用同一套條件與操作計畫回測（見 simulate）。報酬已扣交易成本。
    另附「隔日開盤買、持有 1 日／3 日收盤賣」作為對照。"""
    series = build_series(history)
    n = len(history)
    r1, r3, rp = [], [], []
    signal_days, skipped = set(), 0
    for t in range(20, n - 1):
        for s in series.values():
            f = features(s, t)
            if not f or not passes(f, opt):
                continue
            nxt = s[t + 1]
            if not nxt or not nxt["open"] or nxt["close"] is None or t + MAX_HOLD >= n:
                continue
            signal_days.add(t)
            entry = nxt["open"]
            r1.append((nxt["close"] / entry - 1) * 100 - ROUND_TRIP_COST)
            if s[t + 3] and s[t + 3]["close"] is not None:
                r3.append((s[t + 3]["close"] / entry - 1) * 100 - ROUND_TRIP_COST)
            r = simulate(s, t, f, n)
            if r is None:
                skipped += 1  # 開盤不在進場區間，不買
            else:
                rp.append(r)

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
        "plan": stats(rp),
        "hold1": stats(r1),
        "hold3": stats(r3),
        "cost": ROUND_TRIP_COST,
    }


# ================================================================ 起漲前夕
"""起漲前夕：還在箱型整理、量縮、均線糾結，股價貼近箱頂、今天溫和放量收紅的股票。
進場要等「突破箱頂」確認，5 個交易日內沒突破就放棄。

選股條件（全部符合才入選）：
  1. 近 20 日高低差 ≤ 15%（箱型整理）
  2. 收盤在箱頂 -3% ～ +1% 之間
  3. 5、10、20 日均線最大差距 ≤ 3%（均線糾結），收盤站上 20 日均線且 20 日均線不下彎
  4. 近 20 日均量 ≤ 近 60 日均量（量縮，賣壓減輕）
  5. 今日量比 1.2～3 倍、上漲 0～4% 且收紅 K（溫和放量，尚未發動）
  6. 近 20 日均量 ≥ 300 張、今日成交量 ≥ 500 張；排除注意股、處置股
"""

PRE = {
    "box_days": 20, "max_box": 15.0, "near_low": 0.97, "near_high": 1.01,
    "max_spread": 3.0, "vr_low": 1.2, "vr_high": 3.0, "max_pct": 4.0,
    "min_avg_lots": 300, "min_lots": 500,
    "trigger_days": 5,   # 訊號後幾天內要突破
    "max_hold": 10,      # 突破進場後最多持有天數
}


def tick_size(price):
    for limit, tick in ((10, 0.01), (50, 0.05), (100, 0.1), (500, 0.5), (1000, 1)):
        if price < limit:
            return tick
    return 5


def tick_up(price):
    """比 price 高一檔的有效價格（突破買進價）。"""
    tick = tick_size(price)
    return round((int(round(price / tick, 6)) + 1) * tick, 2)


def pre_features(s, t):
    r = s[t]
    if r is None or None in (r["close"], r["high"], r["low"], r["open"]):
        return None
    past = [x for x in s[max(0, t - 60):t] if x and x["close"] is not None and x["high"] is not None]
    if len(past) < 60:
        return None
    box = past[-PRE["box_days"]:]
    closes = [x["close"] for x in past] + [r["close"]]
    ma = lambda k, end=None: sum((closes[:end] if end else closes)[-k:]) / k
    ma5, ma10, ma20 = ma(5), ma(10), ma(20)
    v20 = sum(x["volume"] for x in past[-20:]) / 20
    v60 = sum(x["volume"] for x in past) / 60
    box_high = max(x["high"] for x in box)
    box_low = min(x["low"] for x in box)
    return {
        "close": r["close"], "open": r["open"], "pct": pct_change(r), "volume": r["volume"],
        "box_high": box_high, "box_low": box_low, "box": (box_high / box_low - 1) * 100,
        "ma5": ma5, "ma10": ma10, "ma20": ma20, "ma20_prev": ma(20, -1),
        "spread": (max(ma5, ma10, ma20) / min(ma5, ma10, ma20) - 1) * 100,
        "v20": v20, "v60": v60, "dry": v20 / v60 if v60 else None,
        "vr": r["volume"] / v20 if v20 else None,
    }


def pre_passes(f):
    return (
        f["box"] <= PRE["max_box"]
        and f["box_high"] * PRE["near_low"] <= f["close"] <= f["box_high"] * PRE["near_high"]
        and f["spread"] <= PRE["max_spread"]
        and f["close"] > f["ma20"] and f["ma20"] >= f["ma20_prev"] * 0.995
        and f["dry"] is not None and f["dry"] <= 1
        and f["vr"] is not None and PRE["vr_low"] <= f["vr"] <= PRE["vr_high"]
        and f["pct"] is not None and 0 < f["pct"] <= PRE["max_pct"] and f["close"] >= f["open"]
        and f["v20"] / 1000 >= PRE["min_avg_lots"] and f["volume"] / 1000 >= PRE["min_lots"]
    )


def pre_levels(f):
    trigger = tick_up(f["box_high"])
    stop = tick_floor(max(f["box_low"], trigger * 0.93))
    target = tick_floor(trigger + (f["box_high"] - f["box_low"]))
    return {"trigger": trigger, "stop": stop, "target": target}


def pre_strength(f):
    """0～100：箱型越窄、量縮越明顯、均線越糾結、越貼近箱頂，分數越高。"""
    score = (1 - f["box"] / PRE["max_box"]) * 30
    score += (1 - min(f["dry"], 1)) * 30
    score += (1 - f["spread"] / PRE["max_spread"]) * 20
    score += max(0, 1 - abs(f["close"] / f["box_high"] - 1) / 0.03) * 20
    return round(score)


def pre_explain(f, cap_e, small_cap):
    lv = pre_levels(f)
    dist = (f["box_high"] / f["close"] - 1) * 100
    reasons = [
        f"近 20 日在 {f['box_low']:g}～{f['box_high']:g} 箱型整理，高低差只有 {f['box']:.1f}%",
        f"近 20 日均量只有 60 日均量的 {f['dry'] * 100:.0f}%，籌碼沉澱、賣壓減輕",
        f"5、10、20 日均線差距僅 {f['spread']:.1f}%，均線糾結，即將選擇方向",
        f"收盤已站上箱頂 {f['box_high']:g}" if dist <= 0
        else f"收盤 {f['close']:g} 距離箱頂 {f['box_high']:g} 只差 {dist:.1f}%",
        f"今日上漲 {f['pct']:.1f}%、量比 {f['vr']:.1f} 倍，溫和放量收紅，尚未大漲",
        f"站上 20 日均線，且 20 日均線{'上揚' if f['ma20'] > f['ma20_prev'] else '走平'}",
    ]
    if cap_e is not None and cap_e < small_cap:
        reasons.append(f"股本僅 {cap_e:.1f} 億，突破後容易有表現")

    stop_pct = (lv["stop"] / lv["trigger"] - 1) * 100
    risks = ["還沒突破，可能繼續整理，甚至跌破箱底轉弱"]
    if stop_pct < -6:
        risks.append(f"箱型較寬，突破進場後停損距離較大（{stop_pct:.1f}%）")
    if f["dry"] > 0.8:
        risks.append("量縮不夠明顯，整理可能還沒結束")
    if f["vr"] >= 2.5:
        risks.append("今日量已放大，若隔天無法續攻容易變成假突破")

    rel = lambda x: (x / lv["trigger"] - 1) * 100
    plan = {
        "levels": lv,
        "entry": f"股價漲過 {lv['trigger']:g}（突破箱頂）才進場，可預先設觸價單；{PRE['trigger_days']} 個交易日內沒突破就放棄",
        "stop": f"跌破 {lv['stop']:g}（相對突破價 {rel(lv['stop']):+.1f}%）停損",
        "target": f"漲到 {lv['target']:g}（相對突破價 {rel(lv['target']):+.1f}%，箱頂 + 箱型高度）出場",
        "exit": f"突破進場後最多持有 {PRE['max_hold']} 個交易日",
    }
    return reasons, risks, plan


def pre_picks(history, ref, small_cap=10):
    series = build_series(history)
    t = len(history) - 1
    out = []
    for code, s in series.items():
        if code in ref["disposal"] or code in ref["attention"]:
            continue
        f = pre_features(s, t)
        if not f or not pre_passes(f):
            continue
        cap = ref["capital"].get(code)
        if cap is None and s[t].get("shares"):
            cap = s[t]["shares"] * 10
        cap_e = cap / 1e8 if cap else None
        reasons, risks, plan = pre_explain(f, cap_e, small_cap)
        out.append({
            "代號": code, "名稱": s[t]["name"], "市場": s[t]["market"],
            "收盤": f["close"], "漲跌%": round(f["pct"], 2), "量比": round(f["vr"], 2),
            "成交張數": int(f["volume"] / 1000),
            "股本(億)": round(cap_e, 2) if cap_e is not None else None,
            "箱頂": f["box_high"], "箱底": f["box_low"],
            "強度": pre_strength(f), "理由": reasons, "風險": risks, "計畫": plan,
        })
    out.sort(key=lambda p: p["強度"], reverse=True)
    return out


def pre_simulate(s, t, f, n):
    """突破箱頂才進場；回傳扣成本後報酬 %，5 日內沒突破回傳 None。
    同一天同時碰到停損與目標時，保守假設先碰到停損。"""
    lv = pre_levels(f)
    entry = entry_day = None
    for d in range(t + 1, min(t + 1 + PRE["trigger_days"], n)):
        x = s[d]
        if x and x["high"] is not None and x["high"] >= lv["trigger"]:
            entry, entry_day = max(x["open"] or lv["trigger"], lv["trigger"]), d
            break
    if entry is None:
        return None
    stop = max(lv["stop"], entry * 0.93)
    target = entry + (f["box_high"] - f["box_low"])
    last = None
    for d in range(entry_day, min(entry_day + PRE["max_hold"], n)):
        x = s[d]
        if not x or x["close"] is None:
            continue
        later = d > entry_day
        if x["low"] <= stop:
            px = min(x["open"], stop) if later else stop
            return (px / entry - 1) * 100 - ROUND_TRIP_COST
        if x["high"] >= target:
            px = max(x["open"], target) if later else target
            return (px / entry - 1) * 100 - ROUND_TRIP_COST
        last = x["close"]
    return (last / entry - 1) * 100 - ROUND_TRIP_COST if last else None


def _stats(rs):
    if not rs:
        return None
    return {"trades": len(rs), "avg": round(sum(rs) / len(rs), 2),
            "win": round(sum(1 for x in rs if x > 0) / len(rs) * 100, 1),
            "best": round(max(rs), 1), "worst": round(min(rs), 1)}


def pre_backtest(history):
    """回測起漲前夕規則，並以「同期間所有股票隔日開盤買、持有 10 日」作為對照。"""
    series = build_series(history)
    n = len(history)
    hold = PRE["max_hold"]
    last_t = n - PRE["trigger_days"] - hold - 1  # 確保每筆訊號都有完整的觀察與持有期
    rs, base, signals, no_break = [], [], 0, 0
    for t in range(60, last_t + 1):
        for s in series.values():
            r = s[t]
            if r is None or r["volume"] < 500_000:
                continue
            nxt, end = s[t + 1], s[t + hold]
            if nxt and nxt["open"] and end and end["close"] is not None:
                ret = (end["close"] / nxt["open"] - 1) * 100
                # 10 日內受漲跌幅限制不可能超過這個範圍，超出代表減資、分割等價格斷層
                if -66 < ret < 160:
                    base.append(ret - ROUND_TRIP_COST)
            f = pre_features(s, t)
            if not f or not pre_passes(f):
                continue
            signals += 1
            res = pre_simulate(s, t, f, n)
            if res is None:
                no_break += 1
            else:
                rs.append(res)
    return {
        "from": history[60][0].isoformat() if last_t >= 60 else None,
        "to": history[last_t][0].isoformat() if last_t >= 60 else None,
        "signals": signals, "no_break": no_break,
        "plan": _stats(rs), "baseline": _stats(base), "cost": ROUND_TRIP_COST,
    }
