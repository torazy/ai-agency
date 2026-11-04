#!/usr/bin/env python3
"""
import_legacy.py ── 旧版のインスタンス（別の PC で使っていた古い ai-agency / ai-manager）の記憶を、
今のインスタンスへ移す。写すのはデータだけで、構造（雛形・設定）は今の版のものを使う。

使い方（新しいインスタンスのルートで）：
  python tools/import_legacy.py --from D:/old/ai-manager            # 計画を表示するだけ（何も変えない）
  python tools/import_legacy.py --from D:/old/ai-manager --apply    # 実行：写す → migrate → check → 点検 → 報告

方針：
- 写す：約束台帳・気づき・inbox、人物ファイル、projects/・memos/・archive/・knowledge/（用語集・業務ルール）
- 突き合わせ：自分で書き換えていた delegation.md は安全に関わるので上書きせず legacy-import に置く
- 載せ替え：状況（situation.md）・プロフィール（user-profile.md）・登録簿（dashboard.md）は版ごとに雛形の構造が
  変わっているので上書きせず memory/legacy-import/ に置き、/setup で秘書が今の構造へ載せ替える
- 写さない：雛形と設定（people/_template.md・team/roster.md・guardrails.json・service-capabilities.md）、
  task-log/（承認の判定方法が違うため委任の根拠に使えない）、生成物（briefs/・sync-state.json）
- 上書きしない：写し先に既に自分の中身があるファイルは残し、旧版は memory/legacy-import/ に置く（秘書が統合する）
- 中身が雛形のままのファイルは写さない（移す意味がない）
- 何度実行しても同じ結果（写した元ファイルの指紋を legacy-import/IMPORT-MANIFEST.json に残し、変わっていなければ飛ばす）
結果は memory/legacy-import/IMPORT-REPORT.md に残る（秘書が /setup や memory-review で続きを行う）。
"""

from __future__ import annotations

import argparse
import filecmp
import hashlib
import importlib.util
import io
import json
import re
import shutil
import sys
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURE = HERE.parent / "tests" / "fixtures" / "memory"  # 今の版の雛形（中身が雛形のままかの判定に使う）

DATA_FILES = ["commitments.md", "insights.md", "inbox.md"]
CONDITIONAL_FILES = ["delegation.md"]           # 自分で書き換えていたときだけ。安全に関わるので上書きせず legacy-import で突き合わせる
DATA_DIRS = ["people", "projects", "memos", "archive", "knowledge"]
SKIP = {"people/_template.md", "team/roster.md", "guardrails.json", "service-capabilities.md", "sync-state.json",
        ".mem.lock"}
SKIP_DIRS = {"task-log", "briefs", "legacy-import"}
REVIEW_FILES = ["dashboard.md", "situation.md", "user-profile.md"]  # 中身があれば legacy-import に置き、/setup で載せ替える


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


def text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


PLACEHOLDER = re.compile(r"（(未登録|未設定|未記録|記録なし|未観察|なし)）|^\s*-\s*$|例[:：]|YYYY")
RETIRED = re.compile(r"GEMINI\.md|AGENTS\.md|Antigravity|\.agents/|Copilot|Hermes")  # 旧ツール向けの雛形の記述（データではない）


def data_lines(content: str, template: str = "") -> list[str]:
    """データらしい行：雛形に無く、見出し・引用・区切り・置き字ではない、表の行・箇条・値の入った項目。"""
    tpl = set(template.splitlines())
    out = []
    for ln in content.splitlines():
        t = ln.strip()
        if not t or ln in tpl or t.startswith(("#", ">", "---")) or re.match(r"^\|\s*:?-{2,}", t):
            continue
        if PLACEHOLDER.search(t) or RETIRED.search(t):
            continue
        if re.match(r"^\*\*最終更新\*\*", t):
            continue
        if t.startswith(("|", "-", "*", "1.", "2.", "3.")) or re.match(r"^- \*\*.+\*\*:\s*\S", t):
            out.append(t)
    return out


def is_template(rel: str, content: str) -> bool:
    """中身が雛形のまま（どの版の雛形でも）か。今の版の雛形と同じ行と、置き字・説明だけなら雛形とみなす。"""
    return not data_lines(content, text(FIXTURE / rel))


def sha(p: Path) -> str:
    return hashlib.sha1(p.read_bytes()).hexdigest()


def manifest_path(new_mem: Path) -> Path:
    return new_mem / "legacy-import" / "IMPORT-MANIFEST.json"


