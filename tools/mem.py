#!/usr/bin/env python3
"""
mem.py ── memory/ への「決定的な追記」CLI。

モデルが markdown 表を直接編集すると列ずれ・重複・雛形行の残置が起きる。記録はこの CLI を通すことで
①書式が崩れない ②ID が振られる（後で状態更新・ボード表示に使える）③PostToolUse フックが
`mem:<sub>` として承認実績に記録できる。markdown は引き続き唯一の正典（この CLI は書き手に過ぎない）。

使い方（リポジトリルートで）：
  python tools/mem.py inbox add "来週の部会は水曜に変更" [--source 会話]
  python tools/mem.py inbox resolve I-001 --to memory/projects/x/status.md
  python tools/mem.py inbox list
  python tools/mem.py commitment add --kind task     --content "経費精算" --due 2026-11-07 --rel -
  python tools/mem.py commitment add --kind received --content "比較表提出" --who 架空部長 --due 2026-11-14 --rel P:kintai-saas
  python tools/mem.py commitment add --kind given    --content "レビュー依頼" --who 鈴木さん --due 2026-11-10 --follow 2026-11-08 --rel P:x
  python tools/mem.py commitment set-state C-003 done
  python tools/mem.py commitment list [--rel P:x] [--state open] [--json]
  python tools/mem.py person observe kakuu-ichiro-kakuu-corp-bucho --situation "..." --judgment "..." --reason "...（推測）"
  python tools/mem.py person note    kakuu-ichiro-kakuu-corp-bucho --situation "..." --insight "..."
  python tools/mem.py person predict kakuu-ichiro-kakuu-corp-bucho --case "..." --prediction "..." --verify-date 2026-11-20
  python tools/mem.py decision add --project kintai-saas --title "..." --content "..." --reason "..." \
         --impact "..." --prediction "..." --verify-date 2026-12-01 [--status proposed|decided] [--revoke "..."]
  python tools/mem.py decision set-status kintai-saas "<タイトル>" decided
  python tools/mem.py check

共通オプション：--root <path>（memory/ を含むリポジトリルート。既定は自身の位置から推定。テストに使う）
出力は UTF-8。成功時は「📝 記録: ...」を1行。失敗は非ゼロ終了。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

PLACEHOLDER_RE = re.compile(r"^\|\s*（(未登録|記録なし|未観察|未記録)）\s*\|")
TABLE_ROW_RE = re.compile(r"^\|.*\|\s*$")
TABLE_SEP_RE = re.compile(r"^\|\s*:?-{2,}")


# ---------------------------------------------------------------- utilities
def today() -> str:
    return date.today().isoformat()


def now_hm() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def esc(s: str) -> str:
    """表のセルに入れる文字列（縦棒と改行を無害化）。"""
    return " ".join(str(s).replace("|", "／").split())


def read(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def write(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")


def find_section(lines: list[str], heading_substr: str) -> int:
    """見出し行（# で始まる）のうち heading_substr を含む最初の行番号。無ければ -1。"""
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith("#") and heading_substr in ln:
            return i
    return -1


def find_table(lines: list[str], start: int) -> tuple[int, int]:
    """start 以降で最初の表（ヘッダ行, 末尾行番号+1）。見つからなければ (-1, -1)。次の見出しで打ち切る。"""
    i = start + 1
    while i < len(lines) and not lines[i].lstrip().startswith("#"):
        if TABLE_ROW_RE.match(lines[i]) and i + 1 < len(lines) and TABLE_SEP_RE.match(lines[i + 1]):
            j = i + 2
            while j < len(lines) and TABLE_ROW_RE.match(lines[j]):
                j += 1
            return i, j
        i += 1
    return -1, -1


def cells(row: str) -> list[str]:
    return [c.strip() for c in row.strip().strip("|").split("|")]


def append_row(lines: list[str], header_idx: int, end_idx: int, row: list[str]) -> list[str]:
    """表末尾に行を追加。雛形行（（未登録）等）は削除する。"""
    body = lines[header_idx + 2:end_idx]
    body = [r for r in body if not PLACEHOLDER_RE.match(r)]
    new_row = "| " + " | ".join(esc(c) for c in row) + " |"
    return lines[:header_idx + 2] + body + [new_row] + lines[end_idx:]


