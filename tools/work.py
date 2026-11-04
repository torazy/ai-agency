#!/usr/bin/env python3
"""
work.py ── チームの仕事の記録（提案・作業依頼・参照と版・実行履歴・引き継ぎ）と、関連情報の変化の検知。

役割は分ける（CLAUDE.md §4・.claude/commands/scout.md・dispatch.md・run-task.md）：
  提案役   状況から必要な仕事を見つけて `propose` する（そのまま実行はしない）
  割り振り役 提案を `decide`（採用・保留・却下・統合・要承認）し、作業依頼 `order` と参照 `ref` を書いて担当へ渡す
  作業役   独立したセッション（tools/run_task.py → /run-task）で作業依頼に沿って進め、報告を返す
  確認役   完了条件・根拠・使った版を確かめ `accept`（受け入れ・差し戻し・人へ確認）。古い条件の成果は受け入れない

記録（すべて memory/work/。git で共有。セッションが終わっても続きを別のセッションが担当できる）：
  proposals.md          提案の一覧（S-001…）。起因（指紋）で同じ変化からの重複提案を防ぐ
  tasks/{C-ID}.md       作業記録：作業依頼／参照（情報・場所・使い方・評価した版）／途中の変更／実行履歴／引き継ぎ
  results/{実行ID}.md   実行ごとの報告（実行ID は C-007-R1 の形）
  settings.md           定期実行の間隔・同時実行の上限・費用の上限・自動で着手するか
  .state/               実行中の状態とログ（端末ごと。gitignore）

参照の「場所」と版：
  memory/…md#見出し  その節の本文のハッシュ（節が無ければ「なし」）。#見出し を省くとファイル全体
  C-006              約束の状態（open / done …）
  それ以外（URL 等）  自動では確かめない（手動）。/update で memory に取り込まれた先を参照にするのがよい
参照の「使い方」：
  前提  変わったら結論を見直す（完了済みの仕事も見直し候補にする）
  待ち  変わったら再開する（返事・見積・資料の到着待ち）
  後工程 その約束が done になったら着手する（場所は C-ID）

  python tools/work.py order C-007 --purpose … --deliverable … --done-when … [--ops …] [--limit "2.5 USD・30分"] [--review qa]
  python tools/work.py ref add C-007 "memory/projects/x/status.md#見積" --use 待ち --label 見積
  python tools/work.py ref touch C-007 [場所]           # 今の版で評価済みにする（影響なしと判断したときも）
  python tools/work.py changes [--json] [--propose]     # 変化の一覧。--propose で未提案のものを提案にする
  python tools/work.py scan [--propose]                 # 台帳から機械的に見つかる仕事（フォロー日・期限が近いのに未着手）
  python tools/work.py propose --what … --why … --evidence … --effect … --who @pm [--key 指紋]
  python tools/work.py decide S-003 adopt|hold|reject|merge|ask [--task C-012] --reason …
  python tools/work.py note C-007 "部長から新しい条件：…"   # 実行中・次回の作業に渡す途中の変更
  python tools/work.py handoff C-007 "ここまで…次は…"
  python tools/work.py accept C-007-R2 accept|return|ask --reason …   # 確認役の判定（古い条件なら accept できない）
  python tools/work.py applied C-007-R2                  # 報告の「記憶に入れる候補」を記録し終えた
  python tools/work.py status [--json]                   # 割り振り役が最初に見る（実行中・今日の費用・上限・未判定・未確認）
  python tools/work.py tick                              # 定期実行の間隔が来ていれば 0、まだなら 1（schedule から呼ぶ）
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_mem():
    spec = importlib.util.spec_from_file_location("mem_for_work", Path(__file__).resolve().parent / "mem.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


mem = _load_mem()
MemError = mem.MemError
fail, ok, read, write, cells, esc, locked = mem.fail, mem.ok, mem.read, mem.write, mem.cells, mem.esc, mem.locked

CID_RE = re.compile(r"C-\d{3,}")
RUN_RE = re.compile(r"(C-\d{3,})-R(\d+)")
SID_RE = re.compile(r"S-\d{3,}")
USES = ("前提", "待ち", "後工程")
DECISIONS = {"adopt": "採用", "hold": "保留", "reject": "却下", "merge": "統合", "ask": "要承認"}
VERDICTS = {"accept": "受け入れ", "return": "差し戻し", "ask": "人へ確認"}
MAX_RETURNS = 2   # 同じタスクの差し戻しはここまで。超えたら人へ確認

PROPOSAL_COLS = ["ID", "日付", "起因", "何をするか", "なぜ今", "根拠", "期待する効果", "担当候補", "判定", "判定理由"]
REF_COLS = ["情報", "場所", "使い方", "評価した版", "評価日"]
RUN_COLS = ["実行ID", "開始", "終了", "結果", "使った版", "費用", "判定", "反映"]
ORDER_KEYS = ["目的", "成果物", "完了条件", "実行してよい操作", "期限", "上限", "レビュー", "報告先", "行き詰まったら"]

SETTINGS_DEFAULT = {
    "実行間隔（分）": "60",
    "同時実行の上限": "2",
    "1日の費用上限（USD）": "10",
    "1回の上限（USD・レビューなし）": "1",
    "1回の上限（USD・レビューあり）": "2.5",
    "自動で着手": "しない",
}
SETTINGS_SKELETON = """# チームの自動運転の設定（work/settings.md）

