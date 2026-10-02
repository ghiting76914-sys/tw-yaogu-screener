#!/bin/bash
# GitHub 排程不可靠時的備案：由這台 Mac 在平日收盤後通知 GitHub 更新網站與 LINE。
# 今天台灣時間 15:00 之後已經有成功或進行中的更新，就不重複觸發。
# 由 ~/Library/LaunchAgents/com.ghiting.yaogu-trigger.plist 在平日 15:45、16:30 執行。
GH="$HOME/.local/bin/gh"
REPO="ghiting76914-sys/tw-yaogu-screener"
LOG="$HOME/tw-yaogu-screener/output/trigger.log"
mkdir -p "$(dirname "$LOG")"

since="$(date -u +%Y-%m-%d)T07:00:00Z"   # 台灣時間 15:00
done_today=$("$GH" run list --repo "$REPO" --workflow update.yml --limit 20 \
  --json createdAt,status,conclusion \
  --jq "[.[] | select(.createdAt >= \"$since\" and (.conclusion == \"success\" or .status != \"completed\"))] | length" 2>>"$LOG")

if [ -z "$done_today" ]; then
  echo "$(date '+%F %T') 無法查詢 GitHub（網路或登入問題）" >> "$LOG"
  exit 1
elif [ "$done_today" -gt 0 ]; then
  echo "$(date '+%F %T') 今天已有更新，略過" >> "$LOG"
else
  "$GH" workflow run update.yml --repo "$REPO" >> "$LOG" 2>&1 \
    && echo "$(date '+%F %T') 已觸發更新" >> "$LOG" \
    || echo "$(date '+%F %T') 觸發失敗" >> "$LOG"
fi
