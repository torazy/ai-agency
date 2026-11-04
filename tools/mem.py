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
  python tools/mem.py commitment set-state C-003 done      # set-due / set-follow / set-content で期限・フォロー日・内容の変更
  python tools/mem.py commitment add --kind given --content "競合3社の料金調査" --who @generalist --due 2026-11-10   # エージェントに任せる
  python tools/mem.py commitment list [--rel P:x] [--state open] [--json]
  python tools/mem.py person new suzuki-hanako-kakuu-corp-soumu --name "鈴木 花子" --org "架空株式会社 総務部" --email ...
  python tools/mem.py person observe kakuu-ichiro-kakuu-corp-bucho --situation "..." --judgment "..." --reason "...（推測）"
  python tools/mem.py person note    kakuu-ichiro-kakuu-corp-bucho --situation "..." --insight "..."
  python tools/mem.py person predict kakuu-ichiro-kakuu-corp-bucho --case "..." --prediction "..." --verify-date 2026-11-20
  python tools/mem.py decision add --project kintai-saas --title "..." --content "..." --reason "..." \
         --impact "..." --prediction "..." --verify-date 2026-12-01 [--status proposed|decided] [--revoke "..."]
  python tools/mem.py decision set-status kintai-saas D-003 decided   # ID（または題名の完全一致）で指定
  python tools/mem.py person add-alias kakuu-ichiro-kakuu-corp-bucho "部長"   # 呼び名（recall.py の名前解決）
  python tools/mem.py term add PSR --meaning "パイプライン状況報告（週次の営業会議資料）" --aliases "パイプライン報告" --kind 略語
  python tools/mem.py rule add --content "経費精算は翌月5営業日までに申請" --source "経費規程 第4条" --scope 全社
  python tools/mem.py rule confirm R-001        # 今も有効と確かめた ／ rule set-state R-001 retired で廃止
  python tools/mem.py check
  （探す・読む・点検するは tools/recall.py。このファイルは書き込み専用）
  python tools/mem.py migrate     # 旧雛形からの移行（本体アップデート後に1回。冪等）

共通オプション：--root <path>（memory/ を含むリポジトリルート。既定は自身の位置から推定。テストに使う）
出力は UTF-8。成功時は「📝 記録: ...」を1行。失敗は非ゼロ終了。
"""

from __future__ import annotations

import argparse
import functools
import json
import os
import re
import sys
import tempfile
import threading
import time
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


def write_text_atomic(path: Path, text: str) -> None:
    """一時ファイルに書いてから置き換える。読み手は「前の内容」か「新しい内容」のどちらかだけを見る（途中の空ファイルを見ない）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        for _ in range(100):  # Windows では読み手が開いている間 replace が失敗するので少し待つ
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                time.sleep(0.02)
        raise MemError(f"{path.name} を置き換えられない（他のプログラムが開いている）")
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def write(path: Path, lines: list[str]) -> None:
    write_text_atomic(path, "\n".join(lines).rstrip("\n") + "\n")


# 書き込みの排他。ボード（複数スレッド）・Claude の CLI・無人実行が同じ markdown に書くので、
# 「ロック → 読む → 変える → 原子的に書く → 解放」を1単位にする。プロセス間は memory/.mem.lock、スレッド間は RLock。
LOCK_WAIT_SEC = 20
LOCK_STALE_SEC = 120
_THREAD_LOCK = threading.RLock()
_DEPTH = threading.local()


def _acquire_file_lock(root: Path) -> Path:
    lock = root / "memory" / ".mem.lock"
    deadline = time.time() + LOCK_WAIT_SEC
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w") as f:
                f.write(f"{os.getpid()} {time.time():.0f}\n")
            return lock
        except (FileExistsError, PermissionError):
            # PermissionError：Windows では、別プロセスが消している最中のロックファイルを作り直そうとするとこれになる（使用中と同じ扱い）
            try:
                if time.time() - lock.stat().st_mtime > LOCK_STALE_SEC:  # 落ちたプロセスの置き土産
                    os.remove(lock)
                    continue
            except FileNotFoundError:
                continue
            except PermissionError:
                pass
            if time.time() > deadline:
                raise MemError("memory/ が他の書き込みでロック中（20秒待っても空かない）。少し待って再実行する")
            time.sleep(0.02)


def locked(fn):
    """memory/ を書き換える関数に付ける。入れ子で呼ばれても1回だけロックする。"""
    @functools.wraps(fn)
    def wrapper(root: Path, *args, **kwargs):
        with _THREAD_LOCK:
            depth = getattr(_DEPTH, "n", 0)
            lock = _acquire_file_lock(root) if depth == 0 else None
            _DEPTH.n = depth + 1
            try:
                return fn(root, *args, **kwargs)
            finally:
                _DEPTH.n = depth
                if lock is not None:
                    try:
                        os.remove(lock)
                    except FileNotFoundError:
                        pass
    return wrapper


