#!/usr/bin/env python3
"""
update_instance.py ── git を使わない環境で、GitHub から落とした最新の ZIP を起点にインスタンスを更新する。

コア（本体の領域：CLAUDE.md・説明書類・.claude/・tools/・tests/ など）だけを最新に置き換え、
データ（memory/・deliverables/・.env・.claude/settings.local.json）には触れない（OPERATIONS.md §1 の境界）。

使い方（ZIP を展開したフォルダの中で、更新したいインスタンスを指定する）：
  python3 tools/update_instance.py --instance ~/ai-agency-mac            # 計画を表示するだけ（何も変えない）
  python3 tools/update_instance.py --instance ~/ai-agency-mac --apply    # 実行

安全のための約束：
- 消すのは「前回本体が配ったが、今回の本体には無いファイル」だけ。インスタンスで足したファイル
  （recruit-expert で足した専門家やスキルなど）は残す。配った一覧はインスタンスの .ai-agency-core.json に記録する
- 上書き・削除する前に、元のファイルを .ai-agency-backup/{日時}/ に控える（インスタンスで直接直していたコアも失わない）
- 最後に mem.py migrate（雛形の変更への追随）・check・テスト・記憶の点検を行い、結果を表示する
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import shutil
import subprocess
import sys
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent
MANIFEST = ".ai-agency-core.json"
BACKUP_DIR = ".ai-agency-backup"

CORE_FILES = ["CLAUDE.md", "README.md", "USAGE.md", "PORTABILITY.md", "OPERATIONS.md", ".mcp.json",
              ".gitignore", ".gitattributes", ".env.template"]
CORE_DIRS = [".claude", "tools", "tests", ".github"]
# コアの中でもインスタンスのもの（触らない）
KEEP = {".claude/settings.local.json"}
IGNORE_PARTS = {"__pycache__", ".pytest_cache"}


def sha(p: Path) -> str:
    return hashlib.sha1(p.read_bytes()).hexdigest()


def core_files(root: Path) -> dict[str, Path]:
    """root のコアのファイル（相対パス → 実体）。"""
    out = {}
    for name in CORE_FILES:
        p = root / name
        if p.is_file():
            out[name] = p
    for d in CORE_DIRS:
        base = root / d
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if not p.is_file() or set(p.parts) & IGNORE_PARTS or p.suffix == ".pyc":
                continue
            rel = p.relative_to(root).as_posix()
            if rel in KEEP:
                continue
            out[rel] = p
    return out


def load_manifest(inst: Path) -> dict:
    try:
        return json.loads((inst / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_manifest(inst: Path, source_root: Path | None = None) -> None:
    """インスタンスに「本体から配ったコアの一覧と指紋」を記録する（次回の更新で、消えたファイルの判定に使う）。"""
    files = core_files(source_root or inst)
    data = {"updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "files": {rel: sha(p) for rel, p in sorted(files.items())}}
    (inst / MANIFEST).write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def plan(src: Path, inst: Path) -> list[tuple[str, str, str]]:
    """(処理, 相対パス, 理由)。処理は add / update / local / delete / keep / same。"""
    new = core_files(src)
    have = core_files(inst)
    shipped = load_manifest(inst).get("files", {})
    items = []
    for rel, p in sorted(new.items()):
        if rel not in have:
            items.append(("add", rel, "本体に新しく入ったファイル"))
        elif sha(p) == sha(have[rel]):
            items.append(("same", rel, "変更なし"))
        elif rel in shipped and shipped[rel] != sha(have[rel]):
            items.append(("local", rel, "インスタンスで直接直されていた（控えを取ってから最新にする。直した内容は本体へ還流する）"))
        else:
            items.append(("update", rel, "本体で更新された"))
    # memory/ は触らないが、今の版で増えた雛形ファイル（例: dashboard.md・inbox.md）がインスタンスに無ければ追加だけする
    src_mem = src / "memory"
    if src_mem.is_dir():
        for p in sorted(src_mem.rglob("*")):
            if p.is_file() and "task-log" not in p.parts:
                rel = p.relative_to(src).as_posix()
                if not (inst / rel).exists():
                    items.append(("add", rel, "今の版で増えた記憶の雛形（無いので追加。既存の記憶は上書きしない）"))
                    new[rel] = p
    for rel in sorted(set(have) - set(new)):
        if rel in shipped:
            if shipped[rel] == sha(have[rel]):
                items.append(("delete", rel, "本体から無くなった（控えを取ってから消す）"))
            else:
                items.append(("keep", rel, "本体から無くなったが、インスタンスで直されていたので残す（要確認）"))
        else:
            items.append(("keep", rel, "インスタンスで足したファイル（残す）" if shipped else
                          "本体に無いファイル（配布の記録が無いので残す。不要なら手で消す）"))
    return items


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


def run_checks(inst: Path, tests: bool = True) -> list[str]:
    """更新後の仕上げ：雛形の変更への追随（migrate）・表の検査・テスト・記憶の点検。"""
    lines = []
    try:
        mem = _load("mem_for_update", inst / "tools" / "mem.py")
        buf = io.StringIO()
        with redirect_stdout(buf):
            mem.migrate(inst)
        lines.append(buf.getvalue().strip())
        buf = io.StringIO()
        code = 0
        try:
            with redirect_stdout(buf):
                mem.check(inst)
        except SystemExit as e:
            code = e.code or 0
        last = (buf.getvalue().strip().splitlines() or ["OK"])[-1]
        lines.append(("表の検査: " if code == 0 else "⚠️ 表の検査で問題: ") + last)
    except Exception as e:  # noqa: BLE001
        lines.append(f"⚠️ 移行・検査に失敗: {type(e).__name__}: {e}")
    if tests and (inst / "tests").is_dir():
        r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "tests"],
                           cwd=inst, capture_output=True, text=True, encoding="utf-8", errors="replace")
        tail = (r.stderr.strip().splitlines() or ["?"])[-1]
        lines.append(f"テスト: {tail}" + ("" if r.returncode == 0 else "（失敗。出力を確認する）"))
    try:
        recall = _load("recall_for_update", inst / "tools" / "recall.py")
        lines.append(f"記憶の点検: 要対応 {len(recall.review_findings(inst))} 件（`python3 tools/recall.py review`）")
    except Exception as e:  # noqa: BLE001
        lines.append(f"⚠️ 点検に失敗: {type(e).__name__}: {e}")
    return lines


def main(argv=None) -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="update_instance.py", description="最新の本体（ZIP を展開したフォルダ）でインスタンスを更新する")
    ap.add_argument("--instance", required=True, type=Path, help="更新するインスタンスのフォルダ（memory/ を含む）")
    ap.add_argument("--apply", action="store_true", help="実行する（付けなければ計画を表示するだけ）")
    ap.add_argument("--skip-tests", action="store_true", help="更新後のテストを省く")
    a = ap.parse_args(argv)
    src, inst = SRC.resolve(), a.instance.resolve()
    if src == inst:
        sys.exit("このスクリプトは ZIP を展開した（最新の本体の）フォルダから実行し、--instance に更新先を指定する")
    if not (inst / "memory").is_dir():
        sys.exit(f"インスタンスに memory/ が無い: {inst}")
    if (inst / ".ai-agency-template").exists():
        sys.exit("更新先が本体（テンプレート）。本体は git で管理する")
    if (inst / ".git").is_dir():
        print("ℹ️ 更新先は git 管理されている。git が使えるなら `git merge upstream/main` のほうが履歴が残る（OPERATIONS.md §3）\n")

    items = plan(src, inst)
    label = {"add": "追加", "update": "更新", "local": "更新（インスタンスで直されていた・控えを取る）",
             "delete": "削除（控えを取る）", "keep": "残す", "same": "変更なし"}
    print(f"最新の本体: {src}\n更新するインスタンス: {inst}\n")
    for st in ("add", "update", "local", "delete", "keep"):
        rows = [(rel, why) for s2, rel, why in items if s2 == st]
        if rows:
            print(f"## {label[st]}（{len(rows)}）")
            for rel, why in rows[:50]:
                print(f"- {rel} ── {why}")
            if len(rows) > 50:
                print(f"- ほか {len(rows) - 50} 件")
            print()
    print(f"変更なし: {sum(1 for st, _, _ in items if st == 'same')} 件。memory/・deliverables/・.env・.claude/settings.local.json には触れない\n")
    if not a.apply:
        print("計画だけ表示した（何も変えていない）。実行するときは --apply を付ける")
        return

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = inst / BACKUP_DIR / stamp
    new = core_files(src)
    changed = 0
    for st, rel, _ in items:
        dst = inst / rel
        if st in ("update", "local", "delete") and dst.exists():
            b = backup / rel
            b.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dst, b)
        if st in ("add", "update", "local"):
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src / rel, dst)
            changed += 1
        elif st == "delete":
            dst.unlink()
            changed += 1
    write_manifest(inst, src)
    print(f"更新した: {changed} 件" + (f"（控え: {backup.relative_to(inst).as_posix()}）" if backup.exists() else ""))
    for line in run_checks(inst, tests=not a.skip_tests):
        print(line)
    print("\n次は Claude Code でインスタンスを開く（起動時の案内に従う）。インスタンスで直されていたコアは、控えを見て本体へ還流する")


if __name__ == "__main__":
    main()
