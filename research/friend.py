#!/usr/bin/env python3
"""朋友的選股策略 vs 我們的明日精選 3 檔（11 年回測）。

朋友的定義（未提供算法的部分採常見定義）：
  週多方成本 = 近 5 日成交均價（成交金額 ÷ 成交股數），月多方成本 = 近 20 日成交均價
  抄底：收盤 ≥ 週成本 × 1.05、收盤 ≥ 月成本 × 1.40、成交金額 ≥ 6,000 萬
  嘎空（未含券資比）：10 日高低差 ≥ 20%、近 10 日 K 曾 > 80 且今天 K < 50、收盤 > 月成本、成交金額 ≥ 6,000 萬
  出場：持有 5～8 天日均報酬 < 1%、9～19 天 < 1.5%、20 天以上 < 2% 就收盤出場；K < 19.9 出場；最長 60 天
  進場：尾盤買（當天收盤價）
我們的明日精選：營收動能（創 12 個月新高、年增 > 20%、站上季線）＋今天突破 60 日新高、日均量 ≥ 1000 張，
  成交值前 3，隔天開盤買、持有 20 天。
報酬含除權息還原、已扣成本 0.585%。

  python3 research/friend.py
"""
import json
import os
import sys
from collections import defaultdict, deque

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import revenue_long as L  # noqa: E402
import revenue_study as R  # noqa: E402

COST = 0.585
MAX_HOLD = 60


def friend_exit(k, ret, kval):
    """朋友的出場規則：第 k 天收盤、累計報酬 ret%、當天 K 值。"""
    if kval is not None and kval < 19.9:
        return True
    if 5 <= k <= 8 and ret / k < 1.0:
        return True
    if 9 <= k <= 19 and ret / k < 1.5:
        return True
    if k >= 20 and ret / k < 2.0:
        return True
    return k >= MAX_HOLD