> 定期実行（tools/schedule/dispatch.*）と割り振り役（/dispatch）が従う。値だけを書き換える（秘書に頼んでもよい）。
> 「自動で着手」：しない＝提案と割り振り案まで（着手は人がボードか会話で）／委任範囲＝`memory/delegation.md` の
> 「チームの自動運転で任せてよい仕事」に当たるものだけ割り振り役が着手する。

| 項目 | 値 | 説明 |
|---|---|---|
| 実行間隔（分） | 60 | 定期の見直しの間隔。スケジューラは15分ごとに呼び、ここで間引く |
| 同時実行の上限 | 2 | 同時に動かす作業セッションの数 |
| 1日の費用上限（USD） | 10 | その日に始めた実行の費用の合計がこれを超えるなら新しく起動しない |
| 1回の上限（USD・レビューなし） | 1 | 作業セッション1回の上限 |
| 1回の上限（USD・レビューあり） | 2.5 | 作成・レビュー・直しの3人分 |
| 自動で着手 | しない | しない／委任範囲 |
"""
PROPOSALS_SKELETON = """# 提案（work/proposals.md）

> 提案役（/scout・定期の見直し）が見つけた仕事。**そのまま全部は実行しない**。割り振り役（/dispatch）が判定する：
> 採用（→ 約束台帳の C-ID）／統合（既存タスクへ）／保留／却下／要承認（人の判断が要る。ボードで採用・却下）。
> 起因：同じ変化から二重に提案しないための指紋（変化なら `C-007|場所|新しい版`、観察なら `観察`）。

