#!/usr/bin/env python3
"""當沖研究（11 年）：開盤買、收盤賣（做多），或開盤賣、收盤買回（做空），只用開盤前／開盤當下知道的條件選股。

只有日 K 資料，無法回測盤中進出；這裡以「開盤價進、收盤價出」近似最單純的當沖。
成本：手續費 0.1425% × 2 ＋ 當沖證交稅 0.15% = 0.435%。
股票範圍：一般股、股價 ≥ 10、20 日均量 ≥ 1000 張。分組：
  前一天收盤鎖漲停／前一天漲停被打開／前一天起漲第 1 根（漲 ≥ 5%）／前一天隔日沖條件（盤中 +7%）
  今天開盤跳空 ≥ +3%、≥ +5%、≤ -3%
  以及組合：前一天鎖漲停且今天開高 ≥ 3%……
報酬已扣成本；做空的報酬 = 開盤 ÷ 收盤 - 1 - 成本。

  python3 research/daytrade.py
"""
import json
import os
import sys
from collections import defaultdict, deque

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import revenue_long as L  # noqa: E402

COST = 0.435


def tick_floor(p):
    for limit, tick in ((10, 0.01), (50, 0.05), (100, 0.1), (500, 0.5), (1000, 1)):
        if p < limit:
            break
    else:
        tick = 5
    return round(int(round(p / tick, 6)) * tick, 2)


def main():
    days = L.trading_days()
    n = len(days)
    split = int(n * 0.75)
    window = deque(maxlen=61)
    tags = {}                       # 前一天收盤後已知的標籤 {代號: [標籤]}
    res = defaultdict(list)
    for t, ymd in enumerate(days):
        day = {}
        for m in ("twse", "tpex"):
            for r in json.load(open(os.path.join(L.CACHE_DIR, f"{m}_{ymd}.json"), encoding="utf-8")):
                if None not in (r["close"], r["open"], r["high"], r["low"], r["change"]) and r["close"] - r["change"] > 0:
                    day[r["code"]] = (r["open"], r["high"], r["low"], r["close"], r["change"], r["volume"])
        # 今天：開盤進、收盤出
        for code, tg in tags.items():
            x = day.get(code)
            if not x:
                continue
            o, h, l, c, ch, v = x
            prev = c - ch
            gap = (o / prev - 1) * 100
            if abs(gap) > 10.5:
                continue
            g = list(tg)
            if gap >= 5:
                g.append("今天開高 ≥5%")
            if gap >= 3:
                g.append("今天開高 ≥3%")
            if gap <= -3:
                g.append("今天開低 ≤-3%")
            for base in tg:
                if base == "全部":
                    continue
                if gap >= 3:
                    g.append(f"{base}＋開高≥3%")
                elif gap <= 0:
                    g.append(f"{base}＋開平或開低")
                if gap <= -3:
                    g.append(f"{base}＋開低≤-3%")
            long_ = (c / o - 1) * 100 - COST
            short = (o / c - 1) * 100 - COST
            squeezed = c >= tick_floor(prev * 1.1)  # 收盤鎖漲停：放空的人可能買不回來
            if -15 < long_ < 15:
                for name in g:
                    res[(name, "多", t >= split)].append(long_)
                    for sl in (3, 5):  # 做多停損：最低價碰到開盤價 -sl% 就以停損價賣出（偏保守）
                        res[(f"{name}｜停損{sl}%", "多", t >= split)].append(-sl - COST if l <= o * (1 - sl / 100) else long_)
                    res[(name, "空", t >= split)].append(short)
                    res[(name, "空-軋", t >= split)].append(1 if squeezed else 0)
                    for sl in (3, 5):  # 放空停損：最高價碰到開盤價 +sl% 就以停損價買回（日 K 無法判斷先後，一律算停損，偏保守）
                        r_sl = -sl * o / o - COST if h >= o * (1 + sl / 100) else short
                        res[(f"{name}｜停損{sl}%", "空", t >= split)].append(r_sl)
        window.append(day)
        tags = {}
        if len(window) < 61:
            continue
        for code, (o, h, l, c, ch, v) in day.items():
            if len(code) != 4 or not code.isdigit() or code[0] == "0" or c < 10:
                continue
            past = [w[code] for w in list(window)[:-1] if code in w]
            if len(past) < 59:
                continue
            vol20 = sum(x[5] for x in past[-19:] + [day[code]]) / 20
            if vol20 < 1_000_000:
                continue
            prev = c - ch
            lim = tick_floor(prev * 1.1)
            tg = ["全部"]
            if c >= lim:
                tg.append("前天鎖漲停")
            elif h >= lim:
                tg.append("前天漲停被打開")
            hi60 = max(x[1] for x in past[-60:])
            if h / prev >= 1.07 and o / prev < 1.07 and v >= 1_000_000 and h >= hi60 * 0.98:
                tg.append("前天符合隔日沖")
            closes = [x[3] for x in past[-20:]]
            if ch / prev >= 0.05 and c > hi60 and v >= vol20 * 2 and max(closes) / min(closes) <= 1.15 \
                    and not any(x[3] - x[4] > 0 and x[4] / (x[3] - x[4]) >= 0.05 for x in past[-20:]):
                tg.append("前天起漲第1根")
            if vol20 >= 5_000_000:
                tg.append("均量≥5000張")
            tags[code] = tg

    def risk(g, period):
        xs = sorted(res[(g, "空", period)])
        sq = res[(g, "空-軋", period)]
        if len(xs) < 50:
            return ""
        if not sq:
            return f"最差{xs[0]:+6.1f}%"
        return (f"最差{xs[0]:+6.1f}% 最差5%平均{sum(xs[:len(xs) // 20]) / (len(xs) // 20):+5.1f}% "
                f"收盤被軋到漲停{sum(sq) / len(sq) * 100:4.1f}%")

    def st(xs):
        if len(xs) < 50:
            return f"n={len(xs):6d} 樣本太少"
        return f"n={len(xs):6d} 平均{sum(xs) / len(xs):+6.2f}% 勝率{sum(x > 0 for x in xs) / len(xs) * 100:5.1f}%"

    print(f"{days[61]} ～ {days[-1]}；訓練期到 {days[split - 1]}，驗證期從 {days[split]}；已扣當沖成本 {COST}%\n")
    names = sorted({k[0] for k in res}, key=lambda g: (g != "全部", g))
    for side in ("多", "空"):
        print(f"===== 做{side}（{'開盤買、收盤賣' if side == '多' else '開盤賣、收盤買回'}）=====")
        for g in names:
            print(f"{g:22s}\t訓練 {st(res[(g, side, False)])}\t驗證 {st(res[(g, side, True)])}")
            if side == "空":
                print(f"{'':22s}\t  風險：訓練 {risk(g, False)}｜驗證 {risk(g, True)}")
        print()


if __name__ == "__main__":
    main()
