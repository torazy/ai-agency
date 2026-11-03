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
  python tools/mem.py commitment set-state C-003 done      # set-due / set-follow で期限・フォロー日の変更
  python tools/mem.py commitment list [--rel P:x] [--state open] [--json]
  python tools/mem.py person new suzuki-hanako-kakuu-corp-soumu --name "鈴木 花子" --org "架空株式会社 総務部" --email ...
  python tools/mem.py person observe kakuu-ichiro-kakuu-corp-bucho --situation "..." --judgment "..." --reason "...（推測）"
  python tools/mem.py person note    kakuu-ichiro-kakuu-corp-bucho --situation "..." --insight "..."
  python tools/mem.py person predict kakuu-ichiro-kakuu-corp-bucho --case "..." --prediction "..." --verify-date 2026-11-20
  python tools/mem.py decision add --project kintai-saas --title "..." --content "..." --reason "..." \
         --impact "..." --prediction "..." --verify-date 2026-12-01 [--status proposed|decided] [--revoke "..."]
  python tools/mem.py decision set-status kintai-saas "<タイトル>" decided
  python tools/mem.py check
  python tools/mem.py migrate     # 旧雛形からの移行（本体アップデート後に1回。冪等）

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


def ensure_rel_column(lines: list[str], header_idx: int, end_idx: int) -> list[str]:
    """旧雛形の「出した指示」表には関連列が無い。無ければ状態列の直前に挿入し、既存行には '-' を入れる。"""
    hdr = cells(lines[header_idx])
    if "関連" in hdr or "状態" not in hdr:
        return lines
    pos = hdr.index("状態")
    hdr.insert(pos, "関連")
    lines[header_idx] = "| " + " | ".join(hdr) + " |"
    lines[header_idx + 1] = "|---" * len(hdr) + "|"
    for i in range(header_idx + 2, end_idx):
        c = cells(lines[i])
        if PLACEHOLDER_RE.match(lines[i]):
            lines[i] = "| （未登録） |" + " |" * (len(hdr) - 1)
        elif len(c) == len(hdr) - 1:
            c.insert(pos, "-")
            lines[i] = "| " + " | ".join(c) + " |"
    return lines


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
    lines = ensure_rel_column(lines, h, e)
    cid = next_id(lines, "C")
    rel = (a.rel or "-").strip("`")
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


def _commitment_update_cell(root: Path, cid: str, column: str, value: str, label: str) -> None:
    """ID で行を特定し、ヘッダ名 column のセルを value に置き換える。"""
    p = commitments_path(root)
    lines = read(p)
    header: list[str] = []
    for i, ln in enumerate(lines):
        if TABLE_ROW_RE.match(ln) and i + 1 < len(lines) and TABLE_SEP_RE.match(lines[i + 1]):
            header = cells(ln)
        elif TABLE_ROW_RE.match(ln) and cells(ln)[:1] == [cid]:
            c = cells(ln)
            if column not in header:
                fail(f"{cid} の表に『{column}』列が無い")
            c[header.index(column)] = esc(value)
            lines[i] = "| " + " | ".join(c) + " |"
            write(p, touch_updated(lines))
            ok(f"{cid} の{label}を {value} に")
            return
    fail(f"{cid} が見つからない")


def commitment_set_state(root: Path, cid: str, state: str) -> None:
    if state not in STATES:
        fail(f"状態は {sorted(STATES)} のいずれか")
    _commitment_update_cell(root, cid, "状態", state, "状態")


def commitment_set_due(root: Path, cid: str, due: str) -> None:
    _commitment_update_cell(root, cid, "期限", due, "期限")


def commitment_set_follow(root: Path, cid: str, follow: str) -> None:
    _commitment_update_cell(root, cid, "フォロー日", follow, "フォロー日")


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
def person_new(root: Path, a: argparse.Namespace) -> None:
    """_template.md から人物ファイルを作る。識別子は {姓}-{名}-{所属略称}-{役職略称}（役職不明なら部署略称）。"""
    ident = a.ident.removesuffix(".md")
    if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+){3,}", ident):
        fail("識別子は英小文字・数字・ハイフンで {姓}-{名}-{所属略称}-{役職略称} の4要素以上（例: tanaka-kenji-abc-corp-cfo）")
    dst = root / "memory" / "people" / f"{ident}.md"
    if dst.exists():
        fail(f"既に存在する: {dst.relative_to(root)}（同一人物なら追記、別人なら識別子を変える）")
    tpl = root / "memory" / "people" / "_template.md"
    if not tpl.exists():
        fail("memory/people/_template.md が無い")
    lines = read(tpl)
    fills = {
        "**氏名**": a.name, "**読み仮名**": a.kana, "**役職**": a.title, "**所属組織**": a.org,
        "**メールアドレス**": a.email, "**Backlogユーザー名**": a.backlog, "**GitHubアカウント**": a.github,
        "**Slackアカウント名**": a.slack, "**初めて関わった日**": today(), "**関係の種別**": a.relation,
    }
    out = []
    for ln in lines:
        if ln.startswith("# 人物ファイル テンプレート"):
            out.append(f"# {a.name}"); continue
        if ln.startswith("> このテンプレートをコピー") or ln.startswith("> **ファイル名は氏名だけにしない**") \
           or ln.startswith("> 例: ") or ln.startswith("> 同姓同名") or ln.strip() == ">":
            continue
        for key, val in fills.items():
            if ln.startswith(f"- {key}:") and val:
                ln = f"- {key}: {esc(val)}"
        out.append(ln)
    if not any(ln.startswith("| （未記録） | 新規作成") for ln in out):
        pass
    out = [ln.replace("| （未記録） | 新規作成 |", f"| {today()} | 新規作成（mem.py person new） |") for ln in out]
    write(dst, out)
    ok(f"人物ファイルを作成: {dst.relative_to(root).as_posix()}（成熟度 Lv0。dashboard.md の人物表への追加は別途）")


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


