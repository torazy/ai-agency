#!/usr/bin/env bash
# Hermes Agent 用ポインタスキルのインストーラ
# .agents/skills/（正典）の frontmatter を転写したポインタを ~/.hermes/skills/ に生成する。
# 本体アップデートでスキルが増減したら再実行する（上書き安全・冪等）。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DEST="${HERMES_SKILLS_DIR:-$HOME/.hermes/skills}"

count=0
for dir in "$REPO_ROOT"/.agents/skills/*/; do
  name=$(basename "$dir")
  src="$dir/SKILL.md"
  [ -f "$src" ] || continue
  mkdir -p "$DEST/$name"
  # frontmatter（最初の --- から次の --- まで）をそのまま転写＝起動トリガーを正典と一致させる
  awk 'NR==1 && /^---/{fm=1; print; next} fm==1{print; if(/^---/){exit}}' "$src" > "$DEST/$name/SKILL.md"
  cat >> "$DEST/$name/SKILL.md" <<EOF

# $name（ポインタ / ai-manager 専用）

このスキルの手順の正典は、作業ディレクトリ（ai-manager インスタンスのルート）の
\`.agents/skills/$name/SKILL.md\` にある。それを読み、手順を忠実に実行すること。
手順を編集する場合も正典側を直す。

⚠️ 作業ディレクトリに \`.agents/skills/$name/SKILL.md\` が存在しない場合、
ここは ai-manager ワークスペースではないので、このスキルを適用しない。
EOF
  count=$((count + 1))
done

echo "installed $count skills to $DEST"
