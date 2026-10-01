#!/usr/bin/env python3
"""把明日強勢候選傳到 LINE（LINE 官方帳號 Messaging API 推播）。

以 Flex Message 輪播卡片發送：每檔股票一張卡片（價格、進場區間、停損、兩段停利、
理由、風險），最後一張是回測成績。整組卡片是同一則訊息，只算 1 則額度。
若 LINE 不接受卡片格式，會自動改傳純文字版。

需要環境變數：
  LINE_CHANNEL_ACCESS_TOKEN  Messaging API 的 Channel access token（長期）
  LINE_USER_ID               接收者的 User ID（U 開頭 33 碼）

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
    footer += ["⚠️ 規則選股的觀察名單，非投資建議，請自行控管風險。",
               f"完整內容與 K 線：{SITE_URL}#picks"]

    if not picks:
        return ["\n".join([f"📈 明日強勢候選｜{data['trade_date']}", "",
                           "今天沒有符合條件的股票。", "條件刻意設得嚴格，沒有好機會時寧可空手。", "",
                           *footer])]
    texts = []
    for i, p in enumerate(picks, 1):
        plan = p["計畫"]
        lines = [
            f"📈 明日強勢候選｜{data['trade_date']}（{i}/{len(picks)}）", "",
            f"{p['名稱']} {p['代號']}（{p['市場']}）",
            f"收盤 {p['收盤']:g}（{p['漲跌%']:+.2f}%）強度 {p['強度']}", "",
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


def stock_bubble(p, i, n, trade_date):
    lv = p["計畫"]["levels"]
    c = p["收盤"]
    hi_color = "#FF6B6B" if p["漲跌%"] > 0 else "#5BD58A"  # 深色標題列上的漲跌色
    return {
        "type": "bubble", "size": "mega",
        "header": {
            "type": "box", "layout": "vertical", "backgroundColor": INK, "paddingAll": "16px", "spacing": "xs",
            "contents": [
                text(f"明日強勢候選 · {trade_date[5:].replace('-', '/')} · {i}/{n}", size="xxs", color="#BDB8AE"),
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
            text(f"明日強勢候選 · {data['trade_date']}", size="xs", color=MUTED),
            text("今天沒有符合條件的股票", size="lg", weight="bold", color=INK, wrap=True),
            text("條件刻意設得嚴格，沒有好機會時寧可空手。", size="xs", color=MUTED, wrap=True),
            {"type": "separator"},
        ]
        return {"type": "flex", "altText": f"明日強勢候選 {data['trade_date']}：今天沒有符合條件的股票",
                "contents": bubble}
    names = "、".join(p["名稱"] for p in picks)
    bubbles = [stock_bubble(p, i, len(picks), data["trade_date"]) for i, p in enumerate(picks, 1)]
    bubbles.append(summary_bubble(data))
    return {"type": "flex", "altText": f"📈 明日強勢候選 {data['trade_date']}：{names}"[:400],
            "contents": {"type": "carousel", "contents": bubbles}}


# ---------------------------------------------------------------- 發送

def push(token, user_id, messages):
    """成功回傳 None，失敗回傳 (HTTP 狀態碼, 錯誤內容)。"""
    body = json.dumps({"to": user_id, "messages": messages}).encode()
    req = urllib.request.Request(
        "https://api.line.me/v2/bot/message/push", data=body, method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=30):
            return None
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def main():
    p = argparse.ArgumentParser(description="把明日強勢候選傳到 LINE")
    p.add_argument("--dry-run", action="store_true", help="印出內容，不發送")
    p.add_argument("--force", action="store_true", help="已發送過仍再發送")
    args = p.parse_args()

    with open(LATEST, encoding="utf-8") as fh:
        data = json.load(fh)
    texts = build_texts(data)
    flex = build_flex(data)
    if args.dry_run:
        print("\n\n========== 下一個對話框 ==========\n\n".join(texts))
        print("\n\n========== Flex JSON ==========\n")
        print(json.dumps(flex, ensure_ascii=False, indent=1))
        return

    # 去掉貼上時可能多出的空白與換行
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
    user_id = os.environ.get("LINE_USER_ID", "").strip()
    if not token or not user_id:
        print("尚未設定 LINE_CHANNEL_ACCESS_TOKEN / LINE_USER_ID，略過 LINE 通知")
        return

    marker = os.path.join(CACHE_DIR, f"line_sent_{data['trade_date'].replace('-', '')}")
    if os.path.exists(marker) and not args.force:
        print(f"{data['trade_date']} 已發送過，略過")
        return

    err = push(token, user_id, [flex])
    if err and err[0] == 400:
        # 卡片格式被拒絕時改傳純文字，確保一定收得到
        print(f"Flex 卡片被拒絕（{err[1]}），改傳純文字")
        err = push(token, user_id, [{"type": "text", "text": t[:5000]} for t in texts[:5]])
    if err:
        sys.exit(f"LINE 發送失敗：HTTP {err[0]} {err[1]}")

    os.makedirs(CACHE_DIR, exist_ok=True)
    open(marker, "w").close()
    print(f"已傳送 {data['trade_date']} 明日強勢候選（{len(data['picks'])} 檔）到 LINE")


if __name__ == "__main__":
    main()
