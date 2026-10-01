#!/usr/bin/env python3
"""把明日強勢候選傳到 LINE（LINE 官方帳號 Messaging API 推播）。

需要環境變數：
  LINE_CHANNEL_ACCESS_TOKEN  Messaging API 的 Channel access token（長期）
  LINE_USER_ID               接收者的 User ID（U 開頭 33 碼）

  python3 notify_line.py            # 讀取 site/data/latest.json 並發送
  python3 notify_line.py --dry-run  # 只印出訊息內容，不發送
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


def build_message(data):
    picks = data["picks"]
    lines = [f"📈 明日強勢候選｜{data['trade_date']}"]
    if not picks:
        lines += ["", "今天沒有符合條件的股票。", "條件刻意設得嚴格，沒有好機會時寧可空手。"]
    for i, p in enumerate(picks[:MAX_PICKS], 1):
        lines += [
            "",
            f"#{i} {p['名稱']} {p['代號']}（{p['市場']}）",
            f"收盤 {p['收盤']:g}（{p['漲跌%']:+.2f}%）強度 {p['強度']}",
            "【理由】",
            *[f"・{r}" for r in p["理由"][:3]],
            "【操作】",
            f"・{p['計畫']['entry']}",
            f"・{p['計畫']['stop']}",
            "【風險】",
            *[f"・{r}" for r in p["風險"][:2]],
        ]
    if len(picks) > MAX_PICKS:
        lines += ["", f"另有 {len(picks) - MAX_PICKS} 檔，請見網頁。"]

    h = data["backtest"].get("hold3_stop")
    if h:
        lines += ["", f"📊 回測（持有 3 日含停損）：平均 {h['avg']:+.2f}%、勝率 {h['win']}%（{h['trades']} 筆）"]
    lines += [
        "⚠️ 規則選股的觀察名單，非投資建議，請自行控管風險。",
        "",
        f"完整內容與 K 線：{SITE_URL}#picks",
    ]
    return "\n".join(lines)


def push(token, user_id, text):
    body = json.dumps({"to": user_id, "messages": [{"type": "text", "text": text[:5000]}]}).encode()
    req = urllib.request.Request(
        "https://api.line.me/v2/bot/message/push", data=body, method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=30):
            pass
    except urllib.error.HTTPError as e:
        sys.exit(f"LINE 發送失敗：HTTP {e.code} {e.read().decode('utf-8', 'replace')}")


def main():
    p = argparse.ArgumentParser(description="把明日強勢候選傳到 LINE")
    p.add_argument("--dry-run", action="store_true", help="只印出訊息，不發送")
    p.add_argument("--force", action="store_true", help="已發送過仍再發送")
    args = p.parse_args()

    with open(LATEST, encoding="utf-8") as fh:
        data = json.load(fh)
    text = build_message(data)
    if args.dry_run:
        print(text)
        return

    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
    user_id = os.environ.get("LINE_USER_ID")
    if not token or not user_id:
        print("尚未設定 LINE_CHANNEL_ACCESS_TOKEN / LINE_USER_ID，略過 LINE 通知")
        return

    marker = os.path.join(CACHE_DIR, f"line_sent_{data['trade_date'].replace('-', '')}")
    if os.path.exists(marker) and not args.force:
        print(f"{data['trade_date']} 已發送過，略過")
        return

    push(token, user_id, text)
    os.makedirs(CACHE_DIR, exist_ok=True)
    open(marker, "w").close()
    print(f"已傳送 {data['trade_date']} 明日強勢候選（{len(data['picks'])} 檔）到 LINE")


if __name__ == "__main__":
    main()