DATE_ARG_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def check_date(value: str | None, label: str, allow_none: bool = True) -> str | None:
    """期限・フォロー日は YYYY-MM-DD か「なし」。「来週」「明日」のような相対表現は呼び出し側で日付に直す。"""
    if value is None or value == "":
        return None
    if allow_none and value in ("なし", "-"):
        return "なし"
    if not DATE_ARG_RE.match(value):
        fail(f"{label} は YYYY-MM-DD で指定する（「{value}」は不可。相対表現は日付に直してから渡す）")
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        fail(f"{label}「{value}」は存在しない日付")
    return value


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


def _ids_in(lines: list[str], prefix: str) -> list[int]:
    return [
        int(m.group(1))
        for ln in lines
        if ln.startswith("|") or ln.startswith("- [")
        for m in re.finditer(rf"\b{re.escape(prefix)}-(\d{{3,}})\b", ln)
    ]


def next_id(lines: list[str], prefix: str, root: Path | None = None) -> str:
    """表の行（| …）とリスト項目（- [ ] / - [x]）に現れる ID だけを数える（説明文中の例 C-001 は無視）。
    root を渡すと memory/ 配下の archive/ も数える（退避した ID を再利用しない）。"""
    nums = _ids_in(lines, prefix)
    if root is not None:
        for f in (root / "memory").rglob("archive/*.md"):
            try:
                nums += _ids_in(f.read_text(encoding="utf-8").splitlines(), prefix)
            except OSError:
                pass
    return f"{prefix}-{(max(nums) + 1) if nums else 1:03d}"


class MemError(Exception):
    """利用者に見せる失敗（入力不備・対象なし）。CLI は ❌ を出して exit 1、ボードは 400 で返す。"""


LAST_OK: list[str] = []  # ライブラリ利用時に ok() の文言を拾うためのバッファ（直近の呼び出し分）


def ok(msg: str) -> None:
    LAST_OK.append(msg)
    line = f"📝 記録: {msg}"
    try:
        print(line)
    except UnicodeEncodeError:  # ライブラリとして cp932 等の端末から呼ばれたとき。記録自体は済んでいるので UTF-8 のまま書き出す
        buf = getattr(sys.stdout, "buffer", None)
        if buf is not None:
            buf.write((line + "\n").encode("utf-8"))
            buf.flush()


def fail(msg: str, code: int = 1) -> None:
    raise MemError(msg)


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


@locked
def inbox_add(root: Path, text: str, source: str) -> None:
    if not (text or "").strip():
        fail("inbox に入れる内容が空")
    if len(text) > 1000:
        fail("inbox の1件は1000字まで（長文は memos/ にファイルで置き、inbox には要点とパス）")
    p = inbox_path(root)
    lines = read(p)
    # 同じ文面が未分類に残っていれば足さない（/update の無人取り込みを何度回しても inbox が膨らまないように）
    norm = lambda t: re.sub(r"\s+", "", t)
    for ln in lines:
        m = re.match(r"^- \[ \] (I-\d+) .*?「(.*)」（出所:", ln)
        if m and norm(m.group(2)) == norm(esc(text)):
            ok(f"inbox {m.group(1)} に同じ内容が未分類で残っている（追加しない）")
            return
    iid = next_id(lines, "I", root)
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


@locked
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
STATES = {"open", "in-progress", "waiting", "done", "dropped"}  # dropped = 取り下げ（誤記録・不要）
CLOSED = {"done", "dropped"}


def commitments_path(root: Path) -> Path:
    p = root / "memory" / "commitments.md"
    if not p.exists():
        fail("memory/commitments.md が無い（雛形から作成してください）")
    return p


def ensure_id_column(lines: list[str], header_idx: int, end_idx: int, root: Path | None = None) -> tuple[list[str], int]:
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
            cid = next_id(lines, "C", root)
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


@locked
def commitment_add(root: Path, a: argparse.Namespace) -> None:
    if a.kind not in KIND_SECTION:
        fail(f"種別は {list(KIND_SECTION)} のいずれか")
    if not (a.content or "").strip():
        fail("--content（内容）が空")
    a.due = check_date(a.due, "--due（期限）")
    a.follow = check_date(a.follow, "--follow（フォロー日）", allow_none=False)
    p = commitments_path(root)
    lines = read(p)
    sec = find_section(lines, KIND_SECTION[a.kind])
    if sec < 0:
        fail(f"commitments.md に『{KIND_SECTION[a.kind]}』節が無い")
    h, e = find_table(lines, sec)
    if h < 0:
        fail(f"『{KIND_SECTION[a.kind]}』節に表が無い")
    lines, e = ensure_id_column(lines, h, e, root)
    lines = ensure_rel_column(lines, h, e)
    cid = next_id(lines, "C", root)
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
        follow = a.follow
        if a.who.startswith("@"):
            # エージェントに任せる作業。相手は .claude/agents/{handle}.md にいる専門家・汎用秘書
            handle = a.who[1:].strip()
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", handle) or not (root / ".claude" / "agents" / f"{handle}.md").exists():
                known = sorted(f.stem for f in (root / ".claude" / "agents").glob("*.md")) if (root / ".claude" / "agents").exists() else []
                fail(f"エージェント「{a.who}」がいない（いるのは {', '.join('@' + k for k in known) or 'なし'}）")
            follow = follow or (due if due != "なし" else "なし")
        elif not follow:
            fail("--follow（フォロー日）が必要。期限の1〜2営業日前を提案する")
        row = [cid, today(), a.who, a.content, due, follow, rel, state]
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


