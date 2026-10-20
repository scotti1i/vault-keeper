#!/usr/bin/env bash
# vault-keeper 一键安装：装 skill → 找到本机 Obsidian 库 → 收编 → git → 钩子 → 定时 → 体检
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/scotti1i/vault-keeper/main/install.sh)"
# 可加参数透传给 vk setup，例如：... install.sh)" -- --vault ~/Notes --create-remote
set -euo pipefail

REPO="${VK_REPO:-https://github.com/scotti1i/vault-keeper}"
LIB="${VK_LIB:-$HOME/.agents/skills/vault-keeper}"

say(){ printf '\033[1m%s\033[0m\n' "$*"; }

command -v python3 >/dev/null || { echo "需要 python3（macOS 自带；Linux: apt install python3）"; exit 1; }
command -v git >/dev/null || { echo "需要 git（macOS 执行 xcode-select --install）"; exit 1; }

say "1/2 安装 skill 到 $LIB"
if [ -d "$LIB/.git" ]; then
  git -C "$LIB" pull -q --ff-only || echo "（已有安装，更新失败，继续用现有版本）"
elif [ -d "$LIB" ]; then
  echo "（$LIB 已存在且不是 git 安装，跳过下载；想更新请 npx skills add scotti1i/vault-keeper）"
else
  mkdir -p "$(dirname "$LIB")"
  git clone -q --depth 1 "$REPO" "$LIB"
fi

say "2/2 收编本机的库"
exec python3 "$LIB/scripts/vk.py" setup "$@"
