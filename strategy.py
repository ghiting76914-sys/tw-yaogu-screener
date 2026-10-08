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


_series_cache = {}


def build_series(history):
    """{代號: 與 history 對齊的每日行情 list}。同一份 history 會被多個功能使用，算一次就記住。"""
    key = (id(history), len(history), history[-1][0])
    if key not in _series_cache:
        _series_cache.clear()
        codes = [c for c in history[-1][1] if is_common_stock(c)]
        _series_cache[key] = {c: [day.get(c) for _, day in history] for c in codes}
    return _series_cache[key]


def _atr(bars, k=14):
    """平均真實波幅 ATR(k)：最近 k 天「最高－最低、與前一天收盤的跳空」取最大值後平均。"""
    trs = []
    for prev, x in zip(bars[-k - 1:-1], bars[-k:]):
        if None in (x["high"], x["low"], prev["close"]):
            continue
        trs.append(max(x["high"] - x["low"], abs(x["high"] - prev["close"]), abs(x["low"] - prev["close"])))
    return sum(trs) / len(trs) if len(trs) >= 10 else None


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
        "atr": _atr(past + [r]),
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


ATR_MULT = 1.5  # 停損距離 = 1.5 倍 ATR（依個股波動調整；2 年回測與舊規則績效相當）


def stop_price(f):
    """停損價：收盤 - 1.5 倍 ATR(14)，讓停損、停利依每檔股票平常的波動調整。
    沒有 ATR 時用舊規則：訊號日最低價；若距離收盤超過 8%，改用收盤 -7%。"""
    if f.get("atr"):
        return tick_floor(f["close"] - ATR_MULT * f["atr"])
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
        "stop": f"盤中跌破 {lv['stop']:g}（{pct(lv['stop']):+.1f}%，約 {ATR_MULT:g} 倍日均波動）立即停損，代表突破失敗"
                if f.get("atr") else f"盤中跌破 {lv['stop']:g}（{pct(lv['stop']):+.1f}%）立即停損，代表突破失敗",
        "tp1": f"漲到 {lv['tp1']:g}（{pct(lv['tp1']):+.1f}%）先賣一半，剩下的停損移到成本價",
        "tp2": f"漲到 {lv['tp2']:g}（{pct(lv['tp2']):+.1f}%）全部出場",
        "exit": f"最多持有 {MAX_HOLD} 個交易日，期滿沒到目標就收盤出場",
    }
    return reasons, risks, plan


def insti_selling(insti, code):
    """三大法人當天合計賣超。2 年研究中，法人賣超的訊號在訓練期與驗證期都明顯較差。"""
    iv = insti.get(code) if insti else None
    return bool(iv and iv[2] < 0)


STREAK_MAX = 20  # 連續上榜最多往回算幾天


def streak(s, t, ok, max_days=STREAK_MAX):
    """從第 t 天往回數，連續符合 ok(第幾天) 的天數（含第 t 天）。"""
    n = 0
    while n < max_days and t - n >= 0 and ok(t - n):
        n += 1
    return n


def tomorrow_picks(history, ref, small_cap=10, opt=DEFAULTS, insti=None, excluded=None, insti_hist=None):
    """insti：今天的三大法人買賣超（None 代表尚未公布，不排除）。
    excluded：傳入 list 時，會放入因法人賣超而排除的股票名稱。
    insti_hist：與 history 對齊的法人資料 list，用來計算過去每天是否也上榜。"""
    series = build_series(history)
    t = len(history) - 1
    picks = []
    for code, s in series.items():
        if code in ref["disposal"]:
            continue
        f = features(s, t)
        if not f or not passes(f, opt):
            continue
        if insti_selling(insti, code):
            if excluded is not None:
                excluded.append(f"{s[t]['name']} {code}")
            continue
        cap = ref["capital"].get(code)
        if cap is None and s[t].get("shares"):
            cap = s[t]["shares"] * 10
        cap_e = cap / 1e8 if cap else None
        reasons, risks, plan = explain(f, cap_e, small_cap, code in ref["attention"])
        iv = insti.get(code) if insti else None
        if iv and iv[2] > 0:
            reasons.append(f"三大法人買超 {iv[2] / 1000:,.0f} 張（外資 {iv[0] / 1000:+,.0f}、投信 {iv[1] / 1000:+,.0f}）")
        elif insti is None:
            risks.append("三大法人資料尚未公布（約 15:00 後），還沒排除法人賣超的股票")
        picks.append({
            "代號": code, "名稱": s[t]["name"], "市場": s[t]["market"],
            "收盤": f["close"], "漲跌%": round(f["pct"], 2),
            "量比": round(f["vol_ratio"], 2), "5日%": round(f["r5"], 1),
            "成交張數": int(f["volume"] / 1000),
            "股本(億)": round(cap_e, 2) if cap_e is not None else None,
            "強度": rank_score(f, cap_e, small_cap),
            "理由": reasons, "風險": risks, "計畫": plan, "鎖漲停": locked_limit(f),
            "連續上榜": streak(s, t, lambda d: (lambda g: bool(g) and passes(g, opt))(features(s, d))
                               and not (insti_hist and insti_selling(insti_hist[d], code))),
        })
    picks.sort(key=lambda p: p["強度"], reverse=True)
    return picks