@locked
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
    due = check_date(due, "期限") or "なし"
    _commitment_update_cell(root, cid, "期限", due, "期限")


@locked
def commitment_set_content(root: Path, cid: str, content: str) -> None:
    """約束の内容を書き換える（分かったことを反映する。例「PSR の意味は確認中」→「パイプライン状況報告を提出」）。"""
    content = (content or "").strip()
    if not content or len(content) > 300:
        fail("内容は1〜300字")
    p = commitments_path(root)
    lines = read(p)
    header: list[str] = []
    for i, ln in enumerate(lines):
        if TABLE_ROW_RE.match(ln) and i + 1 < len(lines) and TABLE_SEP_RE.match(lines[i + 1]):
            header = cells(ln)
        elif TABLE_ROW_RE.match(ln) and cells(ln)[:1] == [cid]:
            col = "タスク" if "タスク" in header else "内容"
            c = cells(ln)
            c[header.index(col)] = esc(content)
            lines[i] = "| " + " | ".join(c) + " |"
            write(p, touch_updated(lines))
            ok(f"{cid} の内容を「{content}」に")
            return
    fail(f"{cid} が見つからない")


def commitment_set_follow(root: Path, cid: str, follow: str) -> None:
    follow = check_date(follow, "フォロー日", allow_none=False)
    if not follow:
        fail("フォロー日が空")
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


# ---------------------------------------------------------------- sync（/update の取り込み範囲）
def sync_path(root: Path) -> Path:
    return root / "memory" / "sync-state.json"


def sync_load(root: Path) -> dict:
    try:
        return json.loads(sync_path(root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def sync_show(root: Path, as_json: bool) -> None:
    st = sync_load(root)
    if as_json:
        print(json.dumps(st, ensure_ascii=False, indent=2))
        return
    if not st:
        print("取り込み記録なし（/update は既定で直近7日を見る）")
        return
    for src, at in sorted(st.items()):
        print(f"{src:12s} {at}")


@locked
def sync_mark(root: Path, source: str, at: str | None) -> None:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,30}", source):
        fail("source は英小文字の短い名前（calendar / gmail / slack / backlog / github など）")
    at = at or datetime.now().strftime("%Y-%m-%dT%H:%M")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}(T\d{2}:\d{2})?", at):
        fail("--at は YYYY-MM-DD または YYYY-MM-DDTHH:MM")
    st = sync_load(root)
    st[source] = at
    write_text_atomic(sync_path(root), json.dumps(st, ensure_ascii=False, indent=2) + "\n")
    ok(f"{source} の取り込みを {at} まで済みとして記録")


# ---------------------------------------------------------------- people
@locked
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
        "**アイコン**": getattr(a, "icon", None), "**呼び名**": getattr(a, "alias", None),
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


IDENT_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)+$")


def person_path(root: Path, ident: str) -> Path:
    ident = (ident or "").strip().removesuffix(".md")
    if "/" in ident or "\\" in ident:  # memory/people/{識別子}.md の形で渡されたとき
        ident = ident.replace("\\", "/").rsplit("/", 1)[-1]
    if not IDENT_RE.match(ident):
        fail(f"人物の識別子が不正: 「{ident}」（英小文字・数字・ハイフン。例: tanaka-kenji-abc-corp-cfo）")
    p = root / "memory" / "people" / f"{ident}.md"
    if not p.exists():
        fail(f"人物ファイルが無い: {p}（_template.md から作成し、識別子を記録してください）")
    return p


def _norm_cell(s: str) -> str:
    return re.sub(r"\s+", "", esc(s or ""))