def next_id(lines: list[str], prefix: str) -> str:
    """表の行（| …）とリスト項目（- [ ] / - [x]）に現れる ID だけを数える（説明文中の例 C-001 は無視）。"""
    nums = [
        int(m.group(1))
        for ln in lines
        if ln.startswith("|") or ln.startswith("- [")
        for m in re.finditer(rf"\b{re.escape(prefix)}-(\d{{3,}})\b", ln)
    ]
    return f"{prefix}-{(max(nums) + 1) if nums else 1:03d}"


def ok(msg: str) -> None:
    print(f"📝 記録: {msg}")


def fail(msg: str, code: int = 1) -> None:
    print(f"❌ {msg}", file=sys.stderr)
    sys.exit(code)


# ---------------------------------------------------------------- inbox
INBOX_HEADER = [
    "# inbox（分類前の受け皿）",
    "",
    "> 「覚えておいて」「あとで整理して」と言われたものを**分類する前に**ここへ即時保存する（取りこぼしゼロ）。",
    "> 分類できたら `mem.py inbox resolve <ID> --to <記録先>` で ✅ にする。未分類が残っていれば",
    "> /morning-brief と memory-maintenance が仕分けを促す。原文は要点のみ（長文はファイルへ）。",
    "",
    "## 未分類",
    "",
    "## 分類済（直近30件。古いものは月次でアーカイブ）",
    "",
]


def inbox_path(root: Path) -> Path:
    p = root / "memory" / "inbox.md"
    if not p.exists():
        write(p, INBOX_HEADER)
    return p


def inbox_add(root: Path, text: str, source: str) -> None:
    p = inbox_path(root)
    lines = read(p)
    iid = next_id(lines, "I")
    sec = find_section(lines, "未分類")
    if sec < 0:
        fail("inbox.md に『## 未分類』が無い")
    # 未分類節の末尾（次の見出し直前）に追加
    j = sec + 1
    while j < len(lines) and not lines[j].lstrip().startswith("#"):
        j += 1
    insert_at = j
    while insert_at > sec + 1 and lines[insert_at - 1].strip() == "":
        insert_at -= 1
    entry = f"- [ ] {iid} {now_hm()} 「{esc(text)}」（出所: {esc(source)}）"
    pad = [""] if lines[insert_at - 1].lstrip().startswith("#") else []
    lines = lines[:insert_at] + pad + [entry] + lines[insert_at:]
    write(p, lines)
    ok(f"inbox {iid} に保存（未分類）。分類したら `mem.py inbox resolve {iid} --to <記録先>`")


def inbox_resolve(root: Path, iid: str, to: str) -> None:
    p = inbox_path(root)
    lines = read(p)
    idx = next((i for i, ln in enumerate(lines) if ln.startswith(f"- [ ] {iid} ")), -1)
    if idx < 0:
        fail(f"未分類に {iid} が無い")
    entry = lines[idx].replace("- [ ] ", "- [x] ", 1) + f" → 記録先: {esc(to)}（{today()}）"
    del lines[idx]
    sec = find_section(lines, "分類済")
    if sec < 0:
        lines += ["", "## 分類済", ""]
        sec = len(lines) - 2
    lines = lines[:sec + 1] + [""] + [entry] + lines[sec + 1:]
    # 連続する空行を1つに
    out: list[str] = []
    for ln in lines:
        if ln.strip() == "" and out and out[-1].strip() == "":
            continue
        out.append(ln)
    write(p, out)
    ok(f"inbox {iid} を分類済に（→ {to}）")


def inbox_list(root: Path) -> None:
    p = inbox_path(root)
    pending = [ln for ln in read(p) if ln.startswith("- [ ] ")]
    print(f"未分類: {len(pending)} 件")
    for ln in pending:
        print(ln)


# ---------------------------------------------------------------- commitments
KIND_SECTION = {"task": "自分のタスク", "received": "受けた指示", "given": "出した指示"}
KIND_COLUMNS = {
    "task": ["ID", "追加日", "タスク", "期限", "関連", "状態"],
    "received": ["ID", "受領日", "依頼者", "内容", "期限", "関連", "状態"],
    "given": ["ID", "依頼日", "相手", "内容", "期限", "フォロー日", "関連", "状態"],
}
STATES = {"open", "in-progress", "waiting", "done"}


def commitments_path(root: Path) -> Path:
    p = root / "memory" / "commitments.md"
    if not p.exists():
        fail("memory/commitments.md が無い（雛形から作成してください）")
    return p