| ID | 日付 | 起因 | 何をするか | なぜ今 | 根拠 | 期待する効果 | 担当候補 | 判定 | 判定理由 |
|---|---|---|---|---|---|---|---|---|---|
| （記録なし） | | | | | | | | | |
"""


# ---------------------------------------------------------------- paths
def work_dir(root: Path) -> Path:
    return root / "memory" / "work"


def state_dir(root: Path) -> Path:
    return work_dir(root) / ".state"


def results_dir(root: Path) -> Path:
    return work_dir(root) / "results"


def task_path(root: Path, cid: str) -> Path:
    return work_dir(root) / "tasks" / f"{cid}.md"


def proposals_path(root: Path) -> Path:
    p = work_dir(root) / "proposals.md"
    if not p.exists():
        mem.write_text_atomic(p, PROPOSALS_SKELETON)
    return p


def settings_path(root: Path) -> Path:
    p = work_dir(root) / "settings.md"
    if not p.exists():
        mem.write_text_atomic(p, SETTINGS_SKELETON)
    return p


def ensure_work(root: Path) -> list[str]:
    """memory/work/ の雛形を作る（migrate から呼ぶ）。作ったものの名前を返す。"""
    made = []
    for name, fn in (("proposals.md", proposals_path), ("settings.md", settings_path)):
        if not (work_dir(root) / name).exists():
            fn(root)
            made.append(f"work/{name}")
    for d in ("tasks", "results"):
        p = work_dir(root) / d
        if not p.exists():
            p.mkdir(parents=True, exist_ok=True)
            (p / ".gitkeep").write_text("", encoding="utf-8")
    return made


# ---------------------------------------------------------------- tables
def _table(lines: list[str], heading: str) -> tuple[int, int]:
    sec = mem.find_section(lines, heading)
    if sec < 0:
        return -1, -1
    return mem.find_table(lines, sec)


def table_rows(lines: list[str], heading: str) -> list[dict]:
    h, e = _table(lines, heading)
    if h < 0:
        return []
    hdr = cells(lines[h])
    return [dict(zip(hdr, cells(ln))) for ln in lines[h + 2:e] if not mem.PLACEHOLDER_RE.match(ln)]


def _set_row(lines: list[str], heading: str, key_col: str, key: str, updates: dict) -> bool:
    h, e = _table(lines, heading)
    if h < 0:
        return False
    hdr = cells(lines[h])
    for i in range(h + 2, e):
        c = cells(lines[i])
        if dict(zip(hdr, c)).get(key_col) == key:
            for k, v in updates.items():
                if k in hdr:
                    while len(c) < len(hdr):
                        c.append("")
                    c[hdr.index(k)] = esc(v)
            lines[i] = "| " + " | ".join(c) + " |"
            return True
    return False


def _append(lines: list[str], heading: str, row: list[str]) -> list[str]:
    h, e = _table(lines, heading)
    if h < 0:
        fail(f"表「{heading}」が無い")
    return mem.append_row(lines, h, e, row)


# ---------------------------------------------------------------- settings
def settings(root: Path) -> dict:
    vals = dict(SETTINGS_DEFAULT)
    for r in table_rows(read(settings_path(root)), "チームの自動運転"):
        if r.get("項目") in vals and r.get("値"):
            vals[r["項目"]] = r["値"]
    return vals


def _num(s: str, default: float) -> float:
    m = re.search(r"\d+(\.\d+)?", s or "")
    return float(m.group(0)) if m else default


def limits(root: Path) -> dict:
    s = settings(root)
    return {"interval_min": _num(s["実行間隔（分）"], 60), "max_running": int(_num(s["同時実行の上限"], 2)),
            "daily_usd": _num(s["1日の費用上限（USD）"], 10), "run_usd": _num(s["1回の上限（USD・レビューなし）"], 1),
            "run_usd_review": _num(s["1回の上限（USD・レビューあり）"], 2.5),
            "auto_start": s["自動で着手"].strip() == "委任範囲"}


# ---------------------------------------------------------------- versions
def _section_text(text: str, heading: str) -> str | None:
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        m = re.match(r"^(#{1,6})\s+(.*)$", ln)
        if m and heading in m.group(2):
            level = len(m.group(1))
            j = i + 1
            while j < len(lines):
                mm = re.match(r"^(#{1,6})\s", lines[j])
                if mm and len(mm.group(1)) <= level:
                    break
                j += 1
            return "\n".join(lines[i:j])
    return None


def _norm(text: str) -> str:
    # 「最終更新」「更新日」の行は版に入れない（書き直しただけで変化扱いにしない）
    return "\n".join(ln.rstrip() for ln in text.splitlines() if not re.match(r"^\s*[>-]?\s*\**(最終更新|更新日)", ln)).strip()


def version_of(root: Path, place: str) -> str | None:
    """参照の今の版。自動で確かめられないもの（URL 等）は None。"""
    place = place.strip().strip("`")
    if CID_RE.fullmatch(place):
        row = next((r for r in mem.commitment_rows(root) if r.get("ID") == place), None)
        return row.get("状態", "") if row else "なし"
    if place.startswith("memory/"):
        path, _, heading = place.partition("#")
        p = (root / path).resolve()
        try:
            p.relative_to((root / "memory").resolve())
        except ValueError:
            return None
        if not p.is_file():
            return "なし"
        text = p.read_text(encoding="utf-8")
        if heading:
            text = _section_text(text, heading.strip())
            if text is None:
                return "なし"
        return hashlib.sha1(_norm(text).encode("utf-8")).hexdigest()[:8]
    return None


# ---------------------------------------------------------------- task records
def commitment(root: Path, cid: str) -> dict:
    row = next((r for r in mem.commitment_rows(root) if r.get("ID") == cid), None)
    if not row:
        fail(f"{cid} が約束台帳に無い")
    return row


def _content(row: dict) -> str:
    return row.get("タスク") or row.get("内容") or ""


def _skeleton(row: dict) -> str:
    cid = row["ID"]
    who = row.get("相手") or row.get("依頼者") or "自分"
    return f"""# {cid} {_content(row)}

- **担当**: {who}
- **状態**: 約束台帳の {cid} を正とする
- **更新**: {mem.today()}

## 作業依頼
| 項目 | 内容 |
|---|---|
| 目的 | {esc(_content(row))} |
| 成果物 | 報告（結論・根拠と出所・記憶に入れる候補・確認してほしいこと） |
| 完了条件 | 依頼に答え、主張に出所があり、人の判断が要る点が挙がっている |
| 実行してよい操作 | 読み取り・Web 調査・報告の作成（記憶・約束の状態・外部サービスは変えない） |
| 期限 | {row.get('期限', 'なし')} |
| 上限 | 既定（work/settings.md） |
| レビュー | auto（秘書が難易度で判断） |
| 報告先 | memory/work/results/ ・ @chief |
| 行き詰まったら | 不足している情報と、誰に何を聞けばよいかを報告の「確認してほしいこと」に書いて終える |

## 参照（使う情報と版）
| 情報 | 場所 | 使い方 | 評価した版 | 評価日 |
|---|---|---|---|---|
| （記録なし） | | | | |

使い方：前提＝変わったら結論を見直す（完了済みでも）／待ち＝変わったら再開／後工程＝その約束が done になったら着手