@locked
def person_append(root: Path, ident: str, heading: str, row: list[str], label: str) -> bool:
    """表に1行足す。日付以外の主要な欄（2〜3列目）が同じ行が既にあれば足さない（同じ観察の二重記録を防ぐ）。"""
    if not all((c or "").strip() for c in row[1:3]):
        fail(f"{label}の主要な欄が空")
    p = person_path(root, ident)
    lines = read(p)
    sec = find_section(lines, heading)
    if sec < 0:
        fail(f"{p.name} に『{heading}』が無い")
    h, e = find_table(lines, sec)
    if h < 0:
        fail(f"『{heading}』に表が無い")
    key = [_norm_cell(c) for c in row[1:3]]
    for ln in lines[h + 2:e]:
        c = cells(ln)
        if len(c) >= 3 and [_norm_cell(x) for x in c[1:3]] == key:
            ok(f"{p.name} の{label}に同じ内容が既にある（追加しない）")
            return False
    lines = append_row(lines, h, e, row)
    write(p, lines)
    ok(f"{p.name} の{label}に追記")
    return True


@locked
def person_observe(root: Path, a: argparse.Namespace) -> None:
    if not person_append(root, a.ident, "判断事例ログ", [today(), a.situation, a.judgment, a.reason or "（不明）"], "判断事例ログ"):
        return
    # 成熟度の再評価の目安を出す
    p = person_path(root, a.ident)
    lines = read(p)
    sec = find_section(lines, "判断事例ログ")
    h, e = find_table(lines, sec)
    n = sum(1 for ln in lines[h + 2:e] if not PLACEHOLDER_RE.match(ln))
    if n >= 5:
        print(f"   判断事例が {n} 件。成熟度 Lv2 以上の要件を満たす（人物ファイルの成熟度行を確認）")


@locked
def person_add_alias(root: Path, ident: str, alias: str) -> None:
    """基本情報の「呼び名」に追加する（無ければ関係の種別の直後に作る）。tools/recall.py の名前解決に使う。"""
    alias = alias.strip()
    if not alias or "|" in alias or len(alias) > 30:
        fail("呼び名は30字以内（「|」不可）")
    p = person_path(root, ident)
    lines = read(p)
    for i, ln in enumerate(lines):
        if ln.startswith("- **呼び名**:"):
            cur = ln.split(":", 1)[1].strip()
            names = [] if (not cur or cur.startswith("（")) else [x.strip() for x in re.split(r"[、,]", cur) if x.strip()]
            if alias in names:
                ok(f"{p.name} には既に呼び名「{alias}」がある")
                return
            lines[i] = "- **呼び名**: " + "、".join(names + [esc(alias)])
            break
    else:
        for i, ln in enumerate(lines):
            if ln.startswith("- **関係の種別**:"):
                lines.insert(i + 1, f"- **呼び名**: {esc(alias)}")
                break
        else:
            fail(f"{p.name} に基本情報の『関係の種別』行が無い")
    write(p, lines)
    ok(f"{p.name} に呼び名「{alias}」を追加")


@locked
def person_set_icon(root: Path, ident: str, icon: str) -> None:
    """基本情報の「アイコン」行を更新する（無ければ関係の種別の直後に挿入）。絵文字1つかボードのアイコン名。"""
    icon = icon.strip()
    if not icon or len(icon) > 24 or "|" in icon:
        fail("アイコンは絵文字1つ、またはボードのアイコン名（英小文字）を指定する")
    p = person_path(root, ident)
    lines = read(p)
    for i, ln in enumerate(lines):
        if ln.startswith("- **アイコン**:"):
            lines[i] = f"- **アイコン**: {esc(icon)}"
            break
    else:
        for i, ln in enumerate(lines):
            if ln.startswith("- **関係の種別**:"):
                lines.insert(i + 1, f"- **アイコン**: {esc(icon)}")
                break
        else:
            fail(f"{p.name} に基本情報の『関係の種別』行が無い")
    write(p, lines)
    ok(f"{p.name} のアイコンを {icon} に設定")


def person_note(root: Path, a: argparse.Namespace) -> None:
    person_append(root, a.ident, "過去のやりとり・気づき", [today(), a.situation, a.insight], "気づき")


def person_predict(root: Path, a: argparse.Namespace) -> None:
    person_append(root, a.ident, "反応予測と検証", [today(), a.case, a.prediction, a.verify_date, "（未検証）", "", ""], "反応予測")


# ---------------------------------------------------------------- knowledge（用語集・業務ルール）
GLOSSARY_SKELETON = """# 用語集（glossary.md）

> 社内の略語・用語・コードネーム・あだ名と、その意味。依頼を実行する前に `recall.py decode` で解読する。
> 追加・更新は `python tools/mem.py term add|set`。

**最終更新**: （未記録）

## 用語

| 用語 | 別表記 | 意味 | 種別 | 範囲 | 出典 | 更新日 |
|---|---|---|---|---|---|---|
| （未登録） | | | | | | |
"""
RULES_SKELETON = """# 業務ルール（rules.md）

> 社内規程・手順・締め日・承認の流れなど。出典と確認日を必ず書く。追加・更新は `python tools/mem.py rule add|set-state|confirm`。

**最終更新**: （未記録）

## ルール

| ID | 内容 | 適用範囲 | 出典 | 有効日 | 確認日 | 状態 |
|---|---|---|---|---|---|---|
| （未登録） | | | | | | |
"""
TERM_KINDS = {"略語", "社内用語", "コードネーム", "あだ名", "業界用語"}
TERM_FIELDS = {"meaning": "意味", "aliases": "別表記", "kind": "種別", "scope": "範囲", "source": "出典"}