def held_bars(s, start, n, days):
    """持有期間（從 start 起）的 K 棒，供模擬交易使用：
      - 除權息：把除權息扣掉的價差加回之後的價格（股利仍在投資人手上），
        避免除息造成假停損，報酬也包含股利
      - 一字跌停：標記 locked_down，當天停損單賣不掉
    回傳 [(交易日索引, {open, high, low, close, locked_down})]。"""
    out, cum = [], 0.0
    for d in range(start, min(start + days, n)):
        x = s[d]
        if not x or x["close"] is None or x["open"] is None:
            continue
        prev = s[d - 1]
        if d > start and prev and prev["close"] is not None and x["change"] is not None:
            gap = prev["close"] - (x["close"] - x["change"])  # 昨收 - 今日參考價
            if gap > prev["close"] * 0.003:
                cum += gap
        p = pct_change(x)
        out.append((d, {"open": x["open"] + cum, "high": x["high"] + cum, "low": x["low"] + cum,
                        "close": x["close"] + cum,
                        "locked_down": x["high"] == x["low"] and p is not None and p <= -9.5}))
    return out


def locked_limit(f):
    """訊號日收盤鎖在漲停（漲幅 ≥ 9.5% 且收在最高價）。"""
    return f["pct"] is not None and f["pct"] >= 9.5 and f["close"] >= f["high"]


def simulate(s, t, f, n, chase=False):
    """照操作計畫模擬一筆交易，回傳扣成本後報酬 %；隔天開盤不在進場區間則回傳 None。
    同一天同時碰到停損與目標時，保守假設先碰到停損；一字跌停賣不掉，延到下一個能成交的開盤出場。"""
    lv = levels(f)
    nxt = s[t + 1] if t + 1 < n else None
    if not nxt or not nxt["open"]:
        return None  # 隔天停止交易或沒有成交
    o = nxt["open"]
    if chase:  # 研究用：只模擬「開盤跳空高於進場區間、仍照樣追價」的情況
        if o <= lv["entry_high"]:
            return None
    elif not (lv["entry_low"] <= o <= lv["entry_high"]):
        return None
    entry, stop = o, lv["stop"]
    targets = [lv["tp1"], lv["tp2"]]
    pos, pnl, last_close, pending = 1.0, 0.0, None, False
    for d, x in held_bars(s, t + 1, n, MAX_HOLD + 10):
        if pending:  # 前一天觸發停損但跌停鎖死
            if x["locked_down"]:
                continue
            pnl += pos * (x["open"] / entry - 1)
            pos = 0
            break
        if d >= t + 1 + MAX_HOLD:
            break
        gap_open = d > t + 1  # 進場日以開盤價買進，之後的日子可能跳空越過價位
        if x["low"] <= stop:
            if x["locked_down"]:
                pending = True
                continue
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


