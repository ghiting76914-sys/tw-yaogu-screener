#!/usr/bin/env python3
"""隔日沖研究：第 t 天收盤買進，第 t+1 天開盤（或收盤）賣出。

訊號用第 t 天的全天資料判斷（等同收盤前確認條件後買進），價格用收盤價成交。
交易成本 0.585%（手續費 0.1425% × 2 + 證交稅 0.3%；隔日沖不是當沖，證交稅不減半）。
逐日讀取行情、只保留最近 60 天，避免一次載入 11 年資料。

  python3 research/overnight.py
"""
import os
import sys
from collections import defaultdict, deque

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import revenue_long as L  # noqa: E402  （共用 trading_days、load_day）

COST = 0.585
TRAIN_RATIO = 0.75


def pct(c, ch):
    if c is None or ch is None:
        return None
    prev = c - ch
    return ch / prev * 100 if prev > 0 else None


def main():
    days = L.trading_days()
    n = len(days)
    split = int(n * TRAIN_RATIO)
    window = deque(maxlen=61)  # 最近 61 天（含今天）{代號: (open, close, change, volume)}
    highs = deque(maxlen=61)   # 每日最高價 {代號: high}
    pending = []               # 前一天的訊號，等今天的開盤/收盤算報酬
    out = defaultdict(lambda: {"open": [[], []], "close": [[], []]})
    base = {"open": [[], []], "close": [[], []]}

    import json
    for t, ymd in enumerate(days):
        day, hi = {}, {}
        for m in ("twse", "tpex"):
            for r in json.load(open(os.path.join(L.CACHE_DIR, f"{m}_{ymd}.json"), encoding="utf-8")):
                if r["close"] is not None and r["open"] is not None:
                    day[r["code"]] = (r["open"], r["close"], r["change"], r["volume"])
                    hi[r["code"]] = (r["high"], r["low"])
        period = 1 if t - 1 >= split else 0
        # 結算前一天的訊號
        for code, entry, groups in pending:
            x = day.get(code)
            if not x:
                continue
            o, c, ch, _ = x
            # 除權息：今天參考價低於昨收的部分加回（股利在投資人手上）
            adj = max(entry - (c - ch), 0) if ch is not None and entry - (c - ch) > entry * 0.003 else 0
            ro = ((o + adj) / entry - 1) * 100 - COST
            rc = ((c + adj) / entry - 1) * 100 - COST
            if not (-15 < ro < 15 and -25 < rc < 25):
                continue  # 價格斷層（減資、分割）
            for g in groups:
                tgt = base if g == "對照組" else out[g]
                tgt["open"][period].append(ro)
                tgt["close"][period].append(rc)
        pending = []
        window.append(day)
        highs.append(hi)
        if len(window) < 61 or t == n - 1:
            continue

        for code, (o, c, ch, v) in day.items():
            if len(code) != 4 or not code.isdigit() or code[0] == "0" or c < 10:
                continue
            p = pct(c, ch)
            if p is None:
                continue
            groups = []
            if t % 5 == 0 and v >= 500_000:
                groups.append("對照組")  # 每 5 天抽樣：隨便一檔收盤買、隔天賣
            if p < 3 or v < 1_000_000:
                if groups:
                    pending.append((code, c, groups))
                continue
            h, l = hi[code]
            if h is None or l is None:
                continue
            past = [w[code] for w in list(window)[:-1] if code in w]
            past_hi = [w[code][0] for w in list(highs)[:-1] if code in w]
            if len(past) < 40:
                continue
            vol20 = sum(x[3] for x in past[-20:]) / 20
            vr = v / vol20 if vol20 else 0
            close_pos = 1.0 if h == l else (c - l) / (h - l)
            prior_high = max(past_hi[-60:])
            r5 = (c / past[-5][1] - 1) * 100 if len(past) >= 5 else 0
            limit = p >= 9.5
            locked = limit and c >= h and o >= h        # 一字漲停：開盤就鎖住，幾乎買不到
            closed_limit = limit and c >= h              # 收盤鎖在漲停
            prev_p = pct(past[-1][1], past[-1][2]) if past else None
            first = limit and not (prev_p is not None and prev_p >= 9.5)
            rules = {
                "漲停（收盤鎖住，假設買得到）": closed_limit,
                "漲停（排除一字鎖死）": closed_limit and not locked,
                "首根漲停（排除一字）": closed_limit and first and not locked,
                "連續第2根以上漲停（排除一字）": closed_limit and not first and not locked,
                "漲停但收盤打開（收低於漲停）": limit and c < h,
                "強勢收高：漲5～9.5%、收最高附近、量比≥2": 5 <= p < 9.5 and close_pos >= 0.9 and vr >= 2,
                "強勢收高 + 突破60日高": 5 <= p < 9.5 and close_pos >= 0.9 and vr >= 2 and c > prior_high,
                "強勢收高 + 5日漲幅≤15%": 5 <= p < 9.5 and close_pos >= 0.9 and vr >= 2 and r5 <= 15,
                "首根漲停 + 突破60日高（排除一字）": closed_limit and first and not locked and c > prior_high,
                "首根漲停 + 量比≥3（排除一字）": closed_limit and first and not locked and vr >= 3,
                "漲3～5%、收最高附近、量比≥2": 3 <= p < 5 and close_pos >= 0.9 and vr >= 2,
            }
            groups += [k for k, ok in rules.items() if ok]
            if groups:
                pending.append((code, c, groups))

    def st(xs):
        if len(xs) < 30:
            return f"n={len(xs):5d} 樣本太少"
        return f"n={len(xs):5d} 勝率{sum(x > 0 for x in xs) / len(xs) * 100:5.1f}% 平均{sum(xs) / len(xs):+.2f}%"

    print(f"行情 {days[60]} ～ {days[-1]}；訓練期到 {days[split - 1]}，驗證期從 {days[split]}；已扣成本 {COST}%\n")
    for exit_ in ("open", "close"):
        print(f"===== 隔天{'開盤' if exit_ == 'open' else '收盤'}賣出 =====")
        print(f"{'':38s}{'訓練期':32s}驗證期")
        print(f"{'對照組（隨便一檔）':30s}\t{st(base[exit_][0])}\t{st(base[exit_][1])}")
        for k, v in out.items():
            print(f"{k:30s}\t{st(v[exit_][0])}\t{st(v[exit_][1])}")
        print()


if __name__ == "__main__":
    main()