## 途中の変更
- （記録なし）

## 実行履歴
| 実行ID | 開始 | 終了 | 結果 | 使った版 | 費用 | 判定 | 反映 |
|---|---|---|---|---|---|---|---|
| （記録なし） | | | | | | | |

## 引き継ぎ
（まだ無い）
"""


def ensure_task(root: Path, cid: str) -> Path:
    if not CID_RE.fullmatch(cid or ""):
        fail(f"ID の形が違う：{cid}")
    p = task_path(root, cid)
    if not p.exists():
        mem.write_text_atomic(p, _skeleton(commitment(root, cid)))
    return p


def _touch(lines: list[str]) -> list[str]:
    return [f"- **更新**: {mem.today()}" if ln.startswith("- **更新**:") else ln for ln in lines]


def read_task(root: Path, cid: str) -> dict:
    p = task_path(root, cid)
    if not p.exists():
        return {}
    lines = read(p)
    order = {r.get("項目"): r.get("内容", "") for r in table_rows(lines, "作業依頼")}
    notes, handoff, sec = [], [], None
    for ln in lines:
        if ln.startswith("## "):
            sec = ln[3:].strip()
            continue
        if sec == "途中の変更" and ln.startswith("- ") and "（記録なし）" not in ln:
            notes.append(ln[2:])
        elif sec == "引き継ぎ" and ln.strip() and ln.strip() != "（まだ無い）":
            handoff.append(ln)
    return {"id": cid, "order": order, "refs": table_rows(lines, "参照"), "runs": table_rows(lines, "実行履歴"),
            "notes": notes, "handoff": "\n".join(handoff)}


@locked
def order_set(root: Path, cid: str, fields: dict) -> None:
    p = ensure_task(root, cid)
    lines = read(p)
    for k, v in fields.items():
        if v is None or k not in ORDER_KEYS:
            continue
        if not _set_row(lines, "作業依頼", "項目", k, {"内容": v}):
            lines = _append(lines, "作業依頼", [k, v])
    write(p, _touch(lines))
    ok(f"{cid} の作業依頼を更新（{', '.join(k for k, v in fields.items() if v is not None)}）")


@locked
def ref_add(root: Path, cid: str, place: str, use: str, label: str) -> None:
    if use not in USES:
        fail(f"使い方は {' / '.join(USES)}")
    place = place.strip().strip("`")
    if use == "後工程" and not CID_RE.fullmatch(place):
        fail("後工程の場所は約束の ID（C-006 など）")
    p = ensure_task(root, cid)
    lines = read(p)
    if any(r.get("場所") == place for r in table_rows(lines, "参照")):
        fail(f"{cid} はもう {place} を参照している（版を更新するなら ref touch）")
    v = version_of(root, place)
    lines = _append(lines, "参照", [label or place, place, use, v if v is not None else "手動", mem.today()])
    write(p, _touch(lines))
    ok(f"{cid} の参照に {place}（{use}・版 {v or '手動'}）")


@locked
def ref_touch(root: Path, cid: str, place: str | None = None) -> list[str]:
    p = task_path(root, cid)
    if not p.exists():
        fail(f"{cid} の作業記録が無い")
    lines = read(p)
    done = []
    for r in table_rows(lines, "参照"):
        if place and r.get("場所") != place:
            continue
        v = version_of(root, r.get("場所", ""))
        if v is not None and v != r.get("評価した版"):
            _set_row(lines, "参照", "場所", r["場所"], {"評価した版": v, "評価日": mem.today()})
            done.append(r["場所"])
    if place and not done and not any(r.get("場所") == place for r in table_rows(lines, "参照")):
        fail(f"{cid} は {place} を参照していない")
    write(p, _touch(lines))
    ok(f"{cid} の参照を今の版で評価済みに（{', '.join(done) or '変化なし'}）")
    return done


@locked
def note(root: Path, cid: str, text: str) -> None:
    p = ensure_task(root, cid)
    lines = read(p)
    i = mem.find_section(lines, "途中の変更")
    j = i + 1
    while j < len(lines) and not lines[j].startswith("## "):
        j += 1
    body = [ln for ln in lines[i + 1:j] if ln.strip() != "- （記録なし）"]
    lines = lines[:i + 1] + body + lines[j:]
    j = i + 1 + len(body)
    while j > i + 1 and not lines[j - 1].strip():
        j -= 1
    lines.insert(j, f"- {mem.now_hm()} {esc(text)}")
    write(p, _touch(lines))
    ok(f"{cid} に途中の変更を記録（実行中なら報告を書く前に、次の実行なら最初に読まれる）")


@locked
def handoff(root: Path, cid: str, text: str) -> None:
    p = ensure_task(root, cid)
    lines = read(p)
    i = mem.find_section(lines, "引き継ぎ")
    j = i + 1
    while j < len(lines) and not lines[j].startswith("## "):
        j += 1
    lines = lines[:i + 1] + [f"（{mem.now_hm()}）", text.strip()] + lines[j:]
    write(p, _touch(lines))
    ok(f"{cid} の引き継ぎを更新")


# ---------------------------------------------------------------- runs
def snapshot(root: Path, cid: str) -> str:
    """実行に使った版（「場所=版」の並び）。"""
    parts = []
    for r in read_task(root, cid).get("refs", []):
        v = version_of(root, r.get("場所", ""))
        parts.append(f"{r.get('場所')}={v if v is not None else '手動'}")
    return "; ".join(parts) or "参照なし"


def _parse_snapshot(s: str) -> dict:
    out = {}
    for part in (s or "").split(";"):
        if "=" in part:
            k, _, v = part.strip().rpartition("=")
            out[k.strip()] = v.strip()
    return out


@locked
def run_start(root: Path, cid: str) -> str:
    p = ensure_task(root, cid)
    lines = read(p)
    n = max([int(m.group(2)) for r in table_rows(lines, "実行履歴") if (m := RUN_RE.fullmatch(r.get("実行ID", "")))] or [0]) + 1
    rid = f"{cid}-R{n}"
    lines = _append(lines, "実行履歴", [rid, mem.now_hm(), "", "実行中", snapshot(root, cid), "", "", ""])
    write(p, _touch(lines))
    return rid


@locked
def run_end(root: Path, rid: str, result: str, cost: float | None) -> None:
    m = RUN_RE.fullmatch(rid)
    p = task_path(root, m.group(1))
    lines = read(p)
    _set_row(lines, "実行履歴", "実行ID", rid, {"終了": mem.now_hm(), "結果": result,
                                              "費用": f"{cost:.2f}" if isinstance(cost, (int, float)) else ""})
    write(p, _touch(lines))


def run_row(root: Path, rid: str) -> dict:
    m = RUN_RE.fullmatch(rid or "")
    if not m:
        fail(f"実行ID の形が違う：{rid}（C-007-R1 の形）")
    row = next((r for r in read_task(root, m.group(1)).get("runs", []) if r.get("実行ID") == rid), None)
    if not row:
        fail(f"{rid} が実行履歴に無い")
    return row


def stale_refs(root: Path, rid: str) -> list[str]:
    """その実行が使った版のうち、前提が今は変わっているもの（古い条件で作られた成果）。"""
    row = run_row(root, rid)
    used = _parse_snapshot(row.get("使った版", ""))
    cid = RUN_RE.fullmatch(rid).group(1)
    out = []
    for r in read_task(root, cid).get("refs", []):
        if r.get("使い方") != "前提":
            continue
        place = r.get("場所", "")
        now = version_of(root, place)
        if now is not None and place in used and used[place] != now:
            out.append(place)
    return out


@locked
def accept(root: Path, rid: str, verdict: str, reason: str = "") -> str:
    if verdict not in VERDICTS:
        fail(f"判定は {' / '.join(VERDICTS)}")
    row = run_row(root, rid)
    cid = RUN_RE.fullmatch(rid).group(1)
    label = VERDICTS[verdict]
    if verdict == "accept":
        later = [r for r in read_task(root, cid).get("runs", []) if r.get("実行ID") != rid
                 and RUN_RE.fullmatch(r.get("実行ID", "")) and int(RUN_RE.fullmatch(r["実行ID"]).group(2)) > int(RUN_RE.fullmatch(rid).group(2))]
        if later:
            fail(f"{rid} より新しい実行（{later[-1]['実行ID']}）がある。最新の結果を確認する")
        stale = stale_refs(root, rid)
        if stale:
            label = "古い条件"
            reason = f"前提が変わった：{', '.join(stale)}。" + (reason or "")
    if verdict == "return":
        returns = sum(1 for r in read_task(root, cid).get("runs", []) if r.get("判定", "").startswith("差し戻し"))
        if returns >= MAX_RETURNS:
            label = "人へ確認"
            reason = f"差し戻しが {returns} 回に達した。" + (reason or "")
    p = task_path(root, cid)
    lines = read(p)
    _set_row(lines, "実行履歴", "実行ID", rid, {"判定": label + (f"（{reason}）" if reason else "")})
    write(p, _touch(lines))
    ok(f"{rid} を「{label}」に" + (f"（{reason}）" if reason else ""))
    return label


@locked
def mark_applied(root: Path, rid: str) -> None:
    run_row(root, rid)
    cid = RUN_RE.fullmatch(rid).group(1)
    p = task_path(root, cid)
    lines = read(p)
    _set_row(lines, "実行履歴", "実行ID", rid, {"反映": f"済（{mem.today()}）"})
    write(p, _touch(lines))
    ok(f"{rid} の報告を記憶に反映済みにした")


def spent_today(root: Path) -> float:
    total = 0.0
    d = mem.today()
    for p in sorted((work_dir(root) / "tasks").glob("C-*.md")) if (work_dir(root) / "tasks").exists() else []:
        for r in table_rows(read(p), "実行履歴"):
            if r.get("開始", "").startswith(d):
                try:
                    total += float(r.get("費用") or 0)
                except ValueError:
                    pass
    return round(total, 2)


# ---------------------------------------------------------------- changes
def changes(root: Path) -> list[dict]:
    """参照している情報の変化。タスク本文が変わらなくても、依存している情報・条件が変われば拾う。"""
    states = {r.get("ID"): r.get("状態", "") for r in mem.commitment_rows(root)}
    out = []
    for p in sorted((work_dir(root) / "tasks").glob("C-*.md")) if (work_dir(root) / "tasks").exists() else []:
        cid = p.stem
        st = states.get(cid)
        if st is None or st == "dropped":
            continue
        t = read_task(root, cid)
        running = any(r.get("結果") == "実行中" for r in t["runs"])
        for r in t["refs"]:
            place, use, old = r.get("場所", ""), r.get("使い方", ""), r.get("評価した版", "")
            new = version_of(root, place)
            if new is None or new == old:
                continue
            closed = st in mem.CLOSED
            if use == "待ち" and not closed:
                kind = "再開"
            elif use == "後工程" and not closed and new == "done":
                kind = "着手"
            elif use == "前提" and not closed:
                kind = "実行中に変化" if running else "見直し"
            elif use == "前提" and closed:
                kind = "完了済みの見直し"
            else:
                continue
            out.append({"task": cid, "label": r.get("情報", ""), "place": place, "use": use, "old": old, "new": new,
                        "kind": kind, "running": running, "task_state": st, "key": f"{cid}|{place}|{new}",
                        "content": t["order"].get("目的", "")})
    return out


CHANGE_TEXT = {
    "再開": ("{task} を再開する（待っていた「{label}」が届いた・変わった）", "待ちの条件が満たされた可能性がある"),
    "着手": ("{task} に着手する（前工程 {place} が完了）", "着手条件を満たした"),
    "見直し": ("{task} の進め方・結論への影響を確かめる（「{label}」が変わった）", "前提にしている情報が変わった"),
    "実行中に変化": ("実行中の {task} の扱いを決める（継続／途中の変更を渡す／止めてやり直す／後で追加）", "実行中に前提が変わった"),
    "完了済みの見直し": ("完了済みの {task} を見直すか判定する（根拠の「{label}」が変わった）", "判断の根拠にした情報が変わった"),
}


# ---------------------------------------------------------------- proposals
def proposal_rows(root: Path) -> list[dict]:
    return table_rows(read(proposals_path(root)), "提案")


@locked
def propose(root: Path, what: str, why: str, evidence: str, effect: str, who: str, key: str = "観察") -> str | None:
    for name, v in (("何をするか", what), ("なぜ今", why), ("根拠", evidence), ("期待する効果", effect), ("担当候補", who)):
        if not (v or "").strip():
            fail(f"提案には「{name}」が要る")
    key = (key or "観察").strip()
    p = proposals_path(root)
    lines = read(p)
    rows = table_rows(lines, "提案")
    if key != "観察" and any(r.get("起因") == esc(key) for r in rows):
        return None   # 同じ変化からは二度提案しない（表のセルでは「|」が「／」になるので、書いた形で比べる）
    if any(r.get("何をするか") == esc(what) and not r.get("判定") for r in rows):
        return None
    sid = mem.next_id(lines, "S", root)
    lines = _append(lines, "提案", [sid, mem.today(), key, what, why, evidence, effect, who, "", ""])
    write(p, lines)
    ok(f"提案 {sid}「{what}」（担当候補 {who}）")
    return sid


@locked
def decide(root: Path, sid: str, decision: str, task: str = "", reason: str = "") -> None:
    if decision not in DECISIONS:
        fail(f"判定は {' / '.join(DECISIONS)}")
    if not reason.strip():
        fail("判定理由が要る（必要性・重複・優先度・依存・委任範囲・作業量のどれで決めたか）")
    if decision in ("adopt", "merge"):
        if not CID_RE.fullmatch(task or ""):
            fail("採用・統合は --task C-xxx（採用なら約束台帳に足した ID、統合なら統合先）")
        commitment(root, task)
    p = proposals_path(root)
    lines = read(p)
    label = DECISIONS[decision] + (f"→{task}" if task else "")
    if not _set_row(lines, "提案", "ID", sid, {"判定": label, "判定理由": reason}):
        fail(f"{sid} が無い")
    write(p, lines)
    ok(f"{sid} を「{label}」に（{reason}）")


def scan(root: Path) -> list[dict]:
    """台帳から機械的に見つかる仕事。観察による提案（会議の準備・不足作業・繰り返す問題）は /scout が足す。"""
    today_d = date.today()
    out = []
    for r in mem.commitment_rows(root):
        if r.get("状態") in mem.CLOSED:
            continue
        cid, content = r.get("ID", ""), _content(r)
        follow = r.get("フォロー日", "")
        who = r.get("相手") or r.get("依頼者") or ""
        if r["kind"] == "given" and not who.startswith("@") and re.fullmatch(r"\d{4}-\d{2}-\d{2}", follow or ""):
            if date.fromisoformat(follow) <= today_d and r.get("状態") != "done":
                out.append({"key": f"フォロー|{cid}|{follow}", "what": f"{who} に {cid}「{content}」の進み具合を確認する（催促の下書き）",
                            "why": f"フォロー日 {follow} が来た", "evidence": f"約束台帳 {cid}", "effect": "返答待ちの停滞を防ぐ",
                            "who": "@chief", "task": cid})
        due = r.get("期限", "")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", due or "") and r.get("状態") == "open":
            days = (date.fromisoformat(due) - today_d).days
            if 0 <= days <= 3 and not task_path(root, cid).exists():
                out.append({"key": f"期限|{cid}|{due}", "what": f"{cid}「{content}」の準備に着手する（期限まで {days} 日・未着手）",
                            "why": f"期限 {due} が近いのに未着手", "evidence": f"約束台帳 {cid}（状態 open）",
                            "effect": "期限切れを防ぐ", "who": who if who.startswith("@") else "@chief", "task": cid})
    return out


def propose_from_changes(root: Path) -> list[str]:
    made = []
    for c in changes(root):
        what, why = CHANGE_TEXT[c["kind"]]
        sid = propose(root, what.format(**c), why, f"{c['task']} の参照 {c['place']}：版 {c['old']} → {c['new']}",
                      "古い前提のまま進めない・待ちを解消する", "@chief（割り振り）", c["key"])
        if sid:
            made.append(sid)
    return made


def propose_from_scan(root: Path) -> list[str]:
    made = []
    for s in scan(root):
        sid = propose(root, s["what"], s["why"], s["evidence"], s["effect"], s["who"], s["key"])
        if sid:
            made.append(sid)
    return made


# ---------------------------------------------------------------- status / tick
def running_runs(root: Path) -> list[dict]:
    out = []
    for p in sorted((work_dir(root) / "tasks").glob("C-*.md")) if (work_dir(root) / "tasks").exists() else []:
        for r in table_rows(read(p), "実行履歴"):
            if r.get("結果") == "実行中":
                out.append({"task": p.stem, **r})
    return out


def unjudged_runs(root: Path) -> list[dict]:
    out = []
    states = {r.get("ID"): r.get("状態", "") for r in mem.commitment_rows(root)}
    for p in sorted((work_dir(root) / "tasks").glob("C-*.md")) if (work_dir(root) / "tasks").exists() else []:
        runs = table_rows(read(p), "実行履歴")
        if runs and runs[-1].get("結果") not in ("実行中", "") and not runs[-1].get("判定") and states.get(p.stem) not in mem.CLOSED:
            out.append({"task": p.stem, **runs[-1]})
    return out


def status(root: Path) -> dict:
    lim = limits(root)
    props = proposal_rows(root)
    return {"limits": lim, "spent_today": spent_today(root), "running": running_runs(root),
            "unjudged_runs": unjudged_runs(root), "changes": changes(root),
            "proposals_open": [r for r in props if not r.get("判定") or r.get("判定", "").startswith("要承認")],
            "can_start": len(running_runs(root)) < lim["max_running"] and spent_today(root) < lim["daily_usd"]}


def tick(root: Path) -> bool:
    """定期実行の間隔が来ているか。来ていれば最終実行時刻を更新して True。"""
    p = state_dir(root) / "last-dispatch.json"
    lim = limits(root)
    try:
        last = json.loads(p.read_text(encoding="utf-8")).get("at", 0)
    except (OSError, ValueError):
        last = 0
    if time.time() - last < lim["interval_min"] * 60 - 30:
        return False
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"at": time.time(), "when": mem.now_hm()}), encoding="utf-8")
    return True


# ---------------------------------------------------------------- CLI
UNATTENDED_OK = {"order", "ref", "changes", "scan", "propose", "decide", "note", "handoff", "accept", "status", "tick", "show"}


def main(argv: list[str] | None = None) -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="チームの仕事の記録と変化の検知")
    ap.add_argument("--root", type=Path, default=ROOT)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("order")
    s.add_argument("id")
    for flag, key in (("purpose", "目的"), ("deliverable", "成果物"), ("done-when", "完了条件"), ("ops", "実行してよい操作"),
                      ("due", "期限"), ("limit", "上限"), ("review", "レビュー"), ("report-to", "報告先"), ("escalate", "行き詰まったら")):
        s.add_argument(f"--{flag}", dest=key)
    s = sub.add_parser("ref")
    rs = s.add_subparsers(dest="sub", required=True)
    x = rs.add_parser("add"); x.add_argument("id"); x.add_argument("place"); x.add_argument("--use", required=True); x.add_argument("--label", default="")
    x = rs.add_parser("touch"); x.add_argument("id"); x.add_argument("place", nargs="?")
    s = sub.add_parser("changes"); s.add_argument("--json", action="store_true"); s.add_argument("--propose", action="store_true")
    s = sub.add_parser("scan"); s.add_argument("--json", action="store_true"); s.add_argument("--propose", action="store_true")
    s = sub.add_parser("propose")
    for f in ("what", "why", "evidence", "effect", "who"):
        s.add_argument(f"--{f}", required=True)
    s.add_argument("--key", default="観察")
    s = sub.add_parser("decide"); s.add_argument("id"); s.add_argument("decision"); s.add_argument("--task", default=""); s.add_argument("--reason", default="")
    s = sub.add_parser("note"); s.add_argument("id"); s.add_argument("text")
    s = sub.add_parser("handoff"); s.add_argument("id"); s.add_argument("text")
    s = sub.add_parser("accept"); s.add_argument("run"); s.add_argument("verdict"); s.add_argument("--reason", default="")
    s = sub.add_parser("applied"); s.add_argument("run")
    s = sub.add_parser("status"); s.add_argument("--json", action="store_true")
    s = sub.add_parser("show"); s.add_argument("id")
    sub.add_parser("tick")
    a = ap.parse_args(argv)
    root = a.root.resolve()
    if getattr(a, "json", False):   # JSON だけを出す（記録のメッセージを混ぜない）
        globals()["ok"] = lambda m: mem.LAST_OK.append(m)
    if os.environ.get("AI_AGENCY_UNATTENDED") and a.cmd not in UNATTENDED_OK:
        print(f"❌ 無人実行では `{a.cmd}` はしない", file=sys.stderr)
        sys.exit(1)
    try:
        if a.cmd == "order":
            order_set(root, a.id, {k: getattr(a, k) for k in ORDER_KEYS})
        elif a.cmd == "ref":
            ref_add(root, a.id, a.place, a.use, a.label) if a.sub == "add" else ref_touch(root, a.id, a.place)
        elif a.cmd in ("changes", "scan"):
            items = changes(root) if a.cmd == "changes" else scan(root)
            made = (propose_from_changes(root) if a.cmd == "changes" else propose_from_scan(root)) if a.propose else []
            if a.json:
                print(json.dumps({"items": items, "proposed": made}, ensure_ascii=False))
            else:
                for c in items:
                    print(f"{c.get('task', '')} {c.get('kind', '')} {c.get('place', c.get('what', ''))} "
                          f"{c.get('old', '')}→{c.get('new', '')}".strip() if a.cmd == "changes" else f"- {c['what']}（{c['why']}）")
                print(f"計 {len(items)} 件" + (f"・新しい提案 {', '.join(made)}" if made else ""))
        elif a.cmd == "propose":
            sid = propose(root, a.what, a.why, a.evidence, a.effect, a.who, a.key)
            if not sid:
                print("（同じ起因・同じ内容の提案が既にあるので足さない）")
        elif a.cmd == "decide":
            decide(root, a.id, a.decision, a.task, a.reason)
        elif a.cmd == "note":
            note(root, a.id, a.text)
        elif a.cmd == "handoff":
            handoff(root, a.id, a.text)
        elif a.cmd == "accept":
            accept(root, a.run, a.verdict, a.reason)
        elif a.cmd == "applied":
            mark_applied(root, a.run)
        elif a.cmd == "status":
            st = status(root)
            if a.json:
                print(json.dumps(st, ensure_ascii=False))
            else:
                lim = st["limits"]
                print(f"実行中 {len(st['running'])}/{lim['max_running']}・今日の費用 {st['spent_today']}/{lim['daily_usd']:g} USD・"
                      f"自動で着手 {'委任範囲' if lim['auto_start'] else 'しない'}")
                print(f"未判定の提案 {len(st['proposals_open'])}・未確認の実行 {len(st['unjudged_runs'])}・変化 {len(st['changes'])}")
        elif a.cmd == "show":
            print(json.dumps(read_task(root, a.id), ensure_ascii=False, indent=1))
        elif a.cmd == "tick":
            sys.exit(0 if tick(root) else 1)
    except MemError as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