def knowledge_path(root: Path, name: str) -> Path:
    p = root / "memory" / "knowledge" / name
    if not p.exists():
        write_text_atomic(p, GLOSSARY_SKELETON if name == "glossary.md" else RULES_SKELETON)
    return p


def _table(lines: list[str], heading: str, path: Path) -> tuple[int, int]:
    sec = find_section(lines, heading)
    h, e = find_table(lines, sec) if sec >= 0 else (-1, -1)
    if h < 0:
        fail(f"{path.name} に『{heading}』の表が無い")
    return h, e


def _norm_term(t: str) -> str:
    return re.sub(r"\s+", "", t or "").lower()


def _term_names(row: dict) -> set[str]:
    names = {_norm_term(row.get("用語", ""))}
    names |= {_norm_term(x) for x in re.split(r"[、,，/／]", row.get("別表記", "")) if x.strip()}
    return {n for n in names if n}


def glossary_rows(root: Path) -> list[dict]:
    p = root / "memory" / "knowledge" / "glossary.md"
    if not p.exists():
        return []
    lines = read(p)
    sec = find_section(lines, "## 用語")
    h, e = find_table(lines, sec) if sec >= 0 else (-1, -1)
    if h < 0:
        return []
    hdr = cells(lines[h])
    return [dict(zip(hdr, cells(ln))) for ln in lines[h + 2:e] if not PLACEHOLDER_RE.match(ln)]


@locked
def term_add(root: Path, a: argparse.Namespace) -> None:
    term = (a.term or "").strip()
    if not term or len(term) > 60:
        fail("用語は1〜60字")
    if not (a.meaning or "").strip() or len(a.meaning) > 300:
        fail("--meaning（意味）は1〜300字")
    if a.kind and a.kind not in TERM_KINDS:
        fail(f"--kind は {sorted(TERM_KINDS)} のいずれか")
    new_names = {_norm_term(term)} | {_norm_term(x) for x in re.split(r"[、,，/／]", a.aliases or "") if x.strip()}
    for r in glossary_rows(root):
        if _term_names(r) & new_names:
            fail(f"「{r.get('用語')}」が既に用語集にある（意味を直すなら term set、別の意味なら範囲を変えて登録）")
    p = knowledge_path(root, "glossary.md")
    lines = read(p)
    h, e = _table(lines, "## 用語", p)
    lines = append_row(lines, h, e, [term, a.aliases or "", a.meaning, a.kind or "社内用語", a.scope or "全社",
                                     a.source or "会話", today()])
    write(p, touch_updated(lines))
    ok(f"用語集に「{term}」＝{a.meaning}")


@locked
def term_set(root: Path, term: str, field_name: str, value: str) -> None:
    col = TERM_FIELDS.get(field_name)
    if not col:
        fail(f"項目は {sorted(TERM_FIELDS)} のいずれか")
    if field_name == "kind" and value not in TERM_KINDS:
        fail(f"種別は {sorted(TERM_KINDS)} のいずれか")
    p = knowledge_path(root, "glossary.md")
    lines = read(p)
    h, e = _table(lines, "## 用語", p)
    hdr = cells(lines[h])
    key = _norm_term(term)
    for i in range(h + 2, e):
        row = dict(zip(hdr, cells(lines[i])))
        if key in _term_names(row):
            c = cells(lines[i])
            c[hdr.index(col)] = esc(value)
            if "更新日" in hdr:
                c[hdr.index("更新日")] = today()
            lines[i] = "| " + " | ".join(c) + " |"
            write(p, touch_updated(lines))
            ok(f"用語「{row.get('用語')}」の{col}を更新")
            return
    fail(f"用語集に「{term}」が無い（term add で登録）")


@locked
def rule_add(root: Path, a: argparse.Namespace) -> None:
    content = (a.content or "").strip()
    if not content or len(content) > 400:
        fail("--content（ルールの内容）は1〜400字")
    if not (a.source or "").strip():
        fail("--source（出典：規程名・URL・誰から聞いたか）が必要。出典の無いルールは記録しない")
    effective = check_date(a.effective, "--effective（有効日）", allow_none=True) or "-"
    p = knowledge_path(root, "rules.md")
    lines = read(p)
    h, e = _table(lines, "## ルール", p)
    hdr = cells(lines[h])
    for ln in lines[h + 2:e]:
        row = dict(zip(hdr, cells(ln)))
        if _norm_term(row.get("内容", "")) == _norm_term(esc(content)) and row.get("状態") != "廃止":
            fail(f"同じルールが既にある（{row.get('ID')}）。確かめ直しただけなら rule confirm")
    rid = next_id(lines, "R", root)
    lines = append_row(lines, h, e, [rid, content, a.scope or "全社", a.source, effective, today(), "有効"])
    write(p, touch_updated(lines))
    ok(f"業務ルール {rid}「{content[:40]}」（出典 {a.source}）")