def ensure_id_column(lines: list[str], header_idx: int, end_idx: int) -> tuple[list[str], int]:
    """表に ID 列が無ければ先頭に挿入し、既存行に ID を振る（旧雛形からの移行）。end_idx は変わらない。"""
    hdr = cells(lines[header_idx])
    if hdr and hdr[0] == "ID":
        return lines, end_idx
    lines[header_idx] = "| ID | " + " | ".join(hdr) + " |"
    lines[header_idx + 1] = "|---" * (len(hdr) + 1) + "|"
    for i in range(header_idx + 2, end_idx):
        if PLACEHOLDER_RE.match(lines[i]):
            lines[i] = "| （未登録） |" + " |" * len(hdr)
        else:
            cid = next_id(lines, "C")
            lines[i] = f"| {cid} " + lines[i].strip()
    return lines, end_idx


def commitment_add(root: Path, a: argparse.Namespace) -> None:
    p = commitments_path(root)
    lines = read(p)
    sec = find_section(lines, KIND_SECTION[a.kind])
    if sec < 0:
        fail(f"commitments.md に『{KIND_SECTION[a.kind]}』節が無い")
    h, e = find_table(lines, sec)
    if h < 0:
        fail(f"『{KIND_SECTION[a.kind]}』節に表が無い")
    lines, e = ensure_id_column(lines, h, e)
    cid = next_id(lines, "C")
    rel = a.rel or "-"
    state = a.state or "open"
    if state not in STATES:
        fail(f"状態は {sorted(STATES)} のいずれか")
    due = a.due or "なし"
    if a.kind == "task":
        row = [cid, today(), a.content, due, rel, state]
    elif a.kind == "received":
        if not a.who:
            fail("--who（依頼者）が必要")
        row = [cid, today(), a.who, a.content, due, rel, state]
    else:
        if not a.who:
            fail("--who（相手）が必要")
        if not a.follow:
            fail("--follow（フォロー日）が必要。期限の1〜2営業日前を提案する")
        row = [cid, today(), a.who, a.content, due, a.follow, rel, state]
    lines = append_row(lines, h, e, row)
    lines = touch_updated(lines)
    write(p, lines)
    ok(f"約束台帳 {KIND_SECTION[a.kind]} に {cid}「{a.content}」（期限 {due}・関連 {rel}）")


def touch_updated(lines: list[str]) -> list[str]:
    for i, ln in enumerate(lines):
        if ln.startswith("**最終更新**:"):
            lines[i] = f"**最終更新**: {today()}"
            break
    return lines


def commitment_set_state(root: Path, cid: str, state: str) -> None:
    if state not in STATES:
        fail(f"状態は {sorted(STATES)} のいずれか")
    p = commitments_path(root)
    lines = read(p)
    for i, ln in enumerate(lines):
        if TABLE_ROW_RE.match(ln) and cells(ln)[:1] == [cid]:
            c = cells(ln)
            c[-1] = state
            lines[i] = "| " + " | ".join(c) + " |"
            write(p, touch_updated(lines))
            ok(f"{cid} の状態を {state} に")
            return
    fail(f"{cid} が見つからない")


def commitment_rows(root: Path) -> list[dict]:
    p = commitments_path(root)
    lines = read(p)
    out = []
    for kind, sec_name in KIND_SECTION.items():
        sec = find_section(lines, sec_name)
        if sec < 0:
            continue
        h, e = find_table(lines, sec)
        if h < 0:
            continue
        hdr = cells(lines[h])
        for ln in lines[h + 2:e]:
            if PLACEHOLDER_RE.match(ln):
                continue
            c = cells(ln)
            d = {"kind": kind}
            d.update({k: v for k, v in zip(hdr, c)})
            out.append(d)
    return out


def commitment_list(root: Path, a: argparse.Namespace) -> None:
    rows = commitment_rows(root)
    if a.rel:
        rows = [r for r in rows if a.rel in r.get("関連", "")]
    if a.state:
        rows = [r for r in rows if r.get("状態") == a.state]
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    for r in rows:
        who = r.get("依頼者") or r.get("相手") or ""
        content = r.get("タスク") or r.get("内容") or ""
        extra = f"・フォロー {r['フォロー日']}" if "フォロー日" in r else ""
        print(f"{r.get('ID','?'):6s} {r['kind']:8s} {who:10s} {content} ── 期限 {r.get('期限','')}{extra} ［{r.get('関連','')}］ {r.get('状態','')}")
    print(f"計 {len(rows)} 件")


