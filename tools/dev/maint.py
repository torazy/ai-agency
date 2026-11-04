#!/usr/bin/env python3
"""
maint.py ── 本体メンテナンスの状況台帳（tools/dev/memory/maint-state.md）と、メンテモードの点検。

メンテモード（本体フォルダで tools/dev/enable-maintenance.py を実行した状態）でだけ働く。インスタンスでは何もしない。
製品のコード（tools/mem.py など）には依存しない：製品を作り替えてもメンテの道具が壊れないように。設計は tools/dev/DESIGN.md。

  python tools/dev/maint.py start          会話の始めに：メンテ層の写し直し・分離の点検・状況（引き継ぎ・要求・未検証・稼働中・git・CI）
  python tools/dev/maint.py add "要求の要約" [--state 着手] [--next "次の一手"]
  python tools/dev/maint.py set R-003 受付|着手|確認待ち|保留|取り下げ [--next "…"]
  python tools/dev/maint.py note R-003 "やったこと" [--next "…"]
  python tools/dev/maint.py done R-003 --result "4b6d349 …" [--unverified "確かめていないこと" ...]
  python tools/dev/maint.py verify R-003 "確かめること"      /  verify --ok V-002 [--note "確かめた結果"]
  python tools/dev/maint.py run add "名前" --kind schtasks|cron|path|other --target X --stop "止め方"  /  run rm "名前"
  python tools/dev/maint.py handoff "続きの要点"             /  handoff --clear
  python tools/dev/maint.py check          分離の点検だけ（問題があれば終了コード 1。写し直しはしない）
  python tools/dev/maint.py git-hook <name>  enable-maintenance.py が入れる git フックから呼ばれる
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
STATES = ("受付", "着手", "確認待ち", "保留", "完了", "取り下げ")
OPEN = ("受付", "着手", "確認待ち", "保留")
KEEP_CLOSED = 6      # 台帳に残す完了・取り下げの数（古いものは maint-archive.md へ）
KEEP_LOG = 30        # 台帳に残す経過の行数
REQ_HEAD = ["ID", "受付", "要求", "状態", "現在地・次の一手", "結果", "更新"]
VER_HEAD = ["ID", "要求", "確かめること", "登録"]
RUN_HEAD = ["名前", "種類", "対象", "ホスト", "登録", "止め方"]

STATE_SKELETON = """# メンテナンスの状況（maint-state.md）

> `tools/dev/maint.py` が書く（手で直してもよい）。メンテモードでは CLAUDE.local.md から毎回読み込まれる。
> 会話が変わったら「引き継ぎ」と「要求」から再開する。完了から時間の経ったものは maint-archive.md へ移る。設計：tools/dev/DESIGN.md

## 引き継ぎ

（なし）

## 要求

| ID | 受付 | 要求 | 状態 | 現在地・次の一手 | 結果 | 更新 |
|---|---|---|---|---|---|---|
| （なし） | | | | | | |

## 未検証

| ID | 要求 | 確かめること | 登録 |
|---|---|---|---|
| （なし） | | | |

## 稼働中のもの

| 名前 | 種類 | 対象 | ホスト | 登録 | 止め方 |
|---|---|---|---|---|---|
| （なし） | | | | | |

## 経過

"""

ARCHIVE_SKELETON = """# メンテナンスの記録庫（maint-archive.md）

> maint-state.md から移した、完了した要求・確かめた事項・古い経過。`tools/dev/maint.py` が書く。

## 完了した要求

| ID | 受付 | 要求 | 状態 | 現在地・次の一手 | 結果 | 更新 |
|---|---|---|---|---|---|---|

## 確かめた事項

| ID | 要求 | 確かめること | 登録 |
|---|---|---|---|

## 経過