def _rule_update(root: Path, rid: str, updates: dict, label: str) -> None:
    p = knowledge_path(root, "rules.md")
    lines = read(p)
    h, e = _table(lines, "## ルール", p)
    hdr = cells(lines[h])
    for i in range(h + 2, e):
        c = cells(lines[i])
        if c[:1] == [rid]:
            for col, v in updates.items():
                c[hdr.index(col)] = esc(v)
            lines[i] = "| " + " | ".join(c) + " |"
            write(p, touch_updated(lines))
            ok(f"{rid} の{label}")
            return
    fail(f"{rid} が業務ルールに無い")


@locked
def rule_set_state(root: Path, rid: str, state: str) -> None:
    label = {"active": "有効", "retired": "廃止"}.get(state)
    if not label:
        fail("状態は active（有効）/ retired（廃止）")
    _rule_update(root, rid, {"状態": label, "確認日": today()}, f"状態を {label} に")


@locked
def rule_confirm(root: Path, rid: str) -> None:
    _rule_update(root, rid, {"確認日": today()}, "確認日を今日に（今も有効と確かめた）")


# ---------------------------------------------------------------- decisions
def decisions_path(root: Path, project: str) -> Path:
    p = root / "memory" / "projects" / project / "decisions.md"
    if not p.exists():
        write(p, [f"# 決定事項（{project}）", "",
                  "> 決定には必ず「予測（期待した結果）」と「検証日」を付す。/review-decisions が検証する。",
                  "> 状態は 提案（未承認）→ 決定 → 検証済。提案のまま放置された項目は /morning-brief が拾う。", ""])
    return p


DECISION_HEAD_RE = re.compile(r"^## (\d{4}-\d{2}-\d{2}): (.*)$")
DECISION_ID_RE = re.compile(r"^- \*\*ID\*\*: (D-\d{3,})\s*$")


def next_decision_id(root: Path) -> str:
    """決定の ID は全案件・過去分を通して一意（D-001…）。"""
    nums = []
    for f in (root / "memory" / "projects").rglob("*.md") if (root / "memory" / "projects").exists() else []:
        if f.name.startswith("decisions") or "archive" in f.parts:
            try:
                nums += [int(m.group(1)[2:]) for m in map(DECISION_ID_RE.match, f.read_text(encoding="utf-8").splitlines()) if m]
            except OSError:
                pass
    return f"D-{(max(nums) + 1) if nums else 1:03d}"


def _decision_blocks(lines: list[str]) -> list[dict]:
    """decisions.md の各ブロック：見出し行・終端・題名・ID・状態行の位置。"""
    blocks = []
    for i, ln in enumerate(lines):
        m = DECISION_HEAD_RE.match(ln)
        if m:
            blocks.append({"head": i, "title": m.group(2).strip(), "id": None, "status": None})
    for k, b in enumerate(blocks):
        end = blocks[k + 1]["head"] if k + 1 < len(blocks) else len(lines)
        b["end"] = end
        for j in range(b["head"] + 1, end):
            if lines[j].startswith("## "):
                b["end"] = j
                break
            mid = DECISION_ID_RE.match(lines[j])
            if mid:
                b["id"] = mid.group(1)
            if lines[j].startswith("- **状態**:") and b["status"] is None:
                b["status"] = j
    return blocks