def backtest(history, opt=DEFAULTS, insti=None):
    """用同一套條件與操作計畫回測（見 simulate）。報酬已扣交易成本。
    insti：與 history 對齊的三大法人資料 list，提供時排除當天法人賣超的訊號。
    另附「隔日開盤買、持有 1 日／3 日收盤賣」作為對照。"""
    series = build_series(history)
    n = len(history)
    r1, r3, rp = [], [], []
    by_lock = {k: {"plan": [], "chase": [], "overnight": []} for k in ("locked", "unlocked")}
    signal_days, skipped = set(), 0
    for t in range(20, n - 1):
        for code, s in series.items():
            r = s[t]
            # 先用漲幅與成交量快速排除（passes 也要求這兩項），省下大部分的指標計算
            if r is None or r["volume"] < opt["min_lots"] * 1000:
                continue
            p = pct_change(r)
            if p is None or p < opt["min_pct"]:
                continue
            f = features(s, t)
            if not f or not passes(f, opt):
                continue
            if insti and insti_selling(insti[t], code):
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
            # 依訊號日是否收盤鎖漲停，分別比較三種做法
            g = by_lock["locked" if locked_limit(f) else "unlocked"]
            if r is not None:
                g["plan"].append(r)
            rc = simulate(s, t, f, n, chase=True)
            if rc is not None:
                g["chase"].append(rc)
            g["overnight"].append((nxt["open"] / f["close"] - 1) * 100 - ROUND_TRIP_COST)

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
        "insti_filter": bool(insti),
        "by_lock": {k: {m: stats(v) for m, v in g.items()} for k, g in by_lock.items()},
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
    "top": 10,           # 每天只列型態完整度最高的前幾檔
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


def pre_picks(history, ref, small_cap=10, total=None):
    """回傳型態完整度最高的前 PRE["top"] 檔；total 傳入 list 時，放入符合條件的總檔數。"""
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
            "連續上榜": streak(s, t, lambda d: (lambda g: bool(g) and pre_passes(g))(pre_features(s, d))),
        })
    out.sort(key=lambda p: p["強度"], reverse=True)
    if total is not None:
        total.append(len(out))
    return out[:PRE["top"]]


def pre_simulate(s, t, f, n):
    """突破箱頂才進場；回傳扣成本後報酬 %，5 日內沒突破回傳 None。
    同一天同時碰到停損與目標時，保守假設先碰到停損；一字跌停賣不掉，延到下一個能成交的開盤出場。"""
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
    last, pending = None, False
    for d, x in held_bars(s, entry_day, n, PRE["max_hold"] + 10):
        if pending:  # 前一天觸發停損但跌停鎖死
            if x["locked_down"]:
                continue
            return (x["open"] / entry - 1) * 100 - ROUND_TRIP_COST
        if d >= entry_day + PRE["max_hold"]:
            break
        later = d > entry_day
        if x["low"] <= stop:
            if x["locked_down"]:
                pending = True
                continue
            px = min(x["open"], stop) if later else stop
            return (px / entry - 1) * 100 - ROUND_TRIP_COST
        if x["high"] >= target:
            px = max(x["open"], target) if later else target
            return (px / entry - 1) * 100 - ROUND_TRIP_COST
        last = x["close"]
    return (last / entry - 1) * 100 - ROUND_TRIP_COST if last else None


def hold_return(s, t, n, hold):
    """隔日開盤買、持有 hold 日收盤賣的報酬 %（含除權息還原），作為對照組。
    資料不完整或出現減資、分割等價格斷層時回傳 None。"""
    bars = held_bars(s, t + 1, n, hold)
    if not bars or bars[-1][0] != t + hold:
        return None
    ret = (bars[-1][1]["close"] / bars[0][1]["open"] - 1) * 100
    # 受漲跌幅限制，hold 日內不可能超過這個範圍；超出代表減資、分割等價格斷層
    if not 0.9 ** hold * 100 - 100 - 1 < ret < 1.1 ** hold * 100 - 100 + 1:
        return None
    return ret


def hold_return_stop(s, t, n, hold, stop):
    """隔日開盤買、最多持有 hold 日；盤中跌破「進場價 ×(1-stop)」就出場（跳空跌破用開盤價，一字跌停延後）。
    回傳 (報酬 %, 是否已出場, 持有天數)，含除權息還原、未扣成本；資料不足回傳 None。"""
    bars = held_bars(s, t + 1, n, hold + 10)
    if not bars or bars[0][0] != t + 1:
        return None
    entry = bars[0][1]["open"]
    level = entry * (1 - stop)
    pending = False
    for k, (d, b) in enumerate(bars):
        if pending:
            if not b["locked_down"]:
                return (b["open"] / entry - 1) * 100, True, k + 1
            continue
        if d > t + hold:
            break
        if b["low"] <= level:
            if b["locked_down"]:
                pending = True
                continue
            px = level if d == t + 1 else min(b["open"], level)
            return (px / entry - 1) * 100, True, k + 1
        if d == t + hold:
            return (b["close"] / entry - 1) * 100, True, k + 1
    last = [b for d, b in bars if d <= t + hold]
    return ((last[-1]["close"] / entry - 1) * 100, False, len(last)) if last else None


