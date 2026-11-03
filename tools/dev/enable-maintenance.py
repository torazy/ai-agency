#!/usr/bin/env python3
"""
enable-maintenance.py ── 本体（テンプレート）フォルダで「メンテナンス層」を有効化する。

生成・更新するもの（すべて gitignore 済み＝インスタンスには届かない）：
  - .claude/settings.local.json   既存内容を保ったまま、製品を止めるキーを追加
      claudeMdExcludes       : CLAUDE.md / AGENTS.md / GEMINI.md / memory/dashboard.md を読み込まない
      disableAllHooks        : 製品のフック（SessionStart / PreToolUse / PostToolUse）を止める
      skillOverrides         : 製品のスキル・ワークフロー（コマンド）を Claude から見えなくする
      disabledMcpjsonServers : .mcp.json の MCP サーバーを起動しない
  - CLAUDE.local.md               tools/dev/CLAUDE.local.md のコピー（メンテナ向け常時ルール）
  - .claude/skills/maint-*/       tools/dev/skills/ のコピー（メンテ用スキル）
  - .ai-agency-template           本体マーカー（フック無効時は不要だが Gemini CLI 系フック向けに残す）

冪等。再実行すると最新の tools/dev/ の内容で上書きする（settings.local.json の他キーは保持）。
戻すには disable-maintenance.py。
"""

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DEV = ROOT / "tools" / "dev"
LOCAL = ROOT / ".claude" / "settings.local.json"

# claudeMdExcludes は絶対パスに対する glob。ドライブ文字の表記差を避け、フォルダ名で終わるパターンにする
EXCLUDE_FILES = ["CLAUDE.md", "AGENTS.md", "GEMINI.md", "memory/dashboard.md"]


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        sys.exit(f"[enable-maintenance] {path} が壊れています: {e}")


def product_skill_names() -> list[str]:
    names = {p.name for p in (ROOT / ".agents" / "skills").iterdir() if p.is_dir()}
    names |= {p.stem for p in (ROOT / ".agents" / "workflows").glob("*.md")}
    names |= {p.name for p in (ROOT / ".claude" / "skills").iterdir() if p.is_dir() and not p.name.startswith("maint-")}
    names |= {p.stem for p in (ROOT / ".claude" / "commands").glob("*.md")}
    return sorted(names)


def mcp_server_names() -> list[str]:
    cfg = load_json(ROOT / ".mcp.json")
    return sorted(cfg.get("mcpServers", {}).keys())


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    folder = ROOT.name
    settings = load_json(LOCAL)

    excludes = set(settings.get("claudeMdExcludes", []))
    excludes |= {f"**/{folder}/{f}" for f in EXCLUDE_FILES}
    settings["claudeMdExcludes"] = sorted(excludes)

    settings["disableAllHooks"] = True

    overrides = dict(settings.get("skillOverrides", {}))
    for name in product_skill_names():
        overrides[name] = "off"
    settings["skillOverrides"] = dict(sorted(overrides.items()))

    servers = mcp_server_names()
    settings["disabledMcpjsonServers"] = sorted(set(settings.get("disabledMcpjsonServers", [])) | set(servers))
    if "enabledMcpjsonServers" in settings:
        settings["enabledMcpjsonServers"] = [s for s in settings["enabledMcpjsonServers"] if s not in servers]

    settings["$comment_maintenance"] = (
        "本体メンテナンス層（tools/dev/enable-maintenance.py が生成）。製品の CLAUDE.md・フック・スキル・MCP を"
        "この本体フォルダでだけ止める。戻すには tools/dev/disable-maintenance.py。"
    )

    LOCAL.parent.mkdir(parents=True, exist_ok=True)
    LOCAL.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    shutil.copyfile(DEV / "CLAUDE.local.md", ROOT / "CLAUDE.local.md")

    copied = []
    for src in (DEV / "skills").iterdir():
        if src.is_dir() and src.name.startswith("maint-"):
            dst = ROOT / ".claude" / "skills" / src.name
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
            copied.append(src.name)

    marker = ROOT / ".ai-agency-template"
    if not marker.exists():
        marker.write_text("このリポジトリは ai-agency 本体（テンプレート）。記憶を蓄積しない。tools/dev/README.md 参照。\n", encoding="utf-8")

    print("[enable-maintenance] 有効化しました（本体フォルダ専用・gitignore 済み）")
    print(f"  - {LOCAL.relative_to(ROOT)}: claudeMdExcludes {len(settings['claudeMdExcludes'])} 件 / disableAllHooks / "
          f"skillOverrides {len(settings['skillOverrides'])} 件 / disabledMcpjsonServers {settings['disabledMcpjsonServers']}")
    print(f"  - CLAUDE.local.md（メンテナ向け常時ルール）")
    print(f"  - .claude/skills/: {', '.join(copied)}")
    print("次に：新しいセッションを開き「あなたは誰？」と聞いて、第一秘書と名乗らないことを確認してください。")


if __name__ == "__main__":
    main()