@locked
def decision_add(root: Path, a: argparse.Namespace) -> None:
    if not (a.title or "").strip():
        fail("--title が空")
    a.verify_date = check_date(a.verify_date, "--verify-date（検証日）", allow_none=False)
    p = decisions_path(root, a.project)
    if any(re.sub(r"\s+", "", b["title"]) == re.sub(r"\s+", "", a.title) for b in _decision_blocks(read(p))):
        fail(f"「{a.title}」と同じ題の決定が既にある（同じ件なら set-status、別件なら題を変える）")
    status = "提案（未承認）" if a.status == "proposed" else "決定"
    did = next_decision_id(root)
    block = [
        "",
        f"## {today()}: {a.title}",
        f"- **状態**: {status}",
        f"- **ID**: {did}",
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
    ok(f"{a.project}/decisions.md に {did}「{a.title}」を{status}として記録（検証日 {a.verify_date}）")


@locked
def decision_set_status(root: Path, project: str, key: str, status: str) -> None:
    """key は決定の ID（D-001）か、題名の完全一致。部分一致では更新しない（「CRM導入」で「CRM導入見送り」を承認しない）。"""
    label = {"proposed": "提案（未承認）", "decided": "決定", "verified": "検証済"}.get(status)
    if not label:
        fail("状態は proposed / decided / verified")
    p = root / "memory" / "projects" / project / "decisions.md"
    if not p.exists():
        fail(f"案件 {project} に decisions.md が無い")
    lines = read(p)
    blocks = _decision_blocks(lines)
    key = (key or "").strip()
    if re.fullmatch(r"D-\d{3,}", key):
        hit = [b for b in blocks if b["id"] == key]
    else:
        nk = re.sub(r"\s+", "", key)
        hit = [b for b in blocks if re.sub(r"\s+", "", b["title"]) == nk]
    if not hit:
        near = [f"{b['id'] or '（IDなし）'}「{b['title']}」" for b in blocks if key and key in b["title"]]
        fail(f"「{key}」に完全一致する決定が無い" + (f"。候補: {' / '.join(near)}（ID で指定する）" if near else ""))
    if len(hit) > 1:
        fail(f"「{key}」に当たる決定が {len(hit)} 件ある: " + " / ".join(f"{b['id'] or '（IDなし）'}「{b['title']}」" for b in hit) + "。ID で指定する")
    b = hit[0]
    new = f"- **状態**: {label}（{today()}）"
    if b["status"] is not None:
        lines[b["status"]] = new
    else:
        lines.insert(b["head"] + 1, new)
    write(p, lines)
    ok(f"{b['id'] or ''}「{b['title']}」の状態を {label} に")


# ---------------------------------------------------------------- migrate
@locked
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
            lines, _ = ensure_id_column(lines, h, e, root)
            lines = ensure_rel_column(lines, h, e)
            touched |= lines[h] != before
        if touched:
            write(p, lines)
            changed.append("commitments.md に ID 列を追加")
    if not (root / "memory" / "inbox.md").exists():
        inbox_path(root)
        changed.append("inbox.md を作成")
    for name in ("glossary.md", "rules.md"):
        if not (root / "memory" / "knowledge" / name).exists():
            knowledge_path(root, name)
            changed.append(f"knowledge/{name} を作成")
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
    # 決定に ID が無ければ振る（日付順・全案件で一意）。部分一致の誤承認を防ぐため set-status は ID 指定が基本
    pending = []
    for d in sorted((root / "memory" / "projects").glob("*/decisions.md")) if (root / "memory" / "projects").exists() else []:
        for b in _decision_blocks(read(d)):
            if not b["id"]:
                pending.append((read(d)[b["head"]][3:13], str(d)))
    if pending:
        counter = int(next_decision_id(root)[2:])
        for path_s in sorted({ps for _, ps in pending}, key=lambda x: min(dt for dt, ps in pending if ps == x)):
            d = Path(path_s)
            lines = read(d)
            out, n = [], 0
            blocks = {b["head"]: b for b in _decision_blocks(lines)}
            for i, ln in enumerate(lines):
                out.append(ln)
                b = blocks.get(i)
                if b and not b["id"] and b["status"] is None:
                    out.append(f"- **ID**: D-{counter:03d}")
                    counter += 1
                    n += 1
                elif any(bb["status"] == i and not bb["id"] for bb in blocks.values()):
                    out.append(f"- **ID**: D-{counter:03d}")
                    counter += 1
                    n += 1
            write(d, out)
            changed.append(f"{d.parent.name}/decisions.md の決定 {n} 件に ID を付与")
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
    s = cm.add_parser("set-content"); s.add_argument("id"); s.add_argument("content")
    s = cm.add_parser("list"); s.add_argument("--rel"); s.add_argument("--state"); s.add_argument("--json", action="store_true")

    pe = sub.add_parser("person").add_subparsers(dest="sub", required=True)
    s = pe.add_parser("new"); s.add_argument("ident"); s.add_argument("--name", required=True)
    for opt in ("--kana", "--title", "--org", "--email", "--backlog", "--github", "--slack"):
        s.add_argument(opt)
    s.add_argument("--relation", default="社内同僚", help="社内同僚 / 顧客担当者 / 外部ベンダー / パートナー / その他")
    s.add_argument("--icon", help="絵文字1つ、またはボードのアイコン名（user / crown / briefcase …）")
    s.add_argument("--alias", help="呼び名（「、」区切り。例: 田中部長、健司さん）")
    s = pe.add_parser("add-alias"); s.add_argument("ident"); s.add_argument("alias")
    s = pe.add_parser("set-icon"); s.add_argument("ident"); s.add_argument("icon")
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
    s = de.add_parser("set-status"); s.add_argument("project"); s.add_argument("key", help="決定の ID（D-001）か題名の完全一致")
    s.add_argument("status")

    te = sub.add_parser("term", help="用語集（memory/knowledge/glossary.md）").add_subparsers(dest="sub", required=True)
    s = te.add_parser("add"); s.add_argument("term"); s.add_argument("--meaning", required=True)
    s.add_argument("--aliases", help="別表記（「、」区切り）"); s.add_argument("--kind", help="略語 / 社内用語 / コードネーム / あだ名 / 業界用語")
    s.add_argument("--scope", help="全社 / P:{案件} / 部署名"); s.add_argument("--source", help="出典（誰から・どの資料）")
    s = te.add_parser("set"); s.add_argument("term"); s.add_argument("field", choices=sorted(TERM_FIELDS)); s.add_argument("value")
    ru = sub.add_parser("rule", help="業務ルール（memory/knowledge/rules.md）").add_subparsers(dest="sub", required=True)
    s = ru.add_parser("add"); s.add_argument("--content", required=True); s.add_argument("--source", required=True)
    s.add_argument("--scope"); s.add_argument("--effective", help="有効日 YYYY-MM-DD")
    s = ru.add_parser("set-state"); s.add_argument("id"); s.add_argument("state", choices=["active", "retired"])
    s = ru.add_parser("confirm"); s.add_argument("id")
    sy = sub.add_parser("sync", help="/update の取り込み済み範囲（memory/sync-state.json）").add_subparsers(dest="sub", required=True)
    s = sy.add_parser("show"); s.add_argument("--json", action="store_true")
    s = sy.add_parser("mark"); s.add_argument("source"); s.add_argument("--at")

    sub.add_parser("check")
    sub.add_parser("migrate", help="旧雛形からの移行（commitments の ID 列・inbox.md・decisions の状態行）。冪等")

    a = ap.parse_args(argv)
    root = (a.root or default_root()).resolve()
    try:
        dispatch(root, a)
    except MemError as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)