# ---------------------------------------------------------------- migrate
def migrate(root: Path) -> None:
    """旧雛形からの移行：commitments.md の3表に ID 列を足し、既存行に ID を振る。inbox.md が無ければ作る。冪等。"""
    changed = []
    p = root / "memory" / "commitments.md"
    if p.exists():
        lines = read(p)
        touched = False
        for sec_name in KIND_SECTION.values():
            sec = find_section(lines, sec_name)
            if sec < 0:
                continue
            h, e = find_table(lines, sec)
            if h < 0:
                continue
            before = lines[h]
            lines, _ = ensure_id_column(lines, h, e)
            lines = ensure_rel_column(lines, h, e)
            touched |= lines[h] != before
        if touched:
            write(p, lines)
            changed.append("commitments.md に ID 列を追加")
    if not (root / "memory" / "inbox.md").exists():
        inbox_path(root)
        changed.append("inbox.md を作成")
    # 関連欄の表記を統一（旧行の `P:x` のバッククォートを外す）
    if p.exists():
        lines = read(p)
        norm = [re.sub(r"`([^`]*)`", r"\1", ln) if ln.startswith("| C-") else ln for ln in lines]
        if norm != lines:
            write(p, norm)
            changed.append("関連欄の表記を統一")
    # decisions.md の各項目に状態行が無ければ補う。見出し・直後の数行に「未承認」「提案」「案）」があれば提案扱い
    for d in sorted((root / "memory" / "projects").glob("*/decisions.md")) if (root / "memory" / "projects").exists() else []:
        lines = read(d)
        out, added = [], 0
        for i, ln in enumerate(lines):
            out.append(ln)
            if ln.startswith("## ") and re.match(r"## \d{4}-\d{2}-\d{2}:", ln):
                nxt = lines[i + 1] if i + 1 < len(lines) else ""
                if not nxt.startswith("- **状態**:"):
                    window = " ".join(lines[i:i + 4])
                    label = "提案（未承認）" if re.search(r"未承認|提案|検討中", window) else "決定"
                    out.append(f"- **状態**: {label}（移行時に補完）")
                    added += 1
        if added:
            write(d, out)
            changed.append(f"{d.parent.name}/decisions.md に状態行を {added} 件補完")
    print("📝 移行: " + ("、".join(changed) if changed else "変更なし（既に最新）"))


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
    s = cm.add_parser("set-due"); s.add_argument("id"); s.add_argument("due")
    s = cm.add_parser("set-follow"); s.add_argument("id"); s.add_argument("follow")
    s = cm.add_parser("list"); s.add_argument("--rel"); s.add_argument("--state"); s.add_argument("--json", action="store_true")

    pe = sub.add_parser("person").add_subparsers(dest="sub", required=True)
    s = pe.add_parser("new"); s.add_argument("ident"); s.add_argument("--name", required=True)
    for opt in ("--kana", "--title", "--org", "--email", "--backlog", "--github", "--slack"):
        s.add_argument(opt)
    s.add_argument("--relation", default="社内同僚", help="社内同僚 / 顧客担当者 / 外部ベンダー / パートナー / その他")
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
    sub.add_parser("migrate", help="旧雛形からの移行（commitments の ID 列・inbox.md・decisions の状態行）。冪等")

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
         "set-due": lambda: commitment_set_due(root, a.id, a.due),
         "set-follow": lambda: commitment_set_follow(root, a.id, a.follow),
         "list": lambda: commitment_list(root, a)}[a.sub]()
    elif a.cmd == "person":
        {"new": lambda: person_new(root, a),
         "observe": lambda: person_observe(root, a),
         "note": lambda: person_note(root, a),
         "predict": lambda: person_predict(root, a)}[a.sub]()
    elif a.cmd == "decision":
        {"add": lambda: decision_add(root, a),
         "set-status": lambda: decision_set_status(root, a.project, a.title, a.status)}[a.sub]()
    elif a.cmd == "check":
        check(root)
    elif a.cmd == "migrate":
        migrate(root)


if __name__ == "__main__":
    main()
