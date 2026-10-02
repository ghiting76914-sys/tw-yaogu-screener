#!/usr/bin/env python3
"""台股妖股篩選系統

資料來源（皆為公開資料，免金鑰）：
  - 證交所 每日收盤行情（上市）
  - 櫃買中心 每日收盤行情（上櫃）
  - 證交所 / 櫃買 OpenAPI：公司基本資料（股本）、注意股、處置股

用法：
  python3 yaogu.py                 # 用最近交易日篩選
  python3 yaogu.py --date 20260930 # 指定日期
  python3 yaogu.py --min-score 6 --top 30
"""
import argparse
import csv
import datetime as dt
import json
import os
import sys
import time
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "data", "cache")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
UA = {"User-Agent": "Mozilla/5.0 (Macintosh) tw-yaogu-screener"}

# 抓取的交易日數：60 日新高、起漲前夕的 60 日整理期都需要 60 天，多出來的天數用於回測
LOOKBACK = 121

# 證交所對頻繁請求會暫時封鎖 IP，抓歷史資料時每次請求間隔幾秒
REQUEST_INTERVAL = 3.0
_last_request = 0.0


# ---------------------------------------------------------------- 工具函式

def fetch_json(url, retries=6, interval=REQUEST_INTERVAL):
    global _last_request
    for attempt in range(retries):
        wait = interval - (time.time() - _last_request)
        if wait > 0:
            time.sleep(wait)
        _last_request = time.time()
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            if attempt == retries - 1:
                raise
            print(f"  請求失敗（{e}），{10 * (attempt + 1)} 秒後重試…", file=sys.stderr)
            time.sleep(10 * (attempt + 1))