def pivot_levels(high, low, close):
    """樞紐點（Pivot Point）：用今天的最高、最低、收盤算出明天的參考壓力與支撐。"""
    p = (high + low + close) / 3
    return {"P": p, "R1": 2 * p - low, "R2": p + (high - low), "S1": 2 * p - high, "S2": p - (high - low)}


def pivot_stats(history):
    """2 年資料中，隔天最高價碰到 R1、R2，最低價碰到 S1、S2 的機率（%）。
    分成所有股票與「當天大漲 7% 以上」兩組（成交量 ≥ 500 張）。"""
    series = build_series(history)
    n = len(history)
    groups = {"all": [0, {}], "strong": [0, {}]}
    for t in range(max(1, n - 500), n - 1):
        for s in series.values():
            r, nx = s[t], s[t + 1]
            if not r or not nx or None in (r["high"], r["low"], r["close"], nx["high"], nx["low"]) \
                    or r["volume"] < 500_000:
                continue
            lv = pivot_levels(r["high"], r["low"], r["close"])
            p = pct_change(r)
            keys = ["all"] + (["strong"] if p is not None and p >= 7 else [])
            for g in keys:
                groups[g][0] += 1
                for k, v in lv.items():
                    if k == "P":
                        continue
                    if (nx["high"] >= v) if k[0] == "R" else (nx["low"] <= v):
                        groups[g][1][k] = groups[g][1].get(k, 0) + 1
    return {g: {k: round(c / tot * 100) for k, c in hits.items()} | {"n": tot}
            for g, (tot, hits) in groups.items() if tot}


def _stats(rs):
    if not rs:
        return None
    return {"trades": len(rs), "avg": round(sum(rs) / len(rs), 2),
            "win": round(sum(1 for x in rs if x > 0) / len(rs) * 100, 1),
            "best": round(max(rs), 1), "worst": round(min(rs), 1)}


def pre_backtest(history, market=None):
    """回測起漲前夕規則，並以「同期間所有股票隔日開盤買、持有 10 日」作為對照。
    market：與 history 對齊的大盤狀態 list，提供時只在大盤站上 20 日均線的日子進場，
    對照組也只取這些日子，比較才公平。"""
    series = build_series(history)
    n = len(history)
    hold = PRE["max_hold"]
    last_t = n - PRE["trigger_days"] - hold - 1  # 確保每筆訊號都有完整的觀察與持有期
    rs, base, signals, no_break = [], [], 0, 0
    for t in range(60, last_t + 1):
        if market and market[t] and market[t]["up"] is False:
            continue  # 大盤跌破 20 日均線：暫停進場
        today = []
        for s in series.values():
            r = s[t]
            if r is None or r["volume"] < 500_000:
                continue
            if t % 2 == 0:  # 對照組每 2 天取樣一次即可
                ret = hold_return(s, t, n, hold)
                if ret is not None:
                    base.append(ret - ROUND_TRIP_COST)
            p = pct_change(r)
            if p is None or not 0 < p <= PRE["max_pct"]:
                continue  # pre_passes 也要求今日上漲 0～4%，先快速排除
            f = pre_features(s, t)
            if f and pre_passes(f):
                today.append((pre_strength(f), s, f))
        # 與網頁一致：每天只做型態完整度最高的前 PRE["top"] 檔
        today.sort(key=lambda x: -x[0])
        for _, s, f in today[:PRE["top"]]:
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
        "market_filter": bool(market),
    }


# ================================================================ 隔日沖
"""隔日沖：盤中漲到 +7% 以上、股價接近或突破 60 日高點的股票，收盤前買進、隔天開盤賣出。

11 年研究（research/overnight.py，2015～2026）：「漲到 +7% 就用 +7% 價格買、接近或突破 60 日高、
隔天開盤賣」訓練期勝率 49.9%、平均 +0.17%，驗證期勝率 50.7%、平均 +0.26%（已扣成本 0.585%）。
收盤鎖住漲停的股票隔天開盤平均 +1.6～2.0%（勝率約 70%），但收盤前通常買不到；
漲停被打開的股票隔天開盤平均 -0.9%。抱到隔天收盤的結果明顯較差。
"""

