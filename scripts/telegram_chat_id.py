#!/usr/bin/env python3
"""查詢 Telegram 頻道／群組的 ID（設定 GitHub Secret TELEGRAM_CHAT_ID 用）。

先把機器人加進頻道（設成管理員）或群組，在裡面隨便發一則訊息，再執行：
  python3 scripts/telegram_chat_id.py
程式會請你貼上機器人金鑰（輸入時不會顯示在畫面上，也不會存檔），列出機器人看得到的頻道與群組 ID。
"""
import getpass
import json
import urllib.request

token = getpass.getpass("請貼上 Telegram 機器人金鑰（不會顯示）：").strip()
with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/getUpdates", timeout=30) as r:
    data = json.loads(r.read())
seen = {}
for u in data.get("result", []):
    msg = u.get("channel_post") or u.get("message") or u.get("my_chat_member") or {}
    chat = msg.get("chat") or {}
    if chat.get("id"):
        seen[chat["id"]] = (chat.get("type"), chat.get("title") or chat.get("username") or chat.get("first_name"))
if not seen:
    print("找不到任何頻道或群組。請確認機器人已加入，並在頻道／群組裡發一則新訊息後再執行一次。")
for cid, (kind, title) in seen.items():
    print(f"{cid}\t{kind}\t{title}")
