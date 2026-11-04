#!/usr/bin/env python3
"""
enable-maintenance.py ── 本体（テンプレート）フォルダで「メンテモード」を有効化する／本体の今の姿に写し直す。

生成・更新するもの（すべて gitignore 済みか .git の中＝インスタンスには届かない）：
  - .claude/settings.local.json   既存内容を保ったまま、製品を止めるキーを書く
      claudeMdExcludes       : CLAUDE.md / memory/dashboard.md を読み込まない
      disableAllHooks        : 製品のフックを全部止める（今後足されるフックも）
      skillOverrides         : 製品のスキル・コマンドを Claude から見えなくする（その時点の一覧。写し直しが要る）
      disabledMcpjsonServers : .mcp.json の MCP サーバーを起動しない（同上）
  - CLAUDE.local.md               tools/dev/claude-local.md のコピー（メンテナ向け常時ルール。状況台帳を読み込む）
  - .claude/skills/maint-*/       tools/dev/skills/ のコピー（メンテ用スキル）
  - .ai-agency-template           本体マーカー（製品のツールは本体では動かない・maint.py はこれでメンテモードと判断）
  - .git/hooks/{pre-commit,post-commit,post-merge,post-checkout}
                                  tools/dev/maint.py を呼ぶ。コミット・pull・切り替えのたびに上を写し直し、
                                  着手中の要求が無いコミットと、扱いの決まっていない本体設定の変更を止める

冪等。写し直しは python tools/dev/maint.py start と git フックからも自動で行う。戻すには disable-maintenance.py。
設計：tools/dev/DESIGN.md
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

# claudeMdExcludes は絶対パスに対する glob。ドライブ文字の表記差を避け、フォルダ名で終わるパターンにする
EXCLUDE_FILES = ["CLAUDE.md", "memory/dashboard.md"]
HOOK_MARK = "# ai-agency maintenance hook (tools/dev/enable-maintenance.py)"
GIT_HOOKS = ("pre-commit", "post-commit", "post-merge", "post-checkout")
COMMENT = ("本体メンテモード（tools/dev/enable-maintenance.py が生成・maint.py と git フックが写し直す）。"
           "製品の CLAUDE.md・フック・スキル・MCP をこの本体フォルダでだけ止める。戻すには tools/dev/disable-maintenance.py。")


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        sys.exit(f"[enable-maintenance] {path} が壊れています: {e}")


def product_skill_names(root: Path = ROOT) -> list[str]:
    sk, cm = root / ".claude" / "skills", root / ".claude" / "commands"
    names = {p.name for p in sk.iterdir() if p.is_dir() and not p.name.startswith("maint-")} if sk.exists() else set()
    names |= {p.stem for p in cm.glob("*.md")} if cm.exists() else set()
    return sorted(names)


def mcp_server_names(root: Path = ROOT) -> list[str]:
    return sorted(load_json(root / ".mcp.json").get("mcpServers", {}).keys())


def _tree(d: Path) -> dict:
    return {p.relative_to(d).as_posix(): p.read_bytes() for p in d.rglob("*") if p.is_file()} if d.exists() else {}


def _hooks_dir(root: Path) -> Path | None:
    try:
        p = subprocess.run(["git", "rev-parse", "--git-path", "hooks"], cwd=root, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0 or not p.stdout.strip():
        return None
    d = Path(p.stdout.strip())
    return d if d.is_absolute() else root / d


def hook_script(name: str) -> str:
    return (f"#!/bin/sh\n{HOOK_MARK}\n"
            "[ -f tools/dev/maint.py ] && [ -f .ai-agency-template ] || exit 0\n"
            f'exec sh tools/py.sh tools/dev/maint.py git-hook {name} "$@"\n')


def sync(root: Path = ROOT) -> list[str]:
    """メンテ層を本体の今の姿に合わせる。変えたことの説明を返す（何も変えなければ空）。"""
    changes: list[str] = []
    local = root / ".claude" / "settings.local.json"
    settings = load_json(local)
    before = json.dumps(settings, sort_keys=True, ensure_ascii=False)

    excludes = set(settings.get("claudeMdExcludes", []))
    excludes |= {f"**/{root.name}/{f}" for f in EXCLUDE_FILES}
    settings["claudeMdExcludes"] = sorted(excludes)
    settings["disableAllHooks"] = True

    overrides = dict(settings.get("skillOverrides", {}))
    newly = [n for n in product_skill_names(root) if overrides.get(n) != "off"]
    for n in newly:
        overrides[n] = "off"
    settings["skillOverrides"] = dict(sorted(overrides.items()))
    if newly:
        changes.append("本体のスキル・コマンドを止めた: " + ", ".join(newly))

    servers = mcp_server_names(root)
    new_srv = [s for s in servers if s not in settings.get("disabledMcpjsonServers", [])]
    settings["disabledMcpjsonServers"] = sorted(set(settings.get("disabledMcpjsonServers", [])) | set(servers))
    if "enabledMcpjsonServers" in settings:
        settings["enabledMcpjsonServers"] = [s for s in settings["enabledMcpjsonServers"] if s not in servers]
    if new_srv:
        changes.append("本体の MCP サーバーを止めた: " + ", ".join(new_srv))
    settings["$comment_maintenance"] = COMMENT

    if json.dumps(settings, sort_keys=True, ensure_ascii=False) != before:
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if not changes:
            changes.append("settings.local.json を更新")

    src, dst = root / "tools" / "dev" / "claude-local.md", root / "CLAUDE.local.md"
    if src.exists() and (not dst.exists() or dst.read_bytes() != src.read_bytes()):
        shutil.copyfile(src, dst)
        changes.append("CLAUDE.local.md を写し直した")

    want = {p.name for p in (root / "tools" / "dev" / "skills").iterdir() if p.is_dir() and p.name.startswith("maint-")} \
        if (root / "tools" / "dev" / "skills").exists() else set()
    for name in sorted(want):
        s, d = root / "tools" / "dev" / "skills" / name, root / ".claude" / "skills" / name
        if _tree(s) != _tree(d):
            if d.exists():
                shutil.rmtree(d)
            shutil.copytree(s, d)
            changes.append(f"メンテ用スキル {name} を写し直した")
    for d in (root / ".claude" / "skills").glob("maint-*") if (root / ".claude" / "skills").exists() else []:
        if d.is_dir() and d.name not in want:
            shutil.rmtree(d)
            changes.append(f"消えたメンテ用スキル {d.name} を外した")

    marker = root / ".ai-agency-template"
    if not marker.exists():
        marker.write_text("このリポジトリは ai-agency 本体（テンプレート）。記憶を蓄積しない。tools/dev/README.md 参照。\n",
                          encoding="utf-8")
        changes.append("本体マーカーを置いた")

    hd = _hooks_dir(root)
    if hd is not None:
        hd.mkdir(parents=True, exist_ok=True)
        for name in GIT_HOOKS:
            p = hd / name
            body = hook_script(name)
            if p.exists() and HOOK_MARK not in p.read_text(encoding="utf-8", errors="replace"):
                changes.append(f"git フック {name} は既存のものがあるので入れていない（手で maint.py git-hook {name} を呼ぶ）")
                continue
            if not p.exists() or p.read_text(encoding="utf-8") != body:
                with open(p, "w", encoding="utf-8", newline="\n") as f:
                    f.write(body)
                os.chmod(p, 0o755)
                changes.append(f"git フック {name} を入れた")
    return changes


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    changes = sync(ROOT)
    print("[enable-maintenance] メンテモード " + ("を更新しました" if changes else "は最新です"))
    for c in changes:
        print(f"  - {c}")
    print("次に：新しいセッションを開き「あなたは誰？」と聞いて、第一秘書と名乗らないことを確認する。"
          "続けて python tools/dev/maint.py start で状況を確かめる。")


if __name__ == "__main__":
    main()
