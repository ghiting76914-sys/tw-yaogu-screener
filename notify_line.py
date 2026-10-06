#!/usr/bin/env python3
"""把明日強勢候選傳到 LINE（LINE 官方帳號 Messaging API 推播）。

以 Flex Message 輪播卡片發送：每檔股票一張卡片（價格、進場區間、停損、兩段停利、
理由、風險），最後一張是回測成績。整組卡片是同一則訊息，只算 1 則額度。
若 LINE 不接受卡片格式，會自動改傳純文字版。

需要環境變數：
  LINE_CHANNEL_ACCESS_TOKEN  Messaging API 的 Channel access token（長期）
  LINE_SEND_TO               all（預設）：群發給所有加官方帳號為好友的人
                             me：只傳給 LINE_USER_ID
  LINE_USER_ID               LINE_SEND_TO=me 時的接收者 User ID（U 開頭 33 碼）

額度：免費方案每月 200 則，群發時依人數計算（每天 1 次推播 × 好友人數）。

  python3 notify_line.py            # 讀取 site/data/latest.json 並發送
  python3 notify_line.py --dry-run  # 印出純文字版與卡片 JSON，不發送
  python3 notify_line.py --force    # 同一交易日已發送過仍再發送

同一個交易日只會發送一次（記錄在 data/cache/line_sent_YYYYMMDD），
所以每天兩次的排程不會重複通知。
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LATEST = os.path.join(BASE_DIR, "site", "data", "latest.json")
CACHE_DIR = os.path.join(BASE_DIR, "data", "cache")
SITE_URL = os.environ.get("SITE_URL", "https://ghiting76914-sys.github.io/tw-yaogu-screener/")
MAX_PICKS = 5

# 明日強勢候選依訊號日是否鎖漲停的回測（main() 從資料填入）
BY_LOCK = None

# 卡片標題；盤中預覽時由 main() 改成「盤中預覽 HH:MM」
TITLE, ICON = "明日強勢候選", "📈"
PREVIEW_NOTE = "盤中預覽：成交量已換算成全天預估、尚未排除法人賣超，名單會隨收盤變動；正式名單收盤後推播。"

UP = "#D1242F"      # 台股紅漲
DOWN = "#1A7F37"    # 綠跌
ACCENT = "#C2410C"
INK = "#1C1B19"
MUTED = "#8A867E"
SOFT = "#F4F2EE"


def rel(x, base):
    return f"{(x / base - 1) * 100:+.1f}%"


def backtest_line(data):
    h = data["backtest"].get("plan")
    if not h:
        return None
    return f"回測（照操作計畫）：平均 {h['avg']:+.2f}%、勝率 {h['win']}%（{h['trades']} 筆）"


# ---------------------------------------------------------------- 純文字版（備援）

def build_texts(data):
    picks = data["picks"][:MAX_PICKS]
    footer = []
    if len(data["picks"]) > MAX_PICKS:
        footer.append(f"另有 {len(data['picks']) - MAX_PICKS} 檔，請見網頁。")
    if backtest_line(data):
        footer.append("📊 " + backtest_line(data))
    if TITLE != "明日強勢候選":
        footer.append("⏰ " + PREVIEW_NOTE)
    footer += ["⚠️ 規則選股的觀察名單，非投資建議，請自行控管風險。",
               f"完整內容與 K 線：{SITE_URL}#picks"]

    if not picks:
        return ["\n".join([f"{ICON} {TITLE}｜{data['trade_date']}", "",
                           "今天沒有符合條件的股票。", "條件刻意設得嚴格，沒有好機會時寧可空手。", "",
                           *footer])]
    texts = []
    for i, p in enumerate(picks, 1):
        plan = p["計畫"]
        lines = [
            f"{ICON} {TITLE}｜{data['trade_date']}（{i}/{len(picks)}）", "",
            f"{p['名稱']} {p['代號']}（{p['市場']}）",
            f"{'收盤' if TITLE == '明日強勢候選' else '現價'} {p['收盤']:g}（{p['漲跌%']:+.2f}%）強度 {p['強度']}",
            "今日新上榜" if p.get("連續上榜", 1) <= 1 else f"連續上榜 {p['連續上榜']} 天", "",
            "【操作】",
            f"・進場：{plan['entry']}",
            f"・停損：{plan['stop']}",
            f"・目標一：{plan['tp1']}",
            f"・目標二：{plan['tp2']}",
            f"・期限：{plan['exit']}", "",
            "【理由】", *[f"・{r}" for r in p["理由"]], "",
            "【風險】", *[f"・{r}" for r in p["風險"]],
        ]
        if i == len(picks):
            lines += ["", "──────────", *footer]
        texts.append("\n".join(lines))
    return texts


# ---------------------------------------------------------------- Flex 卡片

def text(t, **kw):
    return {"type": "text", "text": str(t), **kw}


def level_row(label, value, note, color, note_color=None):
    return {
        "type": "box", "layout": "horizontal", "spacing": "sm", "paddingAll": "8px",
        "contents": [
            text(label, size="sm", color=MUTED, flex=3, gravity="center"),
            text(value, size="md", weight="bold", color=color, flex=4, align="end", gravity="center"),
            text(note, size="xs", color=note_color or color, flex=3, align="end", gravity="center"),
        ],
    }


def bullets(items, mark, color):
    return [{"type": "box", "layout": "baseline", "spacing": "sm", "contents": [
        text(mark, size="xs", color=color, flex=0),
        text(it, size="xs", color=INK, wrap=True, flex=1),
    ]} for it in items]


def lock_line(p):
    if BY_LOCK is None or p.get("鎖漲停") is None:
        return ""
    L = BY_LOCK
    if p["鎖漲停"]:
        on, ch = L["locked"]["overnight"], L["locked"]["chase"]
        return (f"🔒 今天收盤鎖漲停：機會在今天收盤前（隔日沖回測 {on['avg']:+.2f}%）"
                + ("，13:00 已列入隔日沖名單" if p.get("隔日沖名單") else "")
                + f"。明天開盤追價回測 {ch['avg']:+.2f}%，不建議追。") if on and ch else ""
    pl = L["unlocked"]["plan"]
    return f"今天沒有鎖漲停：照計畫進場回測 {pl['avg']:+.2f}%（勝率 {pl['win']}%），僅供觀察。" if pl else ""


def top3_bubble(data):
    """明日精選 3 檔（營收動能＋突破 60 日新高，持有 20 天）。"""
    rev = data.get("revenue") or {}
    T, R = rev.get("top3") or {}, rev.get("top3_research") or {}
    if not T.get("picks"):
        return None
    rows = []
    for p in T["picks"]:
        rows += [
            {"type": "box", "layout": "baseline", "spacing": "sm", "contents": [
                text(f"#{p['排名']}", size="sm", weight="bold", color=UP, flex=1),
                text(f"{p['名稱']} {p['代號']}", size="md", weight="bold", color=INK, flex=6),
                text(f"{p['收盤']:g}", size="sm", color=INK, flex=3, align="end"),
            ]},
            text(" · ".join(p["理由"][:2]), size="xxs", color=MUTED, wrap=True),
        ]
    tr, te = R.get("train", {}), R.get("test", {})
    return {
        "type": "bubble", "size": "mega",
        "header": {"type": "box", "layout": "vertical", "backgroundColor": UP, "paddingAll": "16px", "spacing": "xs",
                   "contents": [text(f"明日精選 · {T['date'][5:].replace('-', '/')}", size="xxs", color="#FFE4E1"),
                                text("🎯 明天開盤買進 3 檔", size="lg", weight="bold", color="#FFFFFF"),
                                text(f"持有 {T['hold']} 個交易日（約一個月）", size="xs", color="#FFE4E1")]},
        "body": {"type": "box", "layout": "vertical", "spacing": "md", "paddingAll": "16px", "contents": [
            text("營收創 12 個月新高、年增 > 20%、站上季線，今天收盤突破 60 日新高，依成交值前 3。",
                 size="xs", color=MUTED, wrap=True),
            {"type": "separator"}, *rows, {"type": "separator"},
            text(f"📊 11 年回測：持有 20 天平均 {tr.get('avg', 0):+.2f}%～{te.get('avg', 0):+.2f}%，"
                 f"{R.get('years', '')} 年都贏過大盤；個股勝率約一半，請分散並控制資金。",
                 size="xxs", color=MUTED, wrap=True),
        ]},
        "footer": {"type": "box", "layout": "vertical", "paddingAll": "12px", "contents": [
            {"type": "button", "style": "primary", "color": UP, "height": "sm",
             "action": {"type": "uri", "label": "看精選理由與 K 線", "uri": SITE_URL}}]},
    }


def stock_bubble(p, i, n, trade_date):
    lv = p["計畫"]["levels"]
    c = p["收盤"]
    hi_color = "#FF6B6B" if p["漲跌%"] > 0 else "#5BD58A"  # 深色標題列上的漲跌色
    return {
        "type": "bubble", "size": "mega",
        "header": {
            "type": "box", "layout": "vertical", "backgroundColor": INK, "paddingAll": "16px", "spacing": "xs",
            "contents": [
                text(f"{TITLE} · {trade_date[5:].replace('-', '/')} · {i}/{n} · "
                     + ("今日新上榜" if p.get("連續上榜", 1) <= 1 else f"連續上榜 {p['連續上榜']} 天"),
                     size="xxs", color="#BDB8AE"),
                {"type": "box", "layout": "baseline", "spacing": "sm", "contents": [
                    text(p["名稱"], size="xl", weight="bold", color="#FFFFFF", flex=0),
                    text(f"{p['代號']} {p['市場']}", size="sm", color="#BDB8AE", flex=0),
                ]},
                {"type": "box", "layout": "baseline", "spacing": "sm", "contents": [
                    text(f"{c:g}", size="xxl", weight="bold", color=hi_color, flex=0),
                    text(f"{p['漲跌%']:+.2f}%", size="md", weight="bold", color=hi_color, flex=0),
                    text(f"強度 {p['強度']}", size="sm", color="#FDBA74", align="end"),
                ]},
            ],
        },
        "body": {
            "type": "box", "layout": "vertical", "spacing": "md", "paddingAll": "16px",
            "contents": [
                {"type": "box", "layout": "vertical", "backgroundColor": SOFT, "cornerRadius": "10px",
                 "paddingAll": "4px", "contents": [
                     level_row("進場區間", f"{lv['entry_low']:g}～{lv['entry_high']:g}",
                               f"{rel(lv['entry_low'], c)}～{rel(lv['entry_high'], c)}", INK, MUTED),
                     level_row("停損", f"{lv['stop']:g}", rel(lv["stop"], c), DOWN),
                     level_row("目標一", f"{lv['tp1']:g}", f"{rel(lv['tp1'], c)} 賣半", UP),
                     level_row("目標二", f"{lv['tp2']:g}", f"{rel(lv['tp2'], c)} 全出", UP),
                 ]},
                text("開盤在進場區間才買，盤中跌破停損立即出場；到目標一後停損移到成本價，最多持有 3 天。",
                     size="xxs", color=MUTED, wrap=True),
                *([text(lock_line(p), size="xs", weight="bold", wrap=True,
                        color=ACCENT if p.get("鎖漲停") else MUTED)] if lock_line(p) else []),
                {"type": "separator"},
                text("為什麼入選", size="sm", weight="bold", color=UP),
                *bullets(p["理由"][:4], "✓", UP),
                text("風險", size="sm", weight="bold", color=ACCENT),
                *bullets(p["風險"][:2], "!", ACCENT),
            ],
        },
        "footer": {
            "type": "box", "layout": "vertical", "spacing": "sm", "paddingAll": "12px",
            "contents": [
                {"type": "button", "style": "primary", "color": ACCENT, "height": "sm",
                 "action": {"type": "uri", "label": "看 K 線與完整分析", "uri": f"{SITE_URL}#picks"}},
                {"type": "button", "style": "link", "height": "sm",
                 "action": {"type": "uri", "label": "Yahoo 股市即時報價",
                            "uri": f"https://tw.stock.yahoo.com/quote/{p['代號']}"}},
            ],
        },
    }


def stat(label, value, color):
    return {"type": "box", "layout": "vertical", "flex": 1, "contents": [
        text(label, size="xxs", color=MUTED),
        text(value, size="lg", weight="bold", color=color)]}


def summary_bubble(data):
    bt = data["backtest"]
    h = bt.get("plan")
    contents = [text("📊 這套規則的回測成績", size="md", weight="bold", color=INK)]
    if h:
        contents += [
            text(f"{bt['from']} ～ {bt['to']}，共 {h['trades']} 筆交易，"
                 f"{'已排除法人賣超、' if bt.get('insti_filter') else ''}已扣手續費與證交稅",
                 size="xxs", color=MUTED, wrap=True),
            {"type": "box", "layout": "horizontal", "contents": [
                stat("平均報酬", f"{h['avg']:+.2f}%", UP if h["avg"] > 0 else DOWN),
                stat("勝率", f"{h['win']}%", INK),
                stat("最差一筆", f"{h['worst']:+.1f}%", DOWN),
            ]},
            {"type": "separator"},
            text("勝率不到一半，獲利靠少數大漲的股票，一定要嚴守停損。" if h["win"] < 50
                 else "回測期間短，不代表未來表現。", size="xs", color=INK, wrap=True),
        ]
    excluded = data.get("insti_excluded") or []
    if excluded:
        contents.append(text(f"已排除三大法人賣超：{'、'.join(excluded)}", size="xs", color=MUTED, wrap=True))
    more = len(data["picks"]) - MAX_PICKS
    if more > 0:
        contents.append(text(f"另有 {more} 檔候選，請見網頁。", size="xs", color=ACCENT, wrap=True))
    if TITLE != "明日強勢候選":
        contents.append(text("⏰ " + PREVIEW_NOTE, size="xs", color=ACCENT, wrap=True))
    contents.append(text("⚠️ 規則選股的觀察名單，非投資建議，請自行控管風險。", size="xxs", color=MUTED, wrap=True))
    return {
        "type": "bubble", "size": "mega",
        "body": {"type": "box", "layout": "vertical", "spacing": "md", "paddingAll": "18px", "contents": contents},
        "footer": {
            "type": "box", "layout": "vertical", "paddingAll": "12px",
            "contents": [{"type": "button", "style": "secondary", "height": "sm",
                          "action": {"type": "uri", "label": "打開妖股雷達", "uri": SITE_URL}}],
        },
    }


def build_flex(data):
    picks = data["picks"][:MAX_PICKS]
    if not picks:
        bubble = summary_bubble(data)
        bubble["body"]["contents"][:0] = [
            text(f"{TITLE} · {data['trade_date']}", size="xs", color=MUTED),
            text("今天沒有符合條件的股票", size="lg", weight="bold", color=INK, wrap=True),
            text("條件刻意設得嚴格，沒有好機會時寧可空手。", size="xs", color=MUTED, wrap=True),
            {"type": "separator"},
        ]
        top = top3_bubble(data) if TITLE == "明日強勢候選" else None
        if top:
            return {"type": "flex", "altText": f"🎯 明日精選 {data['trade_date']}｜明日強勢候選：今天沒有符合條件的股票",
                    "contents": {"type": "carousel", "contents": [top, bubble]}}
        return {"type": "flex", "altText": f"{TITLE} {data['trade_date']}：目前沒有符合條件的股票",
                "contents": bubble}
    names = "、".join(p["名稱"] for p in picks)
    bubbles = [stock_bubble(p, i, len(picks), data["trade_date"]) for i, p in enumerate(picks, 1)]
    bubbles.append(summary_bubble(data))
    top = top3_bubble(data) if TITLE == "明日強勢候選" else None
    if top:
        bubbles.insert(0, top)
        names = "精選 " + "、".join(p["名稱"] for p in data["revenue"]["top3"]["picks"]) + "｜候選 " + names
    return {"type": "flex", "altText": f"{ICON} {TITLE} {data['trade_date']}：{names}"[:400],
            "contents": {"type": "carousel", "contents": bubbles}}


# ---------------------------------------------------------------- 發送

def push(token, user_id, messages):
    """user_id 為 None 時群發給所有好友。成功回傳 None，失敗回傳 (HTTP 狀態碼, 錯誤內容)。"""
    if user_id:
        url, payload = "https://api.line.me/v2/bot/message/push", {"to": user_id, "messages": messages}
    else:
        url, payload = "https://api.line.me/v2/bot/message/broadcast", {"messages": messages}
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=30):
            return None
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def quota_report(token):
    """本月已用的訊息額度（群發依人數計算）。查不到時回傳 None。"""
    def get(path):
        req = urllib.request.Request(f"https://api.line.me/v2/bot/message/{path}",
                                     headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    try:
        limit = get("quota")
        used = get("quota/consumption")["totalUsage"]
        cap = limit.get("value") if limit.get("type") == "limited" else None
        return f"本月已用 {used} 則" + (f"／上限 {cap} 則（剩 {cap - used} 則）" if cap else "（無上限）")
    except Exception as e:
        return f"查詢額度失敗：{e}"


# ---------------------------------------------------------------- 隔日沖（13:00 盤中推播）

def build_overnight_flex(data):
    picks = data.get("overnight") or []
    live = data.get("live") or {}
    hhmm = (live.get("time") or "")[:5]
    bt = (data.get("overnight_backtest") or {}).get("plan")
    rows = []
    for i, p in enumerate(picks, 1):
        rows.append({"type": "box", "layout": "horizontal", "spacing": "sm", "contents": [
            text(f"{i}", size="xs", color=MUTED, flex=1),
            text(f"{p['名稱']} {p['代號']}", size="sm", color=INK, flex=6),
            text(f"{p['漲跌%']:+.1f}%", size="sm", weight="bold", color=UP, flex=3, align="end"),
            text(f"{p['現價']:g}", size="xs", color=MUTED, flex=3, align="end"),
            text("漲停" if p["已漲停"] else "—", size="xxs", color=ACCENT if p["已漲停"] else MUTED, flex=2, align="end"),
        ]})
    body = [
        text("收盤前買進、明天開盤一律賣出。已漲停的股票委買排隊中，可能買不到；"
             "收盤若沒鎖住漲停，隔天開盤平均是虧損。", size="xs", color=MUTED, wrap=True),
        {"type": "separator"},
    ]
    if rows:
        body += [{"type": "box", "layout": "horizontal", "contents": [
            text("#", size="xxs", color=MUTED, flex=1), text("股票", size="xxs", color=MUTED, flex=6),
            text("漲幅", size="xxs", color=MUTED, flex=3, align="end"), text("現價", size="xxs", color=MUTED, flex=3, align="end"),
            text("狀態", size="xxs", color=MUTED, flex=2, align="end")]}, *rows]
    else:
        body.append(text("目前沒有符合隔日沖條件的股票。", size="sm", color=INK, wrap=True))
    body += [{"type": "separator"},
             text((f"📊 2 年回測：勝率 {bt['win']}%、每筆平均 {bt['avg']:+.2f}%（{bt['trades']} 筆，已扣成本）。" if bt else "")
                  + "優勢存在但不大，請控制資金。", size="xxs", color=MUTED, wrap=True),
             text("⚠️ 規則選股的觀察名單，非投資建議。", size="xxs", color=MUTED, wrap=True)]
    names = "、".join(p["名稱"] for p in picks[:5]) or "目前沒有符合條件的股票"
    return {"type": "flex", "altText": f"⚡ 隔日沖候選 {hhmm}：{names}"[:400], "contents": {
        "type": "bubble", "size": "giga",
        "header": {"type": "box", "layout": "vertical", "backgroundColor": INK, "paddingAll": "16px", "spacing": "xs",
                   "contents": [text(f"隔日沖候選 · {data['trade_date'][5:].replace('-', '/')} {hhmm} 盤中", size="xxs", color="#BDB8AE"),
                                text("⚡ 收盤前買、明天開盤賣", size="lg", weight="bold", color="#FFFFFF"),
                                text("盤中漲到 +7% 以上、接近或突破 60 日高", size="xs", color="#FDBA74")]},
        "body": {"type": "box", "layout": "vertical", "spacing": "sm", "paddingAll": "16px", "contents": body},
        "footer": {"type": "box", "layout": "vertical", "paddingAll": "12px", "contents": [
            {"type": "button", "style": "primary", "color": ACCENT, "height": "sm",
             "action": {"type": "uri", "label": "看完整名單與理由", "uri": f"{SITE_URL}#overnight"}}]},
    }}


def build_overnight_text(data):
    picks = data.get("overnight") or []
    hhmm = ((data.get("live") or {}).get("time") or "")[:5]
    lines = [f"⚡ 隔日沖候選｜{data['trade_date']} {hhmm} 盤中", "收盤前買進、明天開盤一律賣出。", ""]
    lines += [f"{i}. {p['名稱']} {p['代號']}  {p['漲跌%']:+.1f}%  現價 {p['現價']:g}{'（已漲停）' if p['已漲停'] else ''}"
              for i, p in enumerate(picks, 1)] or ["目前沒有符合條件的股票。"]
    lines += ["", f"完整名單：{SITE_URL}#overnight"]
    return "\n".join(lines)


# ---------------------------------------------------------------- 營收動能（每月換股日推播一次）

def revenue_due(data):
    """今天是營收動能換股日，且有名單。"""
    r = data.get("revenue")
    return bool(r and r.get("picks") and r.get("rebalance_date") == data["trade_date"])


def build_revenue_flex(data):
    r = data["revenue"]
    ym = f"{r['rev_month'][:4]}/{r['rev_month'][4:]}"
    rows = []
    for p in r["picks"]:
        rows.append({"type": "box", "layout": "horizontal", "spacing": "sm", "contents": [
            text(f"{p['排名']}", size="xs", color=MUTED, flex=1),
            text(f"{p['名稱']} {p['代號']}", size="sm", color=INK, flex=6, wrap=False),
            text(f"年增 {p['營收年增%']:+.0f}%", size="xs", color=UP, flex=4, align="end"),
            text(f"{p['選股日收盤']:g}", size="xs", color=MUTED, flex=3, align="end"),
        ]})
    R = r["research"]
    return {
        "type": "flex",
        "altText": f"📊 營收動能 {r['rebalance_date']} 換股：" + "、".join(p["名稱"] for p in r["picks"][:5]) + "…",
        "contents": {
            "type": "bubble", "size": "giga",
            "header": {"type": "box", "layout": "vertical", "backgroundColor": INK, "paddingAll": "16px", "spacing": "xs",
                       "contents": [
                           text(f"營收動能 · {r['rebalance_date']} 換股", size="xxs", color="#BDB8AE"),
                           text(f"本月名單（{ym} 營收）", size="lg", weight="bold", color="#FFFFFF"),
                           text(f"{r['qualified']} 檔符合，依成交值取前 {len(r['picks'])} 檔", size="xs", color="#FDBA74"),
                       ]},
            "body": {"type": "box", "layout": "vertical", "spacing": "sm", "paddingAll": "16px", "contents": [
                text("營收創 12 個月新高、年增 > 20%、股價站上季線。明天開盤平均分配資金買進，"
                     "持有約一個月（20 個交易日）到下次換股日。", size="xs", color=MUTED, wrap=True),
                {"type": "separator"},
                {"type": "box", "layout": "horizontal", "contents": [
                    text("#", size="xxs", color=MUTED, flex=1), text("股票", size="xxs", color=MUTED, flex=6),
                    text("營收年增", size="xxs", color=MUTED, flex=4, align="end"),
                    text("今日收盤", size="xxs", color=MUTED, flex=3, align="end")]},
                *rows,
                {"type": "separator"},
                text(f"📊 11 年研究：{R['beat']}/{R['months']} 個月贏過大盤，平均每月多 {R['excess']:+.2f}%；"
                     f"大跌月一樣會跌（最差 {R['worst']}%），請控制資金比例。", size="xxs", color=MUTED, wrap=True),
            ]},
            "footer": {"type": "box", "layout": "vertical", "paddingAll": "12px", "contents": [
                {"type": "button", "style": "primary", "color": ACCENT, "height": "sm",
                 "action": {"type": "uri", "label": "看完整名單與理由", "uri": f"{SITE_URL}#rev"}}]},
        },
    }


def build_revenue_text(data):
    r = data["revenue"]
    lines = [f"📊 營收動能｜{r['rebalance_date']} 換股（{r['rev_month'][:4]}/{r['rev_month'][4:]} 營收）",
             "營收創 12 個月新高、年增 > 20%、站上季線，依成交值前 20。明天開盤平均買進、持有約一個月。", ""]
    lines += [f"{p['排名']}. {p['名稱']} {p['代號']}  年增 {p['營收年增%']:+.0f}%  收 {p['選股日收盤']:g}" for p in r["picks"]]
    lines += ["", f"完整名單：{SITE_URL}#rev"]
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser(description="把明日強勢候選（與每月營收動能名單）傳到 LINE")
    p.add_argument("--dry-run", action="store_true", help="印出內容，不發送")
    p.add_argument("--force", action="store_true", help="已發送過仍再發送")
    p.add_argument("--revenue-test", action="store_true", help="只發送本期營收動能名單（測試用，不論是否為換股日）")
    args = p.parse_args()

    with open(LATEST, encoding="utf-8") as fh:
        data = json.load(fh)
    global TITLE, ICON, BY_LOCK
    BY_LOCK = (data.get("backtest") or {}).get("by_lock")
    live = data.get("live")
    preview = bool(live and not live.get("final"))
    if preview:
        TITLE, ICON = f"盤中預覽 {live['time'][:5]}", "⏰"
    texts = build_texts(data)
    flex = build_flex(data)
    rev_due = not preview and (revenue_due(data) or (args.revenue_test and bool((data.get("revenue") or {}).get("picks"))))
    if args.dry_run:
        print("\n\n========== 下一個對話框 ==========\n\n".join(texts))
        print("\n\n========== Flex JSON ==========\n")
        print(json.dumps(flex, ensure_ascii=False, indent=1))
        if data.get("revenue"):
            print("\n\n========== 營收動能（換股日才會發送）==========\n")
            print(build_revenue_text(data))
        return

    # 去掉貼上時可能多出的空白與換行
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
    send_to = os.environ.get("LINE_SEND_TO", "all").strip() or "all"
    user_id = os.environ.get("LINE_USER_ID", "").strip() if send_to == "me" else None
    if not token or (send_to == "me" and not user_id):
        print("尚未設定 LINE_CHANNEL_ACCESS_TOKEN（或 LINE_SEND_TO=me 時的 LINE_USER_ID），略過 LINE 通知")
        return

    # 盤中預覽與收盤後的正式名單分開記錄，各自一天只發一次
    marker = os.path.join(CACHE_DIR, f"line_{'preview' if preview else 'sent'}_{data['trade_date'].replace('-', '')}")
    rev_marker = os.path.join(CACHE_DIR, f"line_rev_sent_{data['trade_date'].replace('-', '')}")
    send_picks = not args.revenue_test and (args.force or not os.path.exists(marker))
    send_rev = rev_due and (args.force or args.revenue_test or not os.path.exists(rev_marker))
    if not send_picks and not send_rev:
        print(f"{data['trade_date']} 已發送過，略過")
        return

    # 同一次推播最多 5 則訊息，只算 1 則額度；中午（盤中）推隔日沖，收盤後推明日強勢候選
    main_msg = build_overnight_flex(data) if preview else flex
    messages = ([main_msg] if send_picks else []) + ([build_revenue_flex(data)] if send_rev else [])
    err = push(token, user_id, messages)
    if err and err[0] == 400:
        # 卡片格式被拒絕時改傳純文字，確保一定收得到
        print(f"Flex 卡片被拒絕（{err[1]}），改傳純文字")
        top = (data.get("revenue") or {}).get("top3") or {}
        top_text = [f"🎯 明日精選 3 檔（明天開盤買、持有 {top.get('hold', 20)} 天）\n" + "\n".join(
            f"{p['排名']}. {p['名稱']} {p['代號']}  收 {p['收盤']:g}  營收年增 {p['營收年增%']:+.0f}%" for p in top["picks"])
            ] if top.get("picks") and not preview else []
        main_text = [build_overnight_text(data)] if preview else (top_text + texts)[:4]
        fallback = (main_text if send_picks else []) + ([build_revenue_text(data)] if send_rev else [])
        err = push(token, user_id, [{"type": "text", "text": t[:5000]} for t in fallback])
    if err:
        sys.exit(f"LINE 發送失敗：HTTP {err[0]} {err[1]}")

    os.makedirs(CACHE_DIR, exist_ok=True)
    if send_picks:
        open(marker, "w").close()
        if preview:
            print(f"已傳送 {data['trade_date']} 隔日沖候選（{len(data.get('overnight') or [])} 檔）到 LINE")
        else:
            print(f"已傳送 {data['trade_date']} 明日強勢候選（{len(data['picks'])} 檔）到 LINE")
    if send_rev:
        if not args.revenue_test:  # 測試發送不記錄，換股日當天仍會正式發送
            open(rev_marker, "w").close()
        print(f"已傳送 {data['revenue']['rebalance_date']} 營收動能名單（{len(data['revenue']['picks'])} 檔）到 LINE")
    print(f"發送對象：{'只有自己' if user_id else '所有好友（群發）'}；{quota_report(token)}")


if __name__ == "__main__":
    main()