"""


class MaintError(Exception):
    pass


# ---------------------------------------------------------------- files
def state_path(root: Path) -> Path:
    return root / "tools" / "dev" / "memory" / "maint-state.md"


def archive_path(root: Path) -> Path:
    return root / "tools" / "dev" / "memory" / "maint-archive.md"


def is_maint(root: Path) -> bool:
    return (root / ".ai-agency-template").exists()


def today() -> str:
    return date.today().isoformat()


def esc(s: str) -> str:
    return " ".join((s or "").replace("|", "／").split())


def load(p: Path, skeleton: str) -> list[str]:
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(skeleton, encoding="utf-8", newline="\n")
    return p.read_text(encoding="utf-8").splitlines()


def save(p: Path, lines: list[str]) -> None:
    tmp = p.with_suffix(".tmp")
    tmp.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, p)


def _sec(lines: list[str], heading: str) -> tuple[int, int]:
    s = next((i for i, l in enumerate(lines) if l.strip() == f"## {heading}"), None)
    if s is None:
        raise MaintError(f"「## {heading}」が無い")
    e = next((i for i in range(s + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return s, e


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def rows(lines: list[str], heading: str) -> list[dict]:
    s, e = _sec(lines, heading)
    tl = [i for i in range(s + 1, e) if lines[i].lstrip().startswith("|")]
    if len(tl) < 2:
        return []
    head = _cells(lines[tl[0]])
    out = []
    for i in tl[2:]:
        c = _cells(lines[i])
        if not c or c[0] in ("（なし）", ""):
            continue
        out.append({**dict(zip(head, c + [""] * (len(head) - len(c)))), "_i": i})
    return out


def _row_line(head: list[str], r: dict) -> str:
    return "| " + " | ".join(esc(r.get(h, "")) for h in head) + " |"


def append(lines: list[str], heading: str, head: list[str], r: dict) -> list[str]:
    s, e = _sec(lines, heading)
    tl = [i for i in range(s + 1, e) if lines[i].lstrip().startswith("|")]
    if len(tl) < 2:
        raise MaintError(f"「## {heading}」に表が無い")
    for i in tl[2:]:
        if _cells(lines[i])[0] == "（なし）":
            lines[i] = _row_line(head, r)
            return lines
    lines.insert(tl[-1] + 1, _row_line(head, r))
    return lines


def update(lines: list[str], heading: str, head: list[str], key: str, fields: dict) -> dict:
    for r in rows(lines, heading):
        if r.get(head[0]) == key:
            r.update(fields)
            lines[r["_i"]] = _row_line(head, r)
            return r
    raise MaintError(f"{key} が「{heading}」に無い")


def remove(lines: list[str], heading: str, head: list[str], key: str) -> dict:
    for r in rows(lines, heading):
        if r.get(head[0]) == key:
            del lines[r["_i"]]
            if not rows(lines, heading):
                append(lines, heading, head, {head[0]: "（なし）"})
            return r
    raise MaintError(f"{key} が「{heading}」に無い")


def next_id(root: Path, prefix: str) -> str:
    n = 0
    for p in (state_path(root), archive_path(root)):
        if p.exists():
            for tok in p.read_text(encoding="utf-8").replace("|", " ").split():
                if tok.startswith(prefix + "-") and tok[len(prefix) + 1:].isdigit():
                    n = max(n, int(tok[len(prefix) + 1:]))
    return f"{prefix}-{n + 1:03d}"


def log(lines: list[str], text: str) -> None:
    s, e = _sec(lines, "経過")
    lines.insert(e if e == len(lines) else e, f"- {today()} {esc(text)}")


def section_text(lines: list[str], heading: str) -> str:
    s, e = _sec(lines, heading)
    return "\n".join(lines[s + 1:e]).strip()


def set_section(lines: list[str], heading: str, text: str) -> list[str]:
    s, e = _sec(lines, heading)
    return lines[:s + 1] + ["", text.strip() or "（なし）", ""] + lines[e:]


def compact(root: Path, lines: list[str]) -> list[str]:
    """古い完了・取り下げと古い経過を記録庫へ移し、台帳を短く保つ（毎回読み込まれるので）。"""
    arch = load(archive_path(root), ARCHIVE_SKELETON)
    closed = [r for r in rows(lines, "要求") if r.get("状態") in ("完了", "取り下げ")]
    moved = closed[:-KEEP_CLOSED] if len(closed) > KEEP_CLOSED else []
    for r in reversed(moved):
        del lines[r["_i"]]
    for r in moved:
        arch = append(arch, "完了した要求", REQ_HEAD, r)
    s, e = _sec(lines, "経過")
    items = [i for i in range(s + 1, e) if lines[i].startswith("- ")]
    old = items[:-KEEP_LOG] if len(items) > KEEP_LOG else []
    if old:
        a_s, a_e = _sec(arch, "経過")
        arch[a_e:a_e] = [lines[i] for i in old]
        for i in reversed(old):
            del lines[i]
    if moved or old:
        save(archive_path(root), arch)
    return lines


# ---------------------------------------------------------------- ledger operations
def _write(root: Path, lines: list[str]) -> None:
    save(state_path(root), compact(root, lines))


def req(lines: list[str], rid: str) -> dict:
    for r in rows(lines, "要求"):
        if r["ID"] == rid:
            return r
    raise MaintError(f"要求 {rid} が無い（python tools/dev/maint.py start で一覧）")


def add(root: Path, text: str, state: str = "受付", nxt: str = "") -> str:
    if state not in OPEN:
        raise MaintError(f"状態は {' / '.join(OPEN)}")
    lines = load(state_path(root), STATE_SKELETON)
    rid = next_id(root, "R")
    append(lines, "要求", REQ_HEAD, {"ID": rid, "受付": today(), "要求": text, "状態": state,
                                    "現在地・次の一手": nxt, "結果": "", "更新": today()})
    log(lines, f"{rid} 受付（{state}）：{text}")
    _write(root, lines)
    return rid


def set_state(root: Path, rid: str, state: str, nxt: str | None = None) -> None:
    if state not in STATES or state == "完了":
        raise MaintError("状態は 受付 / 着手 / 確認待ち / 保留 / 取り下げ（完了は done で）")
    lines = load(state_path(root), STATE_SKELETON)
    req(lines, rid)
    f = {"状態": state, "更新": today()}
    if nxt is not None:
        f["現在地・次の一手"] = nxt
    update(lines, "要求", REQ_HEAD, rid, f)
    log(lines, f"{rid} → {state}" + (f"：{nxt}" if nxt else ""))
    _write(root, lines)


def note(root: Path, rid: str, text: str, nxt: str | None = None) -> None:
    lines = load(state_path(root), STATE_SKELETON)
    req(lines, rid)
    f = {"更新": today()}
    if nxt is not None:
        f["現在地・次の一手"] = nxt
    update(lines, "要求", REQ_HEAD, rid, f)
    log(lines, f"{rid} {text}")
    _write(root, lines)


def done(root: Path, rid: str, result: str, unverified: list[str] | None = None) -> list[str]:
    if not result.strip():
        raise MaintError("完了には結果（コミットや確かめたこと）が要る")
    lines = load(state_path(root), STATE_SKELETON)
    req(lines, rid)
    update(lines, "要求", REQ_HEAD, rid, {"状態": "完了", "結果": result, "現在地・次の一手": "", "更新": today()})
    vids = []
    for u in unverified or []:
        vid = next_id(root, "V") if not vids else f"V-{int(vids[-1][2:]) + 1:03d}"
        append(lines, "未検証", VER_HEAD, {"ID": vid, "要求": rid, "確かめること": u, "登録": today()})
        vids.append(vid)
    log(lines, f"{rid} 完了：{result}" + (f"（未検証 {', '.join(vids)}）" if vids else ""))
    _write(root, lines)
    return vids


def verify_add(root: Path, rid: str, text: str) -> str:
    lines = load(state_path(root), STATE_SKELETON)
    vid = next_id(root, "V")
    append(lines, "未検証", VER_HEAD, {"ID": vid, "要求": rid, "確かめること": text, "登録": today()})
    log(lines, f"{vid}（{rid}）未検証に追加：{text}")
    _write(root, lines)
    return vid


def verify_ok(root: Path, vid: str, result: str = "") -> None:
    lines = load(state_path(root), STATE_SKELETON)
    r = remove(lines, "未検証", VER_HEAD, vid)
    arch = load(archive_path(root), ARCHIVE_SKELETON)
    r["確かめること"] = r.get("確かめること", "") + (f" → {result}" if result else "") + f"（{today()} 確認）"
    save(archive_path(root), append(arch, "確かめた事項", VER_HEAD, r))
    log(lines, f"{vid} 確認済み" + (f"：{result}" if result else ""))
    _write(root, lines)


def run_add(root: Path, name: str, kind: str, target: str, stop: str) -> None:
    if kind not in ("schtasks", "cron", "path", "other"):
        raise MaintError("種類は schtasks / cron / path / other")
    lines = load(state_path(root), STATE_SKELETON)
    if any(r["名前"] == esc(name) for r in rows(lines, "稼働中のもの")):
        raise MaintError(f"「{name}」は登録済み")
    append(lines, "稼働中のもの", RUN_HEAD, {"名前": name, "種類": kind, "対象": target, "ホスト": _host(),
                                          "登録": today(), "止め方": stop})
    log(lines, f"稼働を登録：{name}（{kind} {target}）")
    _write(root, lines)


def run_rm(root: Path, name: str) -> None:
    lines = load(state_path(root), STATE_SKELETON)
    remove(lines, "稼働中のもの", RUN_HEAD, esc(name))
    log(lines, f"稼働を外した：{name}")
    _write(root, lines)


def handoff(root: Path, text: str) -> None:
    lines = load(state_path(root), STATE_SKELETON)
    lines = set_section(lines, "引き継ぎ", f"{today()}：{text}" if text.strip() else "")
    _write(root, lines)


def _host() -> str:
    import platform
    return f"{platform.node()} ({platform.system()})"   # ASCII だけ（コンソールの文字コードで化けない）


# ---------------------------------------------------------------- live checks
def _run(cmd: list[str], root: Path, timeout: int = 15) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(cmd, cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None


def run_alive(root: Path, r: dict) -> str:
    """稼働中のものが今も在るか。別のホストで登録したものは確かめられない。"""
    if r.get("ホスト") and r["ホスト"] != _host():
        return "別のホスト（ここでは確かめられない）"
    kind, target = r.get("種類"), r.get("対象", "")
    if kind == "schtasks":
        p = _run(["schtasks", "/Query", "/TN", target], root)
        return "登録あり" if p and p.returncode == 0 else "見つからない"
    if kind == "cron":
        p = _run(["crontab", "-l"], root)
        return "登録あり" if p and target in (p.stdout or "") else "見つからない"
    if kind == "path":
        return "あり" if Path(target).exists() else "見つからない"
    return "確かめ方なし"


def git_summary(root: Path) -> list[str]:
    out = []
    p = _run(["git", "status", "--porcelain"], root)
    if p and p.returncode == 0:
        n = len([l for l in p.stdout.splitlines() if l.strip()])
        out.append(f"未コミット {n} 件")
    p = _run(["git", "rev-list", "--count", "@{u}..HEAD"], root)
    if p and p.returncode == 0:
        out.append(f"未 push {p.stdout.strip()} 件")
    if shutil.which("gh"):
        p = _run(["gh", "run", "list", "--limit", "1", "--json", "conclusion,status,headSha,displayTitle"], root, 20)
        try:
            runs = json.loads(p.stdout) if p and p.returncode == 0 else []
        except ValueError:
            runs = []
        if runs:
            r = runs[0]
            out.append(f"最新の CI {r.get('conclusion') or r.get('status')}（{r.get('headSha', '')[:7]}）")
    return out


# ---------------------------------------------------------------- isolation (メンテモードと本体の設定の分離)
def _load_enable(root: Path):
    spec = importlib.util.spec_from_file_location("enable_maintenance", root / "tools" / "dev" / "enable-maintenance.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


def _json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except ValueError as e:
        raise MaintError(f"{p} が壊れている: {e}")


def settings_problems(root: Path) -> list[str]:
    """本体の .claude/settings.json のうち、メンテモードでの扱いが決まっていないもの。
    permissions のリストは Claude Code が層をまたいで合算するので、メンテモードにも効く。承認済み（isolation.json）と違えば問題。"""
    iso = _json(root / "tools" / "dev" / "isolation.json")
    st = _json(root / ".claude" / "settings.json")
    probs = []
    for k in st:
        if k not in iso.get("settings_keys", {}):
            probs.append(f"本体 settings.json の「{k}」のメンテモードでの扱いが決まっていない")
    for k, v in (st.get("permissions") or {}).items():
        if k not in iso.get("permissions_keys", {}):
            probs.append(f"本体 settings.json の permissions.{k} のメンテモードでの扱いが決まっていない")
        elif isinstance(v, list):
            ack = iso.get("acknowledged", {}).get(k, [])
            added, gone = [x for x in v if x not in ack], [x for x in ack if x not in v]
            if added or gone:
                probs.append(f"本体の permissions.{k} が承認済みと違う（追加 {added}・削除 {gone}）。"
                             "メンテモードにも効くので影響を確かめ、tools/dev/isolation.json の acknowledged に写す")
    if probs:
        probs.append("→ 扱い方は tools/dev/DESIGN.md の「本体の設定との分離」")
    return probs


def overlay_problems(root: Path) -> list[str]:
    """メンテ層（settings.local.json・CLAUDE.local.md・maint スキル）が本体の今の姿に追いついているか。"""
    en = _load_enable(root)
    local = _json(root / ".claude" / "settings.local.json")
    probs = []
    if local.get("disableAllHooks") is not True:
        probs.append("disableAllHooks が true でない（本体のフックが動く）")
    off = local.get("skillOverrides", {})
    leak = [n for n in en.product_skill_names(root) if off.get(n) != "off"]
    if leak:
        probs.append(f"本体のスキル・コマンドがメンテモードに見えている: {', '.join(leak)}")
    on = [s for s in en.mcp_server_names(root) if s not in local.get("disabledMcpjsonServers", [])]
    if on:
        probs.append(f"本体の MCP サーバーが止まっていない: {', '.join(on)}")
    src, dst = root / "tools" / "dev" / "claude-local.md", root / "CLAUDE.local.md"
    if not dst.exists() or dst.read_bytes() != src.read_bytes():
        probs.append("CLAUDE.local.md が tools/dev/claude-local.md と違う")
    return probs


# ---------------------------------------------------------------- start
def start(root: Path) -> int:
    print("== メンテの状況（tools/dev/memory/maint-state.md）==")
    changes = _load_enable(root).sync(root)
    print("[メンテ層] " + ("写し直した：" + "／".join(changes) + "（スキルの見え方は新しいセッションから確実に変わる）"
                         if changes else "本体に追いついている"))
    probs = overlay_problems(root) + settings_problems(root)
    print("[分離] " + ("OK" if not probs else "問題あり"))
    for p in probs:
        print(f"  - {p}")
    lines = load(state_path(root), STATE_SKELETON)
    print("[引き継ぎ] " + section_text(lines, "引き継ぎ").replace("\n", " "))
    open_reqs = [r for r in rows(lines, "要求") if r.get("状態") in OPEN]
    print(f"[要求] 進行中 {len(open_reqs)} 件")
    for r in open_reqs:
        print(f"  - {r['ID']} {r['状態']}：{r['要求']}" + (f" ／ 次：{r['現在地・次の一手']}" if r.get("現在地・次の一手") else ""))
    ver = rows(lines, "未検証")
    print(f"[未検証] {len(ver)} 件" + ("：" + "、".join(f"{v['ID']} {v['確かめること']}" for v in ver[:5]) if ver else ""))
    runs = rows(lines, "稼働中のもの")
    print(f"[稼働中] {len(runs)} 件")
    for r in runs:
        print(f"  - {r['名前']}（{r['種類']} {r['対象']}）：{run_alive(root, r)}")
    g = git_summary(root)
    if g:
        print("[git] " + "・".join(g))
    return 1 if probs else 0


# ---------------------------------------------------------------- git hooks
def git_hook(root: Path, name: str) -> int:
    if name == "pre-commit":
        p = _run(["git", "diff", "--cached", "--name-only"], root)
        staged = [l for l in (p.stdout if p else "").splitlines() if l.strip()]
        if ".claude/settings.json" in staged or "tools/dev/isolation.json" in staged:
            probs = settings_problems(root)
            if probs:
                print("❌ 本体の設定の変更がメンテモードに効く。扱いを決めてからコミットする：", file=sys.stderr)
                for x in probs:
                    print(f"  - {x}", file=sys.stderr)
                return 1
        work = [f for f in staged if not f.startswith("tools/dev/memory/")]
        lines = load(state_path(root), STATE_SKELETON)
        active = [r for r in rows(lines, "要求") if r.get("状態") == "着手"]
        ledger = "tools/dev/memory/maint-state.md" in staged   # 完了の記録を作業と同じコミットに入れるとき
        if work and not active and not ledger and not os.environ.get("MAINT_ALLOW_UNTRACKED"):
            print("❌ 着手中のメンテ要求が無い。先に台帳へ記録する（会話が変わっても続きが分かるように）：\n"
                  "   python tools/dev/maint.py add \"要求の要約\" --state 着手   （既存なら set R-xxx 着手）\n"
                  "   台帳を使わずにコミットするときだけ MAINT_ALLOW_UNTRACKED=1", file=sys.stderr)
            return 1
        return 0
    if name in ("post-commit", "post-merge", "post-checkout"):
        changes = _load_enable(root).sync(root)
        if changes:
            print("[メンテ層] 写し直した：" + "／".join(changes))
        return 0
    return 0


# ---------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=ROOT)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("start")
    sub.add_parser("check")
    s = sub.add_parser("add"); s.add_argument("text"); s.add_argument("--state", default="受付"); s.add_argument("--next", default="")
    s = sub.add_parser("set"); s.add_argument("id"); s.add_argument("state"); s.add_argument("--next")
    s = sub.add_parser("note"); s.add_argument("id"); s.add_argument("text"); s.add_argument("--next")
    s = sub.add_parser("done"); s.add_argument("id"); s.add_argument("--result", required=True)
    s.add_argument("--unverified", action="append", default=[])
    s = sub.add_parser("verify"); s.add_argument("id"); s.add_argument("text", nargs="?", default="")
    s.add_argument("--ok", action="store_true"); s.add_argument("--note", default="")
    s = sub.add_parser("run"); s.add_argument("op", choices=["add", "rm"]); s.add_argument("name")
    s.add_argument("--kind", default="other"); s.add_argument("--target", default=""); s.add_argument("--stop", default="")
    s = sub.add_parser("handoff"); s.add_argument("text", nargs="?", default=""); s.add_argument("--clear", action="store_true")
    s = sub.add_parser("git-hook"); s.add_argument("name"); s.add_argument("rest", nargs="*")
    a = ap.parse_args(argv)
    root = a.root.resolve()
    if not is_maint(root):
        if a.cmd != "git-hook":
            print("メンテモードではない（本体フォルダで python tools/dev/enable-maintenance.py を実行すると有効になる）")
        return
    try:
        if a.cmd == "start":
            sys.exit(start(root))
        elif a.cmd == "check":
            probs = overlay_problems(root) + settings_problems(root)
            print("分離 OK" if not probs else "\n".join(f"- {p}" for p in probs))
            sys.exit(1 if probs else 0)
        elif a.cmd == "add":
            print(f"受付 {add(root, a.text, a.state, a.next)}（{a.state}）")
        elif a.cmd == "set":
            set_state(root, a.id, a.state, a.next)
            print(f"{a.id} → {a.state}")
        elif a.cmd == "note":
            note(root, a.id, a.text, a.next)
            print(f"{a.id} に経過を記録")
        elif a.cmd == "done":
            vids = done(root, a.id, a.result, a.unverified)
            print(f"{a.id} 完了" + (f"・未検証 {', '.join(vids)}" if vids else ""))
        elif a.cmd == "verify":
            if a.ok:
                verify_ok(root, a.id, a.note)
                print(f"{a.id} 確認済み（記録庫へ）")
            else:
                if not a.text:
                    raise MaintError("確かめることを書く")
                print(f"未検証 {verify_add(root, a.id, a.text)}")
        elif a.cmd == "run":
            run_add(root, a.name, a.kind, a.target, a.stop) if a.op == "add" else run_rm(root, a.name)
            print(f"稼働中のもの：{a.name} を{'登録' if a.op == 'add' else '外した'}")
        elif a.cmd == "handoff":
            handoff(root, "" if a.clear else a.text)
            print("引き継ぎを" + ("消した" if a.clear else "書いた"))
        elif a.cmd == "git-hook":
            sys.exit(git_hook(root, a.name))
    except MaintError as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