def main():
    days = L.trading_days()
    n = len(days)
    split = int(n * 0.75)
    rev = R.load_revenue()
    window = deque(maxlen=61)
    kd = {}           # 代號 -> (K, D)
    khist = defaultdict(lambda: deque(maxlen=10))
    positions = []
    res = defaultdict(list)   # (策略, 期間) -> [(報酬, 持有天數)]

    for t, ymd in enumerate(days):
        day = {}
        for m in ("twse", "tpex"):
            for r in json.load(open(os.path.join(L.CACHE_DIR, f"{m}_{ymd}.json"), encoding="utf-8")):
                if None not in (r["close"], r["open"], r["high"], r["low"], r["change"]):
                    day[r["code"]] = (r["open"], r["high"], r["low"], r["close"], r["change"],
                                      r["volume"], r.get("amount") or 0)
        window.append(day)
        # KD（9 日）
        for code, x in day.items():
            hs = [w[code][1] for w in list(window)[-9:] if code in w]
            ls = [w[code][2] for w in list(window)[-9:] if code in w]
            if len(hs) < 9:
                continue
            hi, lo = max(hs), min(ls)
            rsv = 50 if hi == lo else (x[3] - lo) / (hi - lo) * 100
            k0, d0 = kd.get(code, (50, 50))
            k = k0 * 2 / 3 + rsv / 3
            kd[code] = (k, d0 * 2 / 3 + k / 3)
            khist[code].append(k)
        # 更新部位
        still = []
        for p in positions:
            x = day.get(p["code"])
            if x:
                o, h, l, c, ch, v, a = x
                if p["entry"] is None:
                    p["entry"] = o  # 隔天開盤買
                elif p["last"] and p["last"] - (c - ch) > p["last"] * 0.003:
                    p["cum"] += p["last"] - (c - ch)
                p["last"] = c
                p["k"] += 1  # 進場後第幾個交易日（明天是第 1 天）
                ret = ((c + p["cum"]) / p["entry"] - 1) * 100
                kval = kd.get(p["code"], (None,))[0]
                done = (p["exit"] == "fixed20" and p["k"] >= 20) or \
                       (p["exit"] == "friend" and p["k"] >= 1 and friend_exit(p["k"], ret, kval))
                if done:
                    if -70 < ret < 300:
                        res[(p["name"], p["t"] >= split)].append((ret - COST, p["k"]))
                    continue
            still.append(p)
        positions = still
        if len(window) < 61 or t + MAX_HOLD >= n:
            continue

        mine = []
        for code, (o, h, l, c, ch, v, a) in day.items():
            if len(code) != 4 or not code.isdigit() or code[0] == "0" or c < 10:
                continue
            past = [w[code] for w in list(window)[:-1] if code in w]
            if len(past) < 59:
                continue
            last5 = past[-4:] + [day[code]]
            last20 = past[-19:] + [day[code]]
            v5, v20 = sum(x[5] for x in last5), sum(x[5] for x in last20)
            vwap5 = sum(x[6] for x in last5) / v5 if v5 else None
            vwap20 = sum(x[6] for x in last20) / v20 if v20 else None
            if not vwap5 or not vwap20:
                continue
            k = kd.get(code, (None,))[0]
            # 朋友：抄底（尾盤買：以當天收盤價進場，第 1 天從明天開始算）
            if a >= 6e7 and c >= vwap5 * 1.05 and c >= vwap20 * 1.40:
                positions.append({"code": code, "name": "朋友：抄底＋他的出場", "exit": "friend", "t": t,
                                  "entry": c, "cum": 0.0, "last": c, "k": 0, "started": False})
            # 朋友：嘎空（未含券資比）
            last10 = past[-9:] + [day[code]]
            rng = max(x[1] for x in last10) / min(x[2] for x in last10) - 1
            if (a >= 6e7 and rng >= 0.20 and k is not None and k < 50 and len(khist[code]) == 10
                    and max(khist[code]) > 80 and c > vwap20):
                positions.append({"code": code, "name": "朋友：嘎空（未含券資比）＋他的出場", "exit": "friend", "t": t,
                                  "entry": c, "cum": 0.0, "last": c, "k": 0, "started": False})
            # 我們：明日精選候選
            if sum(x[5] for x in last20) / 20 < 1_000_000:
                continue
            if c <= (sum(x[3] for x in past[-59:]) + c) / 60 or c <= max(x[1] for x in past[-60:]):
                continue
            f = R.rev_features(rev, code, ymd)
            if f and f["high12"] and f["yoy"] > 20:
                mine.append((sum(x[3] * x[5] for x in last20), code))
        mine.sort(reverse=True)
        for _, code in mine[:3]:
            for name, ex in (("我們：明日精選＋持有20天", "fixed20"), ("我們：明日精選＋朋友的出場", "friend")):
                positions.append({"code": code, "name": name, "exit": ex, "t": t,
                                  "entry": None, "cum": 0.0, "last": None, "k": 0, "started": False})

    def st(xs):
        if len(xs) < 30:
            return f"n={len(xs):5d} 樣本太少"
        rets = [r for r, _ in xs]
        hold = sum(k for _, k in xs) / len(xs)
        avg = sum(rets) / len(rets)
        return (f"n={len(xs):5d} 勝率{sum(r > 0 for r in rets) / len(rets) * 100:5.1f}% 平均{avg:+6.2f}% "
                f"持有{hold:4.1f}天 每天{avg / hold:+.3f}%")

    print(f"行情 {days[60]} ～ {days[-1]}；訓練期到 {days[split - 1]}，驗證期從 {days[split]}；已扣成本\n")
    for name in ("朋友：抄底＋他的出場", "朋友：嘎空（未含券資比）＋他的出場",
                 "我們：明日精選＋持有20天", "我們：明日精選＋朋友的出場"):
        print(f"{name}")
        print(f"   訓練 {st(res[(name, False)])}")
        print(f"   驗證 {st(res[(name, True)])}")


if __name__ == "__main__":
    main()