def num(s):
    """'1,234.5' -> 1234.5；'--'、空值 -> None"""
    if s is None:
        return None
    s = str(s).replace(",", "").strip()
    if s in ("", "--", "---", "----", "N/A", "除權息", "除權", "除息"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def is_common_stock(code):
    # 一般股票為 4 碼數字且不以 0 開頭（排除 ETF、權證、特別股等）
    return len(code) == 4 and code.isdigit() and code[0] != "0"


# ---------------------------------------------------------------- 行情資料

def _parse_twse(d):
    if d.get("stat") != "OK":
        return []
    table = next((t for t in d.get("tables", []) if "每日收盤行情" in (t.get("title") or "")), None)
    if not table:
        return []
    f = {name: i for i, name in enumerate(table["fields"])}
    rows = []
    for r in table["data"]:
        sign = "-" if "-" in r[f["漲跌(+/-)"]] else ""
        change = num(r[f["漲跌價差"]])
        rows.append({
            "code": r[f["證券代號"]].strip(),
            "name": r[f["證券名稱"]].strip(),
            "open": num(r[f["開盤價"]]),
            "high": num(r[f["最高價"]]),
            "low": num(r[f["最低價"]]),
            "close": num(r[f["收盤價"]]),
            "change": -change if (sign and change is not None) else change,
            "volume": num(r[f["成交股數"]]) or 0,
            "amount": num(r[f["成交金額"]]) or 0,
            "shares": None,
        })
    return rows


def _parse_tpex(d):
    tables = d.get("tables") or []
    if not tables or not tables[0].get("data"):
        return []
    table = tables[0]
    f = {name.strip(): i for i, name in enumerate(table["fields"])}
    rows = []
    for r in table["data"]:
        rows.append({
            "code": r[f["代號"]].strip(),
            "name": r[f["名稱"]].strip(),
            "open": num(r[f["開盤"]]),
            "high": num(r[f["最高"]]),
            "low": num(r[f["最低"]]),
            "close": num(r[f["收盤"]]),
            "change": num(r[f["漲跌"]]),
            "volume": num(r[f["成交股數"]]) or 0,
            "amount": num(r[f["成交金額(元)"]]) or 0,
            "shares": num(r[f["發行股數"]]),
        })
    return rows


def load_day(market, date):
    """取得某市場某日的行情，有快取就用快取。非交易日回傳空 list。"""
    ymd = date.strftime("%Y%m%d")
    path = os.path.join(CACHE_DIR, f"{market}_{ymd}.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    if market == "twse":
        url = f"https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&date={ymd}&type=ALLBUT0999"
        rows = _parse_twse(fetch_json(url))
    else:
        url = ("https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes"
               f"?date={date.strftime('%Y')}%2F{date.strftime('%m')}%2F{date.strftime('%d')}&response=json")
        rows = _parse_tpex(fetch_json(url))

    rows = [r for r in rows if is_common_stock(r["code"])]  # 只保留一般股票，縮小快取
    # 今天的空資料可能只是還沒收盤，不寫入快取
    if rows or date < dt.date.today():
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, ensure_ascii=False)
    return rows


def load_history(end_date, days):
    """往回抓 days 個交易日。回傳 [(date, {code: row})]，由舊到新。"""
    history = []
    d = end_date
    misses = 0
    while len(history) < days and misses < 15:
        if d.weekday() < 5:
            print(f"  讀取 {d}…", end="\r", file=sys.stderr)
            twse = load_day("twse", d)
            tpex = load_day("tpex", d)
            # 兩市場交易日相同；只有一邊有資料代表另一邊當天還沒公布，先略過
            if twse and tpex:
                merged = {}
                for r in twse:
                    r["market"] = "上市"
                    merged[r["code"]] = r
                for r in tpex:
                    r["market"] = "上櫃"
                    merged[r["code"]] = r
                history.append((d, merged))
                misses = 0
            else:
                misses += 1
        d -= dt.timedelta(days=1)
    print(" " * 40, end="\r", file=sys.stderr)
    history.reverse()
    return history


# ---------------------------------------------------------------- 即時行情（證交所 MIS，約延遲 5～20 秒）

MIS_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch={}&json=1&delay=0"
MIS_BATCH = 100
LIVE_TTL = 180  # 盤中全市場快照的快取秒數


def _parse_mis(x):
    def p(k):
        v = num(x.get(k))
        return v if v else None

    prev = p("y")
    last = p("z") or p("pz")
    if last is None:
        # 冷門股收盤後可能沒有最新成交價，以最佳委買價估計，並限制在當日高低之間
        bids = (x.get("b") or "").split("_")
        last = num(bids[0]) if bids[0] else None
        if last is not None and p("h") and p("l"):
            last = min(max(last, p("l")), p("h"))
    vol = num(x.get("v")) or 0
    if last is None or prev is None or not vol:
        return None  # 今日尚無成交
    return {
        "code": x.get("c", "").strip(),
        "name": x.get("n", "").strip(),
        "open": p("o") or last,
        "high": max(p("h") or last, last),
        "low": min(p("l") or last, last),
        "close": last,
        "change": round(last - prev, 4),
        "volume": vol * 1000,
        "amount": last * vol * 1000,
        "shares": None,
        "market": "上市" if x.get("ex") == "tse" else "上櫃",
        "date": x.get("d"),
        "time": x.get("t") or x.get("%"),
        "limit_up": p("u"),
    }


def fetch_mis(pairs):
    """pairs: [(代號, 上市/上櫃)] -> {代號: row}"""
    out = {}
    for i in range(0, len(pairs), MIS_BATCH):
        chunk = pairs[i:i + MIS_BATCH]
        q = "|".join(f"{'tse' if m == '上市' else 'otc'}_{c}.tw" for c, m in chunk)
        for x in fetch_json(MIS_URL.format(q), interval=1.0).get("msgArray", []):
            row = _parse_mis(x)
            if row:
                out[row["code"]] = row
    return out


def live_snapshot(prev_day, today):
    """用 MIS 抓今天全市場一般股的即時行情。收盤後的快照視為最終資料。"""
    ymd = today.strftime("%Y%m%d")
    path = os.path.join(CACHE_DIR, f"live_{ymd}.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            snap = json.load(fh)
        if snap["final"] or time.time() - snap["fetched"] < LIVE_TTL:
            return snap

    pairs = [(c, r["market"]) for c, r in prev_day.items() if is_common_stock(c)]
    rows = {c: r for c, r in fetch_mis(pairs).items() if r["date"] == ymd}
    now = dt.datetime.now()
    snap = {"fetched": time.time(), "time": now.strftime("%H:%M:%S"),
            "final": now.time() >= dt.time(13, 35), "rows": rows}
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(snap, fh, ensure_ascii=False)
    return snap


def market_open_now():
    now = dt.datetime.now()
    return now.weekday() < 5 and dt.time(9, 0) <= now.time() <= dt.time(13, 35)


# ---------------------------------------------------------------- 基本資料 / 注意 / 處置

def load_reference(today):
    """股本、注意股、處置股。每天快取一次。"""
    path = os.path.join(CACHE_DIR, f"ref_{today.strftime('%Y%m%d')}.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    capital = {}
    for r in fetch_json("https://openapi.twse.com.tw/v1/opendata/t187ap03_L"):
        capital[r["公司代號"].strip()] = num(r.get("實收資本額"))
    for r in fetch_json("https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"):
        capital[r["SecuritiesCompanyCode"].strip()] = num(r.get("Paidin.Capital.NTDollars"))

    attention = {}
    for r in fetch_json("https://openapi.twse.com.tw/v1/announcement/notice"):
        if r.get("Code"):
            attention[r["Code"].strip()] = r.get("TradingInfoForAttention", "")
    for r in fetch_json("https://www.tpex.org.tw/openapi/v1/tpex_trading_warning_information"):
        if r.get("SecuritiesCompanyCode"):
            attention[r["SecuritiesCompanyCode"].strip()] = r.get("TradingInformation", "")

    disposal = {}
    for r in fetch_json("https://openapi.twse.com.tw/v1/announcement/punish"):
        if r.get("Code"):
            disposal[r["Code"].strip()] = r.get("DispositionPeriod", "")
    for r in fetch_json("https://www.tpex.org.tw/openapi/v1/tpex_disposal_information"):
        if r.get("SecuritiesCompanyCode"):
            disposal[r["SecuritiesCompanyCode"].strip()] = r.get("DispositionPeriod", "")

    ref = {"capital": capital, "attention": attention, "disposal": disposal}
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(ref, fh, ensure_ascii=False)
    return ref


# ---------------------------------------------------------------- 大盤與三大法人

TAIEX_REFRESH = 1800  # 當月加權指數尚缺今天時，間隔多久重抓一次（秒）


def load_taiex(dates):
    """加權指數每日收盤 {YYYYMMDD: 收盤}，涵蓋 dates 所在月份與前一個月（20 日均線需要）。
    來源：證交所 FMTQIK，每月一次請求；與研究程式共用 data/cache/taiex.json。"""
    path = os.path.join(CACHE_DIR, "taiex.json")
    data = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    fetched_at = data.pop("_fetched_at", {})
    first = min(dates)
    months = {(d.year, d.month) for d in dates} | {((first.replace(day=1) - dt.timedelta(days=1)).year,
                                                   (first.replace(day=1) - dt.timedelta(days=1)).month)}
    today = dt.date.today()
    changed = False
    for y, m in sorted(months):
        key = f"{y}{m:02d}"
        have = any(k.startswith(key) for k in data)
        current = (y, m) == (today.year, today.month)
        stale = (current and today.strftime("%Y%m%d") not in data
                 and time.time() - fetched_at.get(key, 0) > TAIEX_REFRESH)
        if have and not stale:
            continue
        for row in fetch_json(f"https://www.twse.com.tw/exchangeReport/FMTQIK?response=json&date={key}01").get("data", []):
            ry, rm, rd = row[0].split("/")
            data[f"{int(ry) + 1911}{rm}{rd}"] = num(row[4])
        fetched_at[key] = time.time()
        changed = True
    if changed:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({**data, "_fetched_at": fetched_at}, fh)
    return data


def taiex_live():
    """加權指數即時值（MIS t00）。回傳 (YYYYMMDD, 指數)，抓不到回傳 (None, None)。"""
    x = (fetch_json(MIS_URL.format("tse_t00.tw"), interval=1.0).get("msgArray") or [{}])[0]
    z = num(x.get("z"))
    return (x.get("d"), z) if z else (None, None)


def market_state(taiex, date):
    """date 當天（含）加權指數是否站上 20 日均線。資料不足回傳 None。"""
    ymd = date.strftime("%Y%m%d")
    closes = [taiex[k] for k in sorted(taiex) if k <= ymd and taiex[k]][-20:]
    if len(closes) < 20 or ymd not in taiex:
        return None
    ma20 = sum(closes) / 20
    return {"date": date.isoformat(), "close": taiex[ymd], "ma20": round(ma20, 2), "up": taiex[ymd] > ma20}


def parse_twse_insti(d):
    out = {}
    for r in d.get("data", []):
        code = r[0].strip()
        if is_common_stock(code):
            foreign = (num(r[4]) or 0) + (num(r[7]) or 0)  # 外陸資（不含外資自營商）+ 外資自營商
            out[code] = [foreign, num(r[10]) or 0, num(r[18]) or 0]
    return out


def parse_tpex_insti(d):
    out = {}
    tables = d.get("tables") or []
    for r in (tables[0].get("data", []) if tables else []):
        code = r[0].strip()
        if is_common_stock(code):
            out[code] = [num(r[10]) or 0, num(r[13]) or 0, num(r[23]) or 0]  # 外資合計、投信、三大法人合計
    return out


def load_insti(date):
    """三大法人買賣超 {代號: [外資, 投信, 三大法人合計]}（股數）。當天尚未公布回傳 None。"""
    ymd = date.strftime("%Y%m%d")
    out = {}
    for market in ("twse", "tpex"):
        path = os.path.join(CACHE_DIR, f"insti_{market}_{ymd}.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                rows = json.load(fh)
        else:
            if market == "twse":
                rows = parse_twse_insti(fetch_json(
                    f"https://www.twse.com.tw/fund/T86?response=json&date={ymd}&selectType=ALLBUT0999"))
            else:
                rows = parse_tpex_insti(fetch_json(
                    "https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade?type=Daily&sect=EW"
                    f"&date={date.year}%2F{date.month:02d}%2F{date.day:02d}&response=json"))
            if not rows:
                return None  # 還沒公布，不寫入快取
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(rows, fh)
        out.update(rows)
    return out


# ---------------------------------------------------------------- 指標與評分

def pct_change(row):
    if row is None or row["close"] is None or row["change"] is None:
        return None
    prev = row["close"] - row["change"]
    return row["change"] / prev * 100 if prev > 0 else None


def analyze(code, history, ref, args):
    series = [day.get(code) for _, day in history]
    today = series[-1]
    if today is None or today["close"] is None:
        return None
    if today["volume"] / 1000 < args.min_volume:
        return None

    close = today["close"]
    pct = pct_change(today)

    # 連續漲停天數（漲幅 >= 9.5% 視為漲停）
    streak = 0
    for r in reversed(series):
        p = pct_change(r)
        if p is not None and p >= 9.5:
            streak += 1
        else:
            break

    def ret(n):
        if len(series) <= n or series[-1 - n] is None or not series[-1 - n]["close"]:
            return None
        return (close / series[-1 - n]["close"] - 1) * 100

    past = [r for r in series[:-1] if r is not None]
    vols20 = [r["volume"] for r in past[-20:]]
    avg_vol20 = sum(vols20) / len(vols20) if vols20 else 0
    vol_ratio = today["volume"] / avg_vol20 if avg_vol20 else None

    highs60 = [r["high"] for r in past[-60:] if r["high"] is not None]
    high60 = max(highs60) if highs60 else None
    breakout = high60 is not None and close > high60

    cap = ref["capital"].get(code)
    if cap is None and today.get("shares"):
        cap = today["shares"] * 10
    cap_e = cap / 1e8 if cap else None  # 億元
    shares = cap / 10 if cap else None
    turnover = today["volume"] / shares * 100 if shares else None

    r5, r20 = ret(5), ret(20)

    # 每一項加分都記錄下來，網頁上顯示得分明細；評分規則說明見 web/index.html 的「評分依據」
    breakdown = []

    def add(points, text):
        breakdown.append([text, points])

    if cap_e is not None:
        if cap_e < args.small_cap:
            add(2, f"小股本{cap_e:.1f}億")
        elif cap_e < args.small_cap * 2:
            add(1, f"股本{cap_e:.1f}億")
    if vol_ratio is not None:
        if vol_ratio >= 3:
            add(2, f"爆量{vol_ratio:.1f}倍")
        elif vol_ratio >= 2:
            add(1, f"量增{vol_ratio:.1f}倍")
    if streak >= 1:
        add(2 + min(streak - 1, 3), "漲停" if streak == 1 else f"連{streak}根漲停")
    elif pct is not None and pct >= 5:
        add(1, f"大漲{pct:.1f}%")
    if r5 is not None:
        if r5 >= 20:
            add(2, f"5日+{r5:.0f}%")
        elif r5 >= 10:
            add(1, f"5日+{r5:.0f}%")
    if breakout:
        add(2, "突破60日高")
    if turnover is not None and turnover >= 10:
        add(1, f"週轉率{turnover:.0f}%")
    if code in ref["attention"]:
        add(1, "注意股")
    if code in ref["disposal"]:
        add(0, f"處置中({ref['disposal'][code]})")
    score = sum(p for _, p in breakdown)
    reasons = [t for t, _ in breakdown]

    return {
        "代號": code,
        "名稱": today["name"],
        "市場": today["market"],
        "收盤": close,
        "漲跌%": round(pct, 2) if pct is not None else None,
        "5日%": round(r5, 1) if r5 is not None else None,
        "20日%": round(r20, 1) if r20 is not None else None,
        "量比": round(vol_ratio, 2) if vol_ratio is not None else None,
        "成交張數": int(today["volume"] / 1000),
        "週轉率%": round(turnover, 2) if turnover is not None else None,
        "股本(億)": round(cap_e, 2) if cap_e is not None else None,
        "連續漲停": streak,
        "突破60日高": "是" if breakout else "",
        "分數": score,
        "訊號": "、".join(reasons),
        "得分明細": breakdown,
    }


STREAK_DAYS = 20  # 連續上榜最多往回算幾天


def score_history(code, history, ref, args, days=STREAK_DAYS):
    """最近 days 個交易日（含今天）每天的分數，由舊到新；當天不符合成交量等條件為 None。
    網頁依使用者設定的最低分數計算連續上榜天數。注意股、處置股用的是今天的名單。"""
    out = []
    for t in range(max(1, len(history) - days), len(history)):
        r = analyze(code, history[:t + 1], ref, args)
        out.append(r["分數"] if r else None)
    return out


# ---------------------------------------------------------------- 輸出

def display_width(s):
    return sum(2 if ord(c) > 0x2E80 else 1 for c in s)


def pad(s, width, right=False):
    s = "" if s is None else str(s)
    space = " " * max(width - display_width(s), 0)
    return space + s if right else s + space


def print_table(rows):
    cols = [("代號", 6, False), ("名稱", 10, False), ("市場", 5, False), ("收盤", 8, True),
            ("漲跌%", 7, True), ("5日%", 7, True), ("量比", 6, True), ("股本(億)", 9, True),
            ("分數", 5, True), ("訊號", 0, False)]
    print("  ".join(pad(c, w, r) for c, w, r in cols))
    print("-" * 110)
    for row in rows:
        print("  ".join(pad(row[c], w, r) for c, w, r in cols))


def screen(end, args, log=True):
    """執行篩選，網頁版與命令列共用。

    回傳 dict：trade_date、results、history、ref、live（今天官方資料未公布、改用即時行情時）。
    args.market_data 為真時另外回傳：
      insti：與 history 對齊的三大法人買賣超 list（尚未公布的日子為 None）
      market：與 history 對齊的大盤狀態 list（見 market_state）
    """
    if log:
        print(f"讀取行情資料（首次執行需下載約 {args.lookback} 個交易日，約需數分鐘，之後會使用快取）…",
              file=sys.stderr)
    history = load_history(end, args.lookback)
    if not history:
        raise RuntimeError("找不到行情資料")

    # 今天的官方收盤資料還沒公布（盤中或剛收盤），改用 MIS 即時行情當作今天
    live = None
    today = dt.date.today()
    if (getattr(args, "live", False) and end == today and history[-1][0] < today
            and today.weekday() < 5 and dt.datetime.now().time() >= dt.time(9, 0)):
        if log:
            print("讀取今日即時行情（約 40 秒）…", file=sys.stderr)
        snap = live_snapshot(history[-1][1], today)
        if snap["rows"]:
            history = history[1:] + [(today, snap["rows"])]
            live = {"time": snap["time"], "final": snap["final"]}
    trade_date = history[-1][0]
    if log:
        print("讀取股本、注意股、處置股…", file=sys.stderr)
    ref = load_reference(dt.date.today())

    results = []
    for code in history[-1][1]:
        if not is_common_stock(code):
            continue
        r = analyze(code, history, ref, args)
        if r and r["分數"] >= args.min_score:
            results.append(r)
    results.sort(key=lambda r: (r["分數"], r["量比"] or 0), reverse=True)
    if getattr(args, "streaks", False):
        for r in results:
            r["分數歷史"] = score_history(r["代號"], history, ref, args)
    res = {"trade_date": trade_date, "results": results, "history": history, "ref": ref, "live": live}

    if getattr(args, "market_data", False):
        if log:
            print("讀取加權指數與三大法人買賣超…", file=sys.stderr)
        dates = [d for d, _ in history]
        taiex = load_taiex(dates)
        # 盤中或官方收盤指數尚未公布：用即時指數判斷今天的大盤（不寫入快取）
        last_ymd = dates[-1].strftime("%Y%m%d")
        live_index = False
        if last_ymd not in taiex and dates[-1] == dt.date.today():
            try:
                ymd, z = taiex_live()
                if ymd == last_ymd:
                    taiex = {**taiex, last_ymd: z}
                    live_index = True
            except Exception as e:
                print(f"  即時加權指數讀取失敗：{e}", file=sys.stderr)
        res["market"] = [market_state(taiex, d) for d in dates]
        if live_index and res["market"][-1]:
            res["market"][-1]["live"] = True
        res["insti"] = []
        for d in dates:
            try:
                res["insti"].append(load_insti(d))
            except Exception as e:  # 單日法人資料抓不到不影響其他功能
                print(f"  {d} 法人資料讀取失敗：{e}", file=sys.stderr)
                res["insti"].append(None)
    return res


def main():
    p = argparse.ArgumentParser(description="台股妖股篩選系統")
    p.add_argument("--date", help="篩選日期 YYYYMMDD（預設：最近交易日）")
    p.add_argument("--min-score", type=int, default=6, help="最低分數（預設 6）")
    p.add_argument("--top", type=int, default=50, help="最多顯示幾檔（預設 50）")
    p.add_argument("--min-volume", type=float, default=500, help="最低成交張數（預設 500）")
    p.add_argument("--small-cap", type=float, default=10, help="小股本門檻，單位億元（預設 10）")
    p.add_argument("--lookback", type=int, default=LOOKBACK, help=f"抓取交易日數（預設 {LOOKBACK}）")
    p.add_argument("--live", action="store_true", help="今天官方資料未公布時，改用即時行情")
    args = p.parse_args()

    end = dt.datetime.strptime(args.date, "%Y%m%d").date() if args.date else dt.date.today()

    try:
        res = screen(end, args)
        trade_date, results = res["trade_date"], res["results"]
        if res["live"]:
            print(f"（今日資料來自即時行情 {res['live']['time']}）", file=sys.stderr)
    except RuntimeError as e:
        sys.exit(str(e))

    print(f"\n台股妖股篩選結果　交易日 {trade_date}　分數 ≥ {args.min_score}　共 {len(results)} 檔\n")
    print_table(results[:args.top])

    if results:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        out = os.path.join(OUTPUT_DIR, f"yaogu_{trade_date.strftime('%Y%m%d')}.csv")
        with open(out, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=list(results[0].keys()))
            w.writeheader()
            w.writerows(results)
        print(f"\n完整結果已存到 {out}")
    print("\n※ 僅供研究參考，不構成投資建議。妖股波動極大，請自行控管風險。")


if __name__ == "__main__":
    main()
