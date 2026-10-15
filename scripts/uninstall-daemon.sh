#!/bin/bash
# 忆栈 sync 守护卸载：unload + 删 plist（不动日志与知识库）
set -euo pipefail

LABEL="com.yizhan.sync"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || launchctl unload "$PLIST" 2>/dev/null || true
rm -f "$PLIST"
echo "已卸载 $LABEL 并删除 $PLIST"