# ---------------------------------------------------------------- people
def person_path(root: Path, ident: str) -> Path:
    ident = ident.removesuffix(".md")
    if "/" in ident or "\\" in ident:
        p = root / ident
        if not p.suffix:
            p = p.with_suffix(".md")
    else:
        p = root / "memory" / "people" / f"{ident}.md"
    if not p.exists():
        fail(f"人物ファイルが無い: {p}（_template.md から作成し、識別子を記録してください）")
    return p


def person_append(root: Path, ident: str, heading: str, row: list[str], label: str) -> None:
    p = person_path(root, ident)
    lines = read(p)
    sec = find_section(lines, heading)
    if sec < 0:
        fail(f"{p.name} に『{heading}』が無い")
    h, e = find_table(lines, sec)
    if h < 0:
        fail(f"『{heading}』に表が無い")
    lines = append_row(lines, h, e, row)
    write(p, lines)
    ok(f"{p.name} の{label}に追記")


def person_observe(root: Path, a: argparse.Namespace) -> None:
    person_append(root, a.ident, "判断事例ログ", [today(), a.situation, a.judgment, a.reason or "（不明）"], "判断事例ログ")
    # 成熟度の再評価の目安を出す
    p = person_path(root, a.ident)
    lines = read(p)
    sec = find_section(lines, "判断事例ログ")
    h, e = find_table(lines, sec)
    n = sum(1 for ln in lines[h + 2:e] if not PLACEHOLDER_RE.match(ln))
    if n >= 5:
        print(f"   判断事例が {n} 件。成熟度 Lv2 以上の要件を満たす（人物ファイルの成熟度行を確認）")


def person_note(root: Path, a: argparse.Namespace) -> None:
    person_append(root, a.ident, "過去のやりとり・気づき", [today(), a.situation, a.insight], "気づき")


def person_predict(root: Path, a: argparse.Namespace) -> None:
    person_append(root, a.ident, "反応予測と検証", [today(), a.case, a.prediction, a.verify_date, "（未検証）", "", ""], "反応予測")


# ---------------------------------------------------------------- decisions
def decisions_path(root: Path, project: str) -> Path:
    p = root / "memory" / "projects" / project / "decisions.md"
    if not p.exists():
        write(p, [f"# 決定事項（{project}）", "",
                  "> 決定には必ず「予測（期待した結果）」と「検証日」を付す。/review-decisions が検証する。",
                  "> 状態は 提案（未承認）→ 決定 → 検証済。提案のまま放置された項目は /morning-brief が拾う。", ""])
    return p


def decision_add(root: Path, a: argparse.Namespace) -> None:
    p = decisions_path(root, a.project)
    status = "提案（未承認）" if a.status == "proposed" else "決定"
    block = [
        "",
        f"## {today()}: {a.title}",
        f"- **状態**: {status}",
        f"- **決定内容**: {a.content}",
        f"- **決定理由**: {a.reason}",
        f"- **影響範囲**: {a.impact or '（未記入）'}",
        f"- **予測（期待した結果）**: {a.prediction}",
        f"- **検証日**: {a.verify_date}",
        f"- **撤回条件**: {a.revoke or '（なし）'}",
        "- **検証結果**: （/review-decisions が後日追記）",
    ]
    lines = read(p) + block
    write(p, lines)
    ok(f"{a.project}/decisions.md に「{a.title}」を{status}として記録（検証日 {a.verify_date}）")


def decision_set_status(root: Path, project: str, title: str, status: str) -> None:
    p = decisions_path(root, project)
    lines = read(p)
    label = {"proposed": "提案（未承認）", "decided": "決定", "verified": "検証済"}.get(status)
    if not label:
        fail("状態は proposed / decided / verified")
    for i, ln in enumerate(lines):
        if ln.startswith("## ") and title in ln:
            for j in range(i + 1, min(i + 12, len(lines))):
                if lines[j].startswith("- **状態**:"):
                    lines[j] = f"- **状態**: {label}（{today()}）"
                    write(p, lines)
                    ok(f"「{title}」の状態を {label} に")
                    return
            lines.insert(i + 1, f"- **状態**: {label}（{today()}）")
            write(p, lines)
            ok(f"「{title}」に状態 {label} を追加")
            return
    fail(f"「{title}」が見つからない")