OVERNIGHT = {"min_pct": 7.0, "near_high": 0.98, "min_lots": 1000, "min_price": 10, "top": 10}
OVERNIGHT_RESEARCH = {
    "period": "2015/04～2026/10", "train": {"win": 49.9, "avg": 0.17}, "test": {"win": 50.7, "avg": 0.26},
    "locked": "收盤鎖住漲停：隔天開盤平均 +1.6～2.0%、勝率約 70%（但收盤前通常買不到）",
    "opened": "漲停被打開：隔天開盤平均 -0.9%、勝率約 30%",
    # research/overnight_exit.py：隔天不同賣法（訓練／驗證，已扣成本）
    "exit_open": {"train": {"win": 50.8, "avg": 0.36}, "test": {"win": 51.4, "avg": 0.42}},
    "exit_limit": {"train": {"win": 61.1, "avg": 0.19}, "test": {"win": 62.3, "avg": 0.23}},
    # research/first_bar.py：同樣 20 日均量 ≥ 500 張的股票中，起漲第 1 根 vs 其他（隔天開盤賣）
    "first_bar": {"train": {"win": 53.1, "avg": 0.54}, "test": {"win": 49.9, "avg": 0.42}},
    "not_first": {"train": {"win": 48.7, "avg": -0.02}, "test": {"win": 50.5, "avg": 0.13}},
}


def is_first_bar(past, vol_ratio, vol20):
    """起漲第 1 根：前 20 天盤整（收盤最高 ÷ 最低 ≤ 1.15、沒有任何一天漲 ≥ 5%），今天量 ≥ 20 日均量 2 倍。
    與研究相同，只限 20 日均量 ≥ 500 張的股票。"""
    base = [x for x in past[-20:] if x["close"] is not None and x["change"] is not None]
    if len(base) < 20 or not vol_ratio or vol_ratio < 2 or vol20 < 500_000:
        return False
    closes = [x["close"] for x in base]
    if max(closes) / min(closes) > 1.15:
        return False
    return not any(x["close"] - x["change"] > 0 and x["change"] / (x["close"] - x["change"]) >= 0.05 for x in base)
LIMIT_SELL_PCT = 2  # 另一種賣法：隔天掛 +2% 限價賣出，沒成交就收盤賣


def tick_ceil(price):
    """依台股升降單位，取不低於 price 的有效價格。"""
    f = tick_floor(price)
    return f if f >= price - 1e-9 else tick_up(f)


def limit_sell_price(buy):
    return tick_ceil(buy * (1 + LIMIT_SELL_PCT / 100))


def limit_sell_fill(buy, o, h, c):
    """隔天掛 +2% 限價：開盤就超過用開盤價、盤中碰到用限價，沒碰到收盤賣。"""
    tgt = limit_sell_price(buy)
    return o if o >= tgt else tgt if h >= tgt else c


def _overnight_check(s, t):
    """第 t 天是否符合隔日沖條件；符合回傳特徵 dict。"""
    r = s[t]
    if r is None or None in (r["close"], r["open"], r["high"], r["change"]):
        return None
    prev = r["close"] - r["change"]
    if prev <= 0 or r["close"] < OVERNIGHT["min_price"] or r["volume"] < OVERNIGHT["min_lots"] * 1000:
        return None
    pct = (r["close"] / prev - 1) * 100
    open_pct = (r["open"] / prev - 1) * 100
    if pct < OVERNIGHT["min_pct"] or open_pct >= OVERNIGHT["min_pct"]:
        return None  # 要「開盤後才漲上 +7%」，開盤就在 +7% 以上的買不到研究中的價格
    past = [x for x in s[max(0, t - 60):t] if x and x["high"] is not None]
    if len(past) < 40:
        return None
    prior_high = max(x["high"] for x in past)
    if r["high"] < prior_high * OVERNIGHT["near_high"]:
        return None
    vol20 = sum(x["volume"] for x in past[-20:]) / 20
    vol_ratio = r["volume"] / vol20 if vol20 else None
    return {"pct": pct, "open_pct": open_pct, "prev": prev, "prior_high": prior_high,
            "vol_ratio": vol_ratio, "limit": pct >= 9.5, "first": is_first_bar(past, vol_ratio, vol20)}