# 無人実行（AI_AGENCY_UNATTENDED）で許すのは「追記」と読み取りだけ。既存行の書き換え・状態変更・新規人物・決定はしない
APPEND_ONLY = {("inbox", "add"), ("commitment", "add"), ("person", "observe"), ("person", "note"), ("sync", "mark")}
READ_ONLY = {("inbox", "list"), ("commitment", "list"), ("sync", "show"), ("check", None)}


def dispatch(root: Path, a: argparse.Namespace) -> None:
    if not (root / "memory").is_dir():
        fail(f"{root} に memory/ が無い（--root を確認）")
    if os.environ.get("AI_AGENCY_UNATTENDED"):
        op = (a.cmd, getattr(a, "sub", None))
        if op not in APPEND_ONLY | READ_ONLY:
            fail(f"無人実行では `{' '.join(x for x in op if x)}` はできない（追記と読み取りだけ）。人がいるときに行う")

    if a.cmd == "inbox":
        {"add": lambda: inbox_add(root, a.text, a.source),
         "resolve": lambda: inbox_resolve(root, a.id, a.to),
         "list": lambda: inbox_list(root)}[a.sub]()
    elif a.cmd == "commitment":
        {"add": lambda: commitment_add(root, a),
         "set-state": lambda: commitment_set_state(root, a.id, a.state),
         "set-due": lambda: commitment_set_due(root, a.id, a.due),
         "set-follow": lambda: commitment_set_follow(root, a.id, a.follow),
         "set-content": lambda: commitment_set_content(root, a.id, a.content),
         "list": lambda: commitment_list(root, a)}[a.sub]()
    elif a.cmd == "person":
        {"new": lambda: person_new(root, a),
         "set-icon": lambda: person_set_icon(root, a.ident, a.icon),
         "add-alias": lambda: person_add_alias(root, a.ident, a.alias),
         "observe": lambda: person_observe(root, a),
         "note": lambda: person_note(root, a),
         "predict": lambda: person_predict(root, a)}[a.sub]()
    elif a.cmd == "decision":
        {"add": lambda: decision_add(root, a),
         "set-status": lambda: decision_set_status(root, a.project, a.key, a.status)}[a.sub]()
    elif a.cmd == "term":
        {"add": lambda: term_add(root, a),
         "set": lambda: term_set(root, a.term, a.field, a.value)}[a.sub]()
    elif a.cmd == "rule":
        {"add": lambda: rule_add(root, a),
         "set-state": lambda: rule_set_state(root, a.id, a.state),
         "confirm": lambda: rule_confirm(root, a.id)}[a.sub]()
    elif a.cmd == "sync":
        {"show": lambda: sync_show(root, a.json),
         "mark": lambda: sync_mark(root, a.source, a.at)}[a.sub]()
    elif a.cmd == "check":
        check(root)
    elif a.cmd == "migrate":
        migrate(root)


if __name__ == "__main__":
    main()
