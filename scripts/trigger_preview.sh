#!/bin/bash
# 13:00 盤中預覽（隔日沖推播）的保險：12:55 之後還沒有任何更新開始，就觸發盤中預覽。
# 主要觸發來源是 cron-job.org（平日 13:00）；由 ~/Library/LaunchAgents/com.ghiting.yaogu-preview.plist 在平日 13:05 執行。
GH="$HOME/.local/bin/gh"
REPO="ghiting76914-sys/tw-yaogu-screener"
LOG="$HOME/tw-yaogu-screener/output/trigger.log"
mkdir -p "$(dirname "$LOG")"
since="$(date -u +%Y-%m-%d)T04:55:00Z"   # 台灣時間 12:55
started=$("$GH" run list --repo "$REPO" --workflow update.yml --limit 10 --json createdAt \
  --jq "[.[] | select(.createdAt >= \"$since\")] | length" 2>>"$LOG")
if [ -z "$started" ]; then
  echo "$(date '+%F %T') 盤中預覽：無法查詢 GitHub" >> "$LOG"
elif [ "$started" -gt 0 ]; then
  echo "$(date '+%F %T') 盤中預覽：已經有更新在 12:55 後開始，略過" >> "$LOG"
else
  "$GH" workflow run update.yml --repo "$REPO" -f preview=true >> "$LOG" 2>&1 \
    && echo "$(date '+%F %T') 盤中預覽：已觸發" >> "$LOG" \
    || echo "$(date '+%F %T') 盤中預覽：觸發失敗" >> "$LOG"
fi
