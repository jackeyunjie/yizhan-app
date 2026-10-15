#!/bin/bash
# 忆栈 sync 守护安装：launchd LaunchAgent，每 30 分钟跑一次 yizhan sync
set -euo pipefail

LABEL="com.yizhan.sync"
VAULT="/Users/lv111101/群情报/知识库"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

mkdir -p "$VAULT/.yizhan" "$HOME/Library/LaunchAgents"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$ROOT/yizhan</string>
    <string>sync</string>
    <string>--vault</string>
    <string>$VAULT</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/usr/bin:/bin:/opt/homebrew/bin</string>
  </dict>
  <key>StartInterval</key>
  <integer>1800</integer>
  <key>RunAtLoad</key>
  <false/>
  <key>StandardOutPath</key>
  <string>$VAULT/.yizhan/sync.log</string>
  <key>StandardErrorPath</key>
  <string>$VAULT/.yizhan/sync.log</string>
</dict>
</plist>
EOF
echo "已写入 $PLIST"

launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "已加载 ${LABEL}（StartInterval=1800，RunAtLoad=false）"

echo "--- 立即跑一次 sync 验证 ---"
"$ROOT/yizhan" sync --vault "$VAULT" >> "$VAULT/.yizhan/sync.log" 2>&1
echo "--- sync.log 末尾 ---"
tail -20 "$VAULT/.yizhan/sync.log"