def overnight_picks(history, ref):
    series = build_series(history)
    t = len(history) - 1
    out = []
    for code, s in series.items():
        if code in ref["disposal"]:
            continue
        f = _overnight_check(s, t)
        if not f:
            continue
        r = s[t]
        reasons = ([f"★ 起漲第 1 根：前 20 天盤整（波動 ≤ 15%、沒有大漲），今天放量 {f['vol_ratio']:.1f} 倍首度大漲；"
                    "11 年回測這類隔日沖平均明顯較好"] if f["first"] else []) + [
            f"開盤 {f['open_pct']:+.1f}%，盤中漲到 {f['pct']:+.1f}%，買盤持續推升",
            f"今日最高 {r['high']:g}，{'突破' if r['high'] > f['prior_high'] else '接近'}前 60 日高點 {f['prior_high']:g}",
        ]
        if f["vol_ratio"]:
            reasons.append(f"成交量（預估全天）為 20 日均量的 {f['vol_ratio']:.1f} 倍")
        risks = ["收盤若沒鎖住漲停、或漲幅縮小，隔天開盤平均是虧損"]
        if f["limit"]:
            risks.insert(0, "目前已在漲停，委買排隊中，可能買不到")
        if code in ref["attention"]:
            risks.append("已列注意股，若再觸發可能被處置")
        out.append({
            "代號": code, "名稱": r["name"], "市場": r["market"], "現價": r["close"],
            "漲跌%": round(f["pct"], 2), "開盤%": round(f["open_pct"], 2),
            "漲停價": tick_floor(f["prev"] * 1.1), "已漲停": f["limit"],
            "限價賣出": limit_sell_price(r["close"]), "起漲第1根": f["first"],
            "量比": round(f["vol_ratio"], 2) if f["vol_ratio"] else None,
            "理由": reasons, "風險": risks,
        })
    # 越接近漲停排越前面（收盤鎖住漲停的隔天表現最好），同漲幅依量比
    # 起漲第 1 根排最前面（11 年回測較好），其餘越接近漲停排越前面
    out.sort(key=lambda p: (p["起漲第1根"], p["漲跌%"], p["量比"] or 0), reverse=True)
    return out[:OVERNIGHT["top"]]


def overnight_backtest(history):
    """用網站的 2 年資料回測：符合條件的股票以 +7% 價格買進、隔天開盤賣出（已扣成本、含除權息）。"""
    series = build_series(history)
    n = len(history)
    rs, base = [], []
    for t in range(60, n - 1):
        for s in series.values():
            r, nxt = s[t], s[t + 1]
            if not r or not nxt or nxt["open"] is None or r["change"] is None or r["close"] is None:
                continue
            prev = r["close"] - r["change"]
            if prev <= 0:
                continue
            gap = r["close"] - (nxt["close"] - nxt["change"]) if nxt["change"] is not None else 0
            div = gap if gap > r["close"] * 0.003 else 0  # 隔天除權息：價差加回
            if t % 5 == 0 and r["volume"] >= 500_000:
                b = ((nxt["open"] + div) / r["close"] - 1) * 100 - ROUND_TRIP_COST
                if -15 < b < 15:  # 排除減資、分割等價格斷層
                    base.append(b)
            if r["high"] is None or (r["high"] / prev - 1) * 100 < OVERNIGHT["min_pct"]:
                continue
            # 研究的進場：盤中漲到 +7% 就用 +7% 價格買（開盤已在 +7% 以上的不算）
            if (r["open"] / prev - 1) * 100 >= OVERNIGHT["min_pct"] or r["volume"] < OVERNIGHT["min_lots"] * 1000:
                continue
            past = [x for x in s[max(0, t - 60):t] if x and x["high"] is not None]
            if len(past) < 40 or r["high"] < max(x["high"] for x in past) * OVERNIGHT["near_high"] or r["close"] < 10:
                continue
            entry = prev * (1 + OVERNIGHT["min_pct"] / 100)
            ret = ((nxt["open"] + div) / entry - 1) * 100 - ROUND_TRIP_COST
            if -15 < ret < 15:
                rs.append(ret)
    return {"from": history[60][0].isoformat(), "to": history[-1][0].isoformat(),
            "plan": _stats(rs), "baseline": _stats(base), "cost": ROUND_TRIP_COST}
