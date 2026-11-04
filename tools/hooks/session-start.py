#!/usr/bin/env python3
"""
session-start.py ── SessionStart フック。記憶の育成状態を 1〜3 行で会話に注入する。

目的：memory/ が雛形のまま（dashboard 未設定・案件/人物ゼロ）だと、/simulate・/morning-brief・
/propose-delegation などは素材が無くて動かない。その状態を秘書が毎回自力で診断する代わりに、
セッション開始時に事実だけを渡し、/setup の案内や期限超過の注意につなげる。

stdout（プレーンテキスト・exit 0）は Claude Code が「Claude が見られる文脈」として追加する。
命令形ではなく事実の記述にする（フック出力の規約）。失敗しても exit 0。上限 10,000 文字。
"""

import re
import sys
from datetime import date, datetime
from pathlib import Path


def workspace_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    root = workspace_root()
    mem = root / "memory"
    lines = []

    # 本体（テンプレート）リポジトリでは記憶を蓄積しないので /setup を案内しない。
    # 判定はルートの gitignore 済みマーカーファイル `.ai-agency-template`（本体メンテナが1回作る。
    # clone されないためインスタンスには存在しない。OPERATIONS.md §5）。
    if (root / ".ai-agency-template").exists():
        print("[記憶の状態] このリポジトリは本体（テンプレート）。記憶は蓄積せず /setup も案内しない（OPERATIONS.md §1）。")
        sys.exit(0)

    dashboard = read(mem / "dashboard.md")
    profile_unset = "**名前**: （未設定）" in dashboard
    projects = [p for p in (mem / "projects").glob("*/overview.md")] if (mem / "projects").exists() else []
    people = [p for p in (mem / "people").glob("*.md") if p.name != "_template.md"] if (mem / "people").exists() else []

    ops = 0
    for f in (mem / "task-log").glob("*.md") if (mem / "task-log").exists() else []:
        ops += read(f).count("**操作**:")

    if profile_unset or (not projects and not people):
        lines.append(
            "[記憶の状態] memory/ は未初期化（プロフィール"
            + ("未設定" if profile_unset else "設定済")
            + f"・案件 {len(projects)} 件・人物 {len(people)} 名）。/setup ワークフローで初期化できる。"
        )
    else:
        lines.append(f"[記憶の状態] 案件 {len(projects)} 件・人物 {len(people)} 名・操作実績 {ops} 件。")

    # situation.md の鮮度（最終更新が 14 日以上前なら注意）
    situation = read(mem / "situation.md")
    m = re.search(r"\*\*最終更新\*\*:\s*(\d{4}-\d{2}-\d{2})", situation)
    if m:
        try:
            age = (date.today() - datetime.strptime(m.group(1), "%Y-%m-%d").date()).days
            if age >= 14:
                lines.append(f"[situation.md] 最終更新から {age} 日経過。")
        except ValueError:
            pass

    # commitments.md の期限超過：ID 列（C-nnn）で始まる実データ行のうち、追加日より後の日付セルに
    # 今日より前があり、状態が done でない行を数える（記法説明や雛形行は対象外）
    commitments = read(mem / "commitments.md")
    overdue = 0
    today = date.today()
    for row in commitments.splitlines():
        cells = [c.strip() for c in row.strip().strip("|").split("|")] if row.startswith("|") else []
        if len(cells) < 4 or not re.fullmatch(r"C-\d{3,}", cells[0]) or cells[-1] == "done":
            continue
        for d in re.findall(r"(\d{4}-\d{2}-\d{2})", " ".join(cells[2:])):
            try:
                if datetime.strptime(d, "%Y-%m-%d").date() < today:
                    overdue += 1
                    break
            except ValueError:
                continue
    if overdue:
        lines.append(f"[約束台帳] 期限またはフォロー日が過去の未完了行が {overdue} 行ある（要確認）。")

    # inbox の未分類
    inbox = read(mem / "inbox.md")
    pending = sum(1 for ln in inbox.splitlines() if ln.startswith("- [ ] "))
    if pending:
        lines.append(f"[inbox] 未分類が {pending} 件ある（分類して `mem.py inbox resolve`）。")

    # 無人実行の朝のブリーフ（tools/schedule/）。今日か昨日の分があれば件数だけ伝える
    briefs = sorted((mem / "briefs").glob("????-??-??.md")) if (mem / "briefs").exists() else []
    if briefs:
        latest = briefs[-1]
        try:
            age = (today - datetime.strptime(latest.stem, "%Y-%m-%d").date()).days
        except ValueError:
            age = 99
        if age <= 1:
            body = read(latest)
            def count(head: str) -> int:
                # 末端の箇条だけ数える（案件見出しの親行と「該当なし」は数えない）
                m = re.search(rf"^## {head}.*?$(.*?)(?=^## |\Z)", body, re.M | re.S)
                if not m:
                    return 0
                items = [ln for ln in m.group(1).splitlines() if re.match(r"^\s*- ", ln)]
                depth = lambda ln: len(ln) - len(ln.lstrip())
                n = 0
                for i, ln in enumerate(items):
                    is_parent = i + 1 < len(items) and depth(items[i + 1]) > depth(ln)
                    if not is_parent and "該当なし" not in ln:
                        n += 1
                return n
            lines.append(
                f"[朝のブリーフ] memory/briefs/{latest.name}（{'今日' if age == 0 else '昨日'}の無人実行）"
                f"：今日中 {count('🔴')} 件・見ておく {count('🟡')} 件・取り込み候補は inbox へ。"
            )

    print("\n".join(lines)[:9000])
    sys.exit(0)


if __name__ == "__main__":
    main()