# ---------------------------------------------------------------- check
def check(root: Path) -> None:
    problems = 0
    mem = root / "memory"
    for path in [mem / "commitments.md"] + sorted((mem / "people").glob("*.md")) + sorted(mem.glob("projects/*/decisions.md")):
        if not path.exists():
            continue
        lines = read(path)
        i = 0
        while i < len(lines):
            if TABLE_ROW_RE.match(lines[i]) and i + 1 < len(lines) and TABLE_SEP_RE.match(lines[i + 1]):
                ncol = len(cells(lines[i]))
                j = i + 2
                while j < len(lines) and TABLE_ROW_RE.match(lines[j]):
                    if len(cells(lines[j])) != ncol:
                        print(f"⚠️ 列数不一致 {path.relative_to(root)}:{j + 1}（{len(cells(lines[j]))} 列、ヘッダは {ncol} 列）")
                        problems += 1
                    j += 1
                i = j
            else:
                i += 1
    pending = [ln for ln in read(mem / "inbox.md")] if (mem / "inbox.md").exists() else []
    n_pending = sum(1 for ln in pending if ln.startswith("- [ ] "))
    print(f"inbox 未分類: {n_pending} 件")
    print("OK" if problems == 0 else f"問題 {problems} 件")
    sys.exit(0 if problems == 0 else 1)


# ---------------------------------------------------------------- main
def default_root() -> Path:
    return Path(__file__).resolve().parent.parent


def main(argv: list[str] | None = None) -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(prog="mem.py", description="memory/ への決定的な追記 CLI")
    ap.add_argument("--root", type=Path, default=None, help="リポジトリルート（既定: tools/ の親）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    ib = sub.add_parser("inbox").add_subparsers(dest="sub", required=True)
    s = ib.add_parser("add"); s.add_argument("text"); s.add_argument("--source", default="会話")
    s = ib.add_parser("resolve"); s.add_argument("id"); s.add_argument("--to", required=True)
    ib.add_parser("list")

    cm = sub.add_parser("commitment").add_subparsers(dest="sub", required=True)
    s = cm.add_parser("add")
    s.add_argument("--kind", choices=list(KIND_SECTION), required=True)
    s.add_argument("--content", required=True); s.add_argument("--who"); s.add_argument("--due")
    s.add_argument("--follow"); s.add_argument("--rel"); s.add_argument("--state")
    s = cm.add_parser("set-state"); s.add_argument("id"); s.add_argument("state")
    s = cm.add_parser("list"); s.add_argument("--rel"); s.add_argument("--state"); s.add_argument("--json", action="store_true")

    pe = sub.add_parser("person").add_subparsers(dest="sub", required=True)
    s = pe.add_parser("observe"); s.add_argument("ident"); s.add_argument("--situation", required=True)
    s.add_argument("--judgment", required=True); s.add_argument("--reason")
    s = pe.add_parser("note"); s.add_argument("ident"); s.add_argument("--situation", required=True); s.add_argument("--insight", required=True)
    s = pe.add_parser("predict"); s.add_argument("ident"); s.add_argument("--case", required=True)
    s.add_argument("--prediction", required=True); s.add_argument("--verify-date", required=True)

    de = sub.add_parser("decision").add_subparsers(dest="sub", required=True)
    s = de.add_parser("add")
    for opt in ("--project", "--title", "--content", "--reason", "--prediction", "--verify-date"):
        s.add_argument(opt, required=True)
    s.add_argument("--impact"); s.add_argument("--revoke")
    s.add_argument("--status", choices=["proposed", "decided"], default="proposed")
    s = de.add_parser("set-status"); s.add_argument("project"); s.add_argument("title"); s.add_argument("status")

    sub.add_parser("check")

    a = ap.parse_args(argv)
    root = (a.root or default_root()).resolve()
    if not (root / "memory").is_dir():
        fail(f"{root} に memory/ が無い（--root を確認）")

    if a.cmd == "inbox":
        {"add": lambda: inbox_add(root, a.text, a.source),
         "resolve": lambda: inbox_resolve(root, a.id, a.to),
         "list": lambda: inbox_list(root)}[a.sub]()
    elif a.cmd == "commitment":
        {"add": lambda: commitment_add(root, a),
         "set-state": lambda: commitment_set_state(root, a.id, a.state),
         "list": lambda: commitment_list(root, a)}[a.sub]()
    elif a.cmd == "person":
        {"observe": lambda: person_observe(root, a),
         "note": lambda: person_note(root, a),
         "predict": lambda: person_predict(root, a)}[a.sub]()
    elif a.cmd == "decision":
        {"add": lambda: decision_add(root, a),
         "set-status": lambda: decision_set_status(root, a.project, a.title, a.status)}[a.sub]()
    elif a.cmd == "check":
        check(root)


if __name__ == "__main__":
    main()