def load_manifest(new_mem: Path) -> dict:
    try:
        return json.loads(manifest_path(new_mem).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def plan(old_mem: Path, new_mem: Path) -> list[tuple[str, str, str]]:
    """(処理, 相対パス, 理由) の一覧。処理は copy / skip / conflict / review / same。"""
    items: list[tuple[str, str, str]] = []
    manifest = load_manifest(new_mem)

    def target_state(rel: str, src: Path) -> tuple[str, str]:
        if manifest.get(rel) == sha(src):
            return "same", "前回の移行で取り込み済み（元が変わっていない）"
        dst = new_mem / rel
        if not dst.exists():
            return "copy", "写し先に無い"
        if filecmp.cmp(src, dst, shallow=False):
            return "same", "同じ内容が既にある"
        if is_template(rel, text(dst)):
            return "copy", "写し先は雛形のまま"
        return "conflict", "写し先にも自分の中身がある → legacy-import に置いて秘書が統合"

    for rel in DATA_FILES:
        src = old_mem / rel
        if not src.exists():
            continue
        if is_template(rel, text(src)):
            items.append(("skip", rel, "旧版も雛形のまま（移す中身なし）"))
            continue
        st, why = target_state(rel, src)
        items.append((st, rel, why))
    for rel in CONDITIONAL_FILES:
        src = old_mem / rel
        if src.exists():
            if is_template(rel, text(src)):
                items.append(("skip", rel, "書き換えていない（今の版の雛形を使う）"))
            elif manifest.get(rel) == sha(src):
                items.append(("same", rel, "前回の移行で legacy-import に置き済み"))
            else:
                items.append(("review", rel, "自分で書き換えていた委任設定。安全に関わるので上書きせず、秘書が今の版と突き合わせる"))
    for d in DATA_DIRS:
        base = old_mem / d
        if not base.is_dir():
            continue
        for src in sorted(base.rglob("*")):
            if not src.is_file():
                continue
            rel = src.relative_to(old_mem).as_posix()
            if rel in SKIP or src.name.startswith(".") or set(Path(rel).parts) & SKIP_DIRS:
                continue
            st, why = target_state(rel, src)
            items.append((st, rel, why))
    for rel in REVIEW_FILES:
        src = old_mem / rel
        if not src.exists():
            continue
        if is_template(rel, text(src)):
            items.append(("skip", rel, "旧版も雛形のまま（移す中身なし）"))
        elif manifest.get(rel) == sha(src):
            items.append(("same", rel, "前回の移行で legacy-import に置き済み"))
        else:
            items.append(("review", rel, "版ごとに構造が違う。legacy-import に置き、/setup で今の構造へ載せ替える"))
    for src in sorted(old_mem.rglob("*")):
        if not src.is_file():
            continue
        rel = src.relative_to(old_mem).as_posix()
        if set(Path(rel).parts) & SKIP_DIRS or rel in SKIP:
            items.append(("skip", rel, "写さない（雛形・設定・生成物、または task-log）"))
            continue
        known = rel in DATA_FILES or rel in CONDITIONAL_FILES or rel in REVIEW_FILES \
            or any(rel.startswith(d + "/") for d in DATA_DIRS)
        if not known and not src.name.startswith("."):
            if manifest.get(rel) == sha(src):
                items.append(("same", rel, "前回の移行で legacy-import に置き済み"))
            else:
                items.append(("review", rel, "今の版に無いファイル。legacy-import に置き、秘書が置き場所を決める"))
    # 同じ相対パスの重複（skip と後段の判定）は先に出たものを優先
    seen, out = set(), []
    for it in items:
        if it[1] not in seen:
            seen.add(it[1])
            out.append(it)
    return out


def apply(old_mem: Path, new_root: Path, items: list) -> dict:
    new_mem = new_root / "memory"
    legacy = new_mem / "legacy-import"
    done = {"copy": [], "conflict": [], "review": []}
    manifest = load_manifest(new_mem)
    for st, rel, _ in items:
        src = old_mem / rel
        if st in ("copy", "conflict", "review"):
            manifest[rel] = sha(src)
        if st == "copy":
            dst = new_mem / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            done["copy"].append(rel)
        elif st in ("conflict", "review"):
            dst = legacy / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            done[st].append(rel)
    mp = manifest_path(new_mem)
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return done


def main(argv=None) -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="import_legacy.py", description="旧版インスタンスの記憶を今のインスタンスへ移す")
    ap.add_argument("--from", dest="old", required=True, type=Path, help="旧版インスタンスのフォルダ（memory/ を含む）")
    ap.add_argument("--root", type=Path, default=HERE.parent, help="移し先（今のインスタンス。既定はこのリポジトリ）")
    ap.add_argument("--apply", action="store_true", help="実行する（付けなければ計画を表示するだけ）")
    a = ap.parse_args(argv)
    old_root, new_root = a.old.resolve(), a.root.resolve()
    old_mem, new_mem = old_root / "memory", new_root / "memory"
    if not old_mem.is_dir():
        sys.exit(f"旧版のフォルダに memory/ が無い: {old_root}")
    if not new_mem.is_dir():
        sys.exit(f"移し先に memory/ が無い: {new_root}")
    if old_root == new_root:
        sys.exit("旧版と移し先が同じフォルダ")
    if (new_root / ".ai-agency-template").exists():
        sys.exit("移し先が本体（テンプレート）。記憶はインスタンスへ移す")

    if not FIXTURE.is_dir():
        print(f"⚠️ 今の版の雛形（{FIXTURE}）が無いので、写し先が雛形のままかを判定できない。"
              "上書きを避けるため、写し先に既にあるファイルはすべて legacy-import 扱いになる\n")
    items = plan(old_mem, new_mem)
    label = {"copy": "写す", "same": "同じ（飛ばす）", "conflict": "両方に中身（legacy-import へ）",
             "review": "要確認（legacy-import へ）", "skip": "写さない"}
    counts = {k: sum(1 for st, _, _ in items if st == k) for k in label}
    print(f"旧版: {old_root}\n移し先: {new_root}\n")
    for st in ("copy", "conflict", "review", "same", "skip"):
        rows = [(rel, why) for s2, rel, why in items if s2 == st]
        if not rows:
            continue
        print(f"## {label[st]}（{len(rows)}）")
        for rel, why in rows[:60]:
            print(f"- {rel} ── {why}")
        if len(rows) > 60:
            print(f"- ほか {len(rows) - 60} 件")
        print()
    if not a.apply:
        print("計画だけ表示した（何も変えていない）。実行するときは --apply を付ける")
        return

    done = apply(old_mem, new_root, items)
    # ZIP を展開したばかりのフォルダがインスタンスになる場合に備え、配ったコアの一覧を残す（次回の update_instance.py が使う）
    if not (new_root / ".ai-agency-core.json").exists() and not (new_root / ".git").is_dir():
        upd = load_module("update_for_import", new_root / "tools" / "update_instance.py")
        upd.write_manifest(new_root)
    mem = load_module("mem_for_import", new_root / "tools" / "mem.py")
    buf = io.StringIO()
    with redirect_stdout(buf):
        mem.migrate(new_root)
    migrate_out = buf.getvalue().strip()
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            mem.check(new_root)
        check_ok = True
    except SystemExit as e:
        check_ok = e.code in (0, None)
    check_out = buf.getvalue().strip()
    recall = load_module("recall_for_import", new_root / "tools" / "recall.py")
    findings = recall.review_findings(new_root)

    report = [f"# 旧版からの移行（{datetime.now():%Y-%m-%d %H:%M}）", "",
              f"- 旧版: `{old_root}`",
              f"- 写した: {len(done['copy'])} 件 ／ 両方に中身があり legacy-import に置いた: {len(done['conflict'])} 件 ／"
              f" 要確認で legacy-import に置いた: {len(done['review'])} 件",
              f"- 移行: {migrate_out}", f"- 表の検査: {'OK' if check_ok else '問題あり'}（{check_out.splitlines()[-1] if check_out else ''}）",
              f"- 記憶の点検: 要対応 {len(findings)} 件（`python tools/recall.py review`）", "",
              "## 秘書がやること（/setup または memory-review で）",
              "- legacy-import/ の各ファイルを読み、今の記憶へ統合する（両方に中身があるものは突き合わせて1つに。統合したファイルは legacy-import から消す。IMPORT-MANIFEST.json は残す＝再実行で二重に取り込まない）",
              "- 旧版の dashboard.md・user-profile.md・situation.md があれば、名前・案件・人物・優先事項を今の dashboard.md・situation.md へ載せ替える",
              "- 移した案件が今も進行中か確かめる（完了ならアーカイブ）。移行で補った決定の状態（提案／決定）に目を通す", ""]
    for key, title in (("conflict", "両方に中身があったファイル"), ("review", "要確認のファイル")):
        if done[key]:
            report.append(f"## {title}（memory/legacy-import/ に置いた）")
            report += [f"- `{r}`" for r in done[key]]
            report.append("")
    rp = new_mem / "legacy-import" / "IMPORT-REPORT.md"
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n".join(report))
    print(f"報告: {rp.relative_to(new_root).as_posix()}。次は git にコミットし、Claude Code で /setup を実行する")


if __name__ == "__main__":
    main()
