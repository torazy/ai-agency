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
  python tools/work.py note C-007 "部長：報告のままで可" --info   # 成果物を変えない記録（古い条件の判定に数えない）
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
import math
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
USES = ("前提", "待ち", "後工程", "不足")
DECISIONS = {"adopt": "採用", "hold": "保留", "reject": "却下", "merge": "統合", "ask": "要承認"}
VERDICTS = {"accept": "受け入れ", "return": "差し戻し", "ask": "人へ確認", "wait": "条件待ち"}
NEED_MET = ("done", "dropped")   # 不足の約束がこの状態なら満たした（dropped＝無しで進めると人が決めた）
MAX_OPEN_NEEDS = 3   # 1つの仕事に開いている不足の上限（超えたら人へ確認。集め過ぎ・費用の暴走を防ぐ）
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
    "観察の間隔（時間）": "8",
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
| 1回の上限（USD・レビューなし） | 1 | 作業セッション1回の上限（1 以上。起動だけで 0.3〜0.55 使う） |
| 1回の上限（USD・レビューあり） | 2.5 | 作成・レビュー・直しの3人分 |
| 自動で着手 | しない | しない／委任範囲 |
| 観察の間隔（時間） | 8 | 変化も確認待ちも無い回は Claude を起動しない（費用0）。観察（/scout）はこの間隔でだけ行い、/setup 前は行わない |
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


RUN_MIN_USD = 1.0   # 1回の上限の下限。作業セッションは起動だけで 0.30〜0.54 USD 使った（実測）。0.5 では起動だけで打ち切られる
RUN_MAX_USD = 20.0  # 1回の上限の上限（run_task.check_budget と同じ）


def clamp_run_budget(v: float) -> float:
    """設定・環境変数の1回の上限を、起動の判定（check_budget の範囲）に揃える。範囲外のままだと起動は毎回断られるのに、
    起動待ちの見込み（launch_waiting・pending）は起動できるとみなし、定期の見直しが空振りし続ける（R-024 の独立レビュー）。"""
    return min(max(v, RUN_MIN_USD), RUN_MAX_USD)


def env_run_budget() -> float | None:
    """環境変数 MAX_BUDGET_USD（朝のブリーフと共有）。数でない・nan・無限は無いものとして設定に任せる。"""
    try:
        v = float(os.environ.get("MAX_BUDGET_USD") or "")
    except ValueError:
        return None
    return clamp_run_budget(v) if math.isfinite(v) else None


def limits(root: Path) -> dict:
    s = settings(root)
    # 1回の上限が下限より小さい設定は下限に揃える。そのままだと起動が毎回断られ、定期の見直しが起動できない仕事のために
    # 毎回 Claude を起こしてしまう（launch_waiting の見込みと run_task の判定が食い違う）
    return {"interval_min": _num(s["実行間隔（分）"], 60), "max_running": int(_num(s["同時実行の上限"], 2)),
            "daily_usd": _num(s["1日の費用上限（USD）"], 10),
            "run_usd": clamp_run_budget(_num(s["1回の上限（USD・レビューなし）"], 1)),
            "run_usd_review": clamp_run_budget(_num(s["1回の上限（USD・レビューあり）"], 2.5)),
            "auto_start": s["自動で着手"].strip() == "委任範囲", "observe_h": _num(s["観察の間隔（時間）"], 8)}


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


def _human_kind(row: dict) -> bool:
    """人・相手がそろえる約束（自分のタスク・人に出した指示）。エージェントに任せた @handle は除く。"""
    return row.get("kind") == "task" or (row.get("kind") == "given" and not (row.get("相手") or "").startswith("@"))


def version_of(root: Path, place: str, use: str = "") -> str | None:
    """参照の今の版。自動で確かめられないもの（URL 等）は None。
    不足（use="不足"）で人・相手の約束が閉じていれば「状態#答えの版の数」（例 done#2）：開き直して別の答えにしたら版が変わる。
    答えの記録が無いまま閉じたものは「#0」＝そろっていない（答えが読めない。独立レビュー R-037 実装の B・G）。"""
    place = place.strip().strip("`")
    if CID_RE.fullmatch(place):
        row = next((r for r in mem.commitment_rows(root) if r.get("ID") == place), None)
        if not row:
            return "なし"
        st = row.get("状態", "")
        if use == "不足" and st in NEED_MET and _human_kind(row):
            return f"{st}#{len(read_need_rec(root, place)['answers'])}"
        return st
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
    if use in ("後工程", "不足") and not CID_RE.fullmatch(place):
        fail(f"{use}の場所は約束の ID（C-006 など）")
    p = ensure_task(root, cid)
    lines = read(p)
    if any(r.get("場所") == place for r in table_rows(lines, "参照")):
        fail(f"{cid} はもう {place} を参照している（版を更新するなら ref touch）")
    v = version_of(root, place, use)
    lines = _append(lines, "参照", [label or place, place, use, v if v is not None else "手動", mem.now_hm()])
    write(p, _touch(lines))
    ok(f"{cid} の参照に {place}（{use}・版 {v or '手動'}）")


def _next_eval(prev: str) -> str:
    """評価日の欄：評価した時刻＋世代（「2026-11-06 10:00 #3」）。世代は ref touch のたびに増え、変化の出来事を分ける。"""
    m = re.search(r"#(\d+)\s*$", prev or "")
    return f"{mem.now_hm()} #{int(m.group(1)) + 1 if m else 2}"


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
        if not place and r.get("使い方") == "不足":
            continue   # 不足は場所を指定して進める（別の変化の処理のついでに解消を消費しない）
        v = version_of(root, r.get("場所", ""), r.get("使い方", ""))
        if v is not None and v != r.get("評価した版"):
            _set_row(lines, "参照", "場所", r["場所"], {"評価した版": v, "評価日": _next_eval(r.get("評価日", ""))})
            done.append(r["場所"])
    if place and not done and not any(r.get("場所") == place for r in table_rows(lines, "参照")):
        fail(f"{cid} は {place} を参照していない")
    write(p, _touch(lines))
    ok(f"{cid} の参照を今の版で評価済みに（{', '.join(done) or '変化なし'}）")
    return done


INFO_MARK = "［記録］"   # 成果物を変えない記録（停止の理由・打ち切り・期限の延長・人の回答で「そのままで可」等）。鮮度の判定に数えない


@locked
def note(root: Path, cid: str, text: str, info: bool = False) -> None:
    p = ensure_task(root, cid)
    lines = read(p)
    if info and not text.startswith(INFO_MARK):
        text = INFO_MARK + text
    i = mem.find_heading(lines, "## 途中の変更")
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
# 何を作るかを決める欄。進め方・上限・報告先は成果物を変えない。期限は延ばしても成果物は古くならないので含めず、
# 前倒しだけを途中の変更として伝える（mem._sync_work_order）
ORDER_SPEC_KEYS = ("目的", "成果物", "完了条件", "実行してよい操作", "行き詰まったら")
ORDER_V, NOTES_V = "作業依頼", "途中の変更"


def order_version(root: Path, cid: str) -> str:
    """作業依頼のうち、何を作るかを決める欄の版。"""
    order = read_task(root, cid).get("order", {})
    text = "\n".join(f"{k}={order.get(k, '')}" for k in ORDER_SPEC_KEYS)
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]


def snapshot(root: Path, cid: str) -> str:
    """実行に使った版（「場所=版」の並び）。参照に加えて、作業依頼の版と途中の変更の件数も残す
    （要求そのものが変わったのに、参照の版が同じだから新しい成果として受け入れる、を防ぐ。外部レビュー #3）。"""
    parts = []
    for r in read_task(root, cid).get("refs", []):
        v = version_of(root, r.get("場所", ""), r.get("使い方", ""))
        parts.append(f"{r.get('場所')}={v if v is not None else '手動'}")
    parts.append(f"{ORDER_V}={order_version(root, cid)}")
    parts.append(f"{NOTES_V}={len(read_task(root, cid).get('notes', []))}")
    return "; ".join(parts)


def _vstate(v: str | None) -> str:
    return (v or "").split("#")[0]


def _met(v: str | None) -> bool:
    """不足の版が「満たした」か：done・dropped（答えの版つきなら #1 以上。#0＝答えなしで閉じた）。以前の版（done だけ）も満たした。"""
    return _vstate(v) in NEED_MET and not (v or "").endswith("#0")


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
def run_end(root: Path, rid: str, result: str, cost: float | None, expect: tuple | None = None) -> bool:
    """実行の終わりを記録する。expect を渡したら、今の結果がそのどれかのときだけ書く（ロックの中で読み直す）。
    後始末（終了不明）・停止・実行役の終了の記録が競っても、先に書かれた完了と費用を上書きしないため。"""
    m = RUN_RE.fullmatch(rid)
    p = task_path(root, m.group(1))
    lines = read(p)
    now = next((x.get("結果", "") for x in table_rows(lines, "実行履歴") if x.get("実行ID") == rid), None)
    if expect is not None and now not in expect:
        return False
    _set_row(lines, "実行履歴", "実行ID", rid, {"終了": mem.now_hm(), "結果": result,
                                              "費用": f"{cost:.2f}" if isinstance(cost, (int, float)) else ""})
    write(p, _touch(lines))
    return True


def run_row(root: Path, rid: str) -> dict:
    m = RUN_RE.fullmatch(rid or "")
    if not m:
        fail(f"実行ID の形が違う：{rid}（C-007-R1 の形）")
    row = next((r for r in read_task(root, m.group(1)).get("runs", []) if r.get("実行ID") == rid), None)
    if not row:
        fail(f"{rid} が実行履歴に無い")
    return row


def unread_notes(root: Path, rid: str, row: dict | None = None, used: dict | None = None) -> list[str]:
    """その実行の開始後に足された途中の変更のうち、作業セッションが読めていないもの。
    作業セッションは報告を書く直前に途中の変更を読む（/run-task 手順6）ので、報告ファイルの更新時刻より後の変更は
    読めていない。終了の記録（run_end）の時刻では測らない：報告を書いてからプロセスが終わるまでの間や、
    終了の記録が無いまま落ちた実行で取りこぼすため。時刻は分単位なので、報告と同じ分の変更は読めていない側に倒す（古い成果を完了にするより、やり直しの方が安い）。"""
    row = row or run_row(root, rid)
    used = used if used is not None else _parse_snapshot(row.get("使った版", ""))
    start = used.get(NOTES_V, "")
    if not start.isdigit():
        return []
    report = results_dir(root) / f"{rid}.md"
    if report.exists():
        seen = datetime.fromtimestamp(report.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    elif row.get("終了"):
        seen = row["終了"]
    else:
        return []   # 実行中で報告もまだ無い
    after = read_task(root, RUN_RE.fullmatch(rid).group(1)).get("notes", [])[int(start):]
    return [n for n in after if INFO_MARK not in n[:24] and n[:16] >= seen]


def done_blocker(root: Path, cid: str) -> str:
    """任せたタスクを完了にしてよいか。最新の実行に報告があり、それが今の条件で古ければ理由を返す（空文字＝よい）。
    ボードの承認・状態ボタン・CLI の set-state done のどの経路からでも同じ検査を通す（外部レビュー #4 と独立レビュー）。"""
    t = read_task(root, cid) if task_path(root, cid).exists() else {}
    opened = open_needs(root, cid, t)
    if opened:   # 不足が残ったまま完了にすると、揃っても再開されない（独立レビュー R-032 の 1）
        return f"{cid} は不足がそろっていない（{', '.join(n['id'] + '「' + n['content'] + '」' for n in opened)}）。そろえてからやり直す"
    runs = t.get("runs", [])
    if not runs:
        return ""
    rid = runs[-1].get("実行ID", "")
    if runs[-1].get("判定", "").startswith("条件待ち"):
        return f"{rid} は不足があって途中までの成果（条件待ち）。不足がそろったのでやり直す"
    if runs[-1].get("結果") == "実行中":
        return f"{rid} がまだ実行中（報告は書きかけかもしれない）。終わるのを待つか、止めてから"
    if not RUN_RE.fullmatch(rid) or not (results_dir(root) / f"{rid}.md").exists():
        return ""
    stale = stale_refs(root, rid)
    return f"{rid} は古い条件で作られた（{', '.join(stale)}）。新しい条件でやり直す" if stale else ""


class DoneBlocked(MemError):
    """完了の検査（不足・実行中・条件待ち・古い条件）で止めた。人が影響なしと判断したら外せる（CLI の --force）。"""


class NeedsAnswer(MemError):
    """人・相手の不足を答えなしで閉じようとした。answer へ回す（--force は案内しない）。"""


@locked
def complete_task(root: Path, cid: str, approve: bool = False) -> str:
    """任せたタスクを完了にする（ボードの承認・状態ボタン・かんばん・CLI の set-state done の共通の入口）。
    確かめてから done を書くまでを1つのロックの中で行う。間に不足の追加・新しい実行の起動・判定の書き換え・
    途中の変更が入っても、それを見てから決める（承認の検査がロックの外だった R-020 からの残り）。
    approve：人の承認。最新の実行の報告が要り、受け入れでなければ受け入れに書き換える（古い条件なら「古い条件」を残して断る）。
    すでに done なら何も書かずに成功（画面の再送・二重押し）。取り下げ（dropped）は完了に上書きしない（別の画面で取り下げたあと）。
    検査で止めたときは DoneBlocked（CLI はこのときだけ --force を案内する）。"""
    state = next((r.get("状態", "") for r in mem.commitment_rows(root) if r.get("ID") == cid), None)
    if state is None:
        fail(f"{cid} が見つからない")
    if state == "done":
        return f"{cid} はもう done（変更なし）"
    if state in mem.CLOSED:
        fail(f"{cid} は {state}（取り下げ済み）。完了にするなら、先に open に戻してから")
    why = close_blocker(root, cid, "done")
    if why:   # 答えずに閉じると止めている仕事が答えを読めない（独立レビュー R-037 の H1）
        raise NeedsAnswer(why)
    if not approve:
        why = done_blocker(root, cid)
        if why:
            raise DoneBlocked(why)
    else:
        t = read_task(root, cid) if task_path(root, cid).exists() else {}
        runs = t.get("runs", [])
        rid = runs[-1].get("実行ID", "") if runs else ""
        if runs and runs[-1].get("結果") == "実行中":   # 報告の有無より先に（承認の画面には前の報告が見えている）
            raise DoneBlocked(f"{rid} がまだ実行中（報告は書きかけかもしれない）。終わってから承認する")
        if not rid or not RUN_RE.fullmatch(rid) or not (results_dir(root) / f"{rid}.md").exists():
            fail("報告が無い")
        opened = open_needs(root, cid, t)
        if opened:   # 不足が残ったまま完了にしない（そろっても再開されなくなる）
            raise DoneBlocked(f"{cid} は不足がそろっていない（{', '.join(n['id'] for n in opened)}）。そろってからやり直す")
        verdict = runs[-1].get("判定", "")
        if verdict.startswith("条件待ち"):
            raise DoneBlocked(f"{rid} は不足があって途中までの成果（条件待ち）。やり直させる")
        # 確認役の受け入れは「確認した時点で妥当」。人が承認するまでに条件が変わることがあるので、毎回確かめる（外部レビュー #4）
        stale = stale_refs(root, rid)
        if stale:
            accept(root, rid, "accept", "人が承認しようとした")   # 判定を「古い条件」に書き換えて残す（このあと断る）
            raise DoneBlocked(f"{rid} は古い条件で作られた（{', '.join(stale)}）。新しい条件でやり直す（やり直させる）")
        if not verdict.startswith("受け入れ"):
            label = accept(root, rid, "accept", "人が承認")
            if label != "受け入れ":
                raise DoneBlocked(f"{rid} は{label}で作られた。新しい条件でやり直す（やり直させる）")
    mem.commitment_set_state(root, cid, "done")
    return f"{cid} を done に" + ("（承認）" if approve else "")


@locked
def set_task_state(root: Path, cid: str, state: str) -> str:
    """done 以外の状態の変更（状態ボタン・かんばん・CLI）。人・相手の不足の取り下げは answer --without へ回す（理由を残す）。"""
    if state == "done":
        return complete_task(root, cid)
    why = close_blocker(root, cid, state)
    if why:
        raise NeedsAnswer(why)
    mem.commitment_set_state(root, cid, state)
    return f"{cid} を {state} に"


def stale_refs(root: Path, rid: str) -> list[str]:
    """その実行が使った版のうち、今は変わっているもの（古い条件で作られた成果）：前提の参照・作業依頼・
    報告を書いたあとに足された途中の変更（［記録］は除く）。版を残していない古い記録は、その項目を比べない。"""
    row = run_row(root, rid)
    used = _parse_snapshot(row.get("使った版", ""))
    cid = RUN_RE.fullmatch(rid).group(1)
    out = []
    if ORDER_V in used and used[ORDER_V] != order_version(root, cid):
        out.append("作業依頼（実行の開始後に変わった）")
    unread = unread_notes(root, rid, row, used)
    if unread:
        out.append(f"途中の変更（報告のあとに {len(unread)} 件）")
    for r in read_task(root, cid).get("refs", []):
        place = r.get("場所", "")
        if r.get("使い方") == "不足":
            # 使った版に無い不足は、実行のあと（報告のあと）に足したもの＝その実行のときはそろっていなかった（独立レビュー R-032 の 1）
            now, then = version_of(root, place, "不足"), used.get(place, "open")
            was = _met(then)
            if not was and _met(now) and _answered_before_report(root, place, rid, r):
                was = then = now   # 実行中・報告の前に答えた（作業役は報告の前に答えを読む。独立レビュー R-037 の M7）
            if _vstate(now) == "done" and _met(now) and not was:
                out.append(f"不足 {place}（そろった。そろう前に作った成果）")
            elif was and not _met(now):
                out.append(f"不足 {place}（そろっていない状態に戻った）")
            elif was and _met(now) and _ans_n(then) and _ans_n(now) and _ans_n(then) != _ans_n(now):
                out.append(f"不足 {place}（答えが変わった）")
            continue   # 取り下げ（dropped）は「無しで進める」と決めたので、無しで作った成果を古くしない
        if r.get("使い方") != "前提":
            continue
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
        if open_needs(root, cid):
            fail(f"{cid} に開いている不足がある。部分的な成果は「条件待ち」（wait）にする")
        if row.get("判定", "").startswith("条件待ち"):
            fail(f"{rid} は条件待ち（不足がそろう前の成果）。受け入れに書き換えず、そろったらやり直す")
        later = [r for r in read_task(root, cid).get("runs", []) if r.get("実行ID") != rid
                 and RUN_RE.fullmatch(r.get("実行ID", "")) and int(RUN_RE.fullmatch(r["実行ID"]).group(2)) > int(RUN_RE.fullmatch(rid).group(2))]
        if later:
            fail(f"{rid} より新しい実行（{later[-1]['実行ID']}）がある。最新の結果を確認する")
        stale = stale_refs(root, rid)
        if stale:
            label = "古い条件"
            reason = f"条件が変わった：{', '.join(stale)}。" + (reason or "")
    if verdict == "wait" and not open_needs(root, cid):
        fail(f"{cid} に開いている不足が無い。先に `work.py need {cid} …` で不足を約束にする")
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


def record_cost(root: Path, kind: str, cost: float | None) -> None:
    """作業セッション以外（定期の見直しなど）の費用を残す。1日の上限に数える。"""
    if not isinstance(cost, (int, float)):
        return
    p = state_dir(root) / "costs.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps({"date": mem.today(), "at": mem.now_hm(), "kind": kind, "cost": round(float(cost), 2)}, ensure_ascii=False) + "\n")


DISPATCH_LOCK_STALE_SEC = 2 * 3600   # tools/schedule/dispatch.py のロックがこれより古ければ止まったものとみなす


def reserved(root: Path) -> float:
    """これから使うかもしれない額（外部レビュー #7）：実行中の作業セッションの上限（.state の budget）と、
    動いている定期の見直しの上限（dispatch.lock）。終われば実際の費用に置き換わり、予約は外れる。"""
    total = 0.0
    lim = limits(root)
    for r in running_runs(root):
        total += _run_budget(root, r.get("実行ID", ""), r.get("task", ""))
    lock = state_dir(root) / "dispatch.lock"
    try:
        if time.time() - lock.stat().st_mtime < DISPATCH_LOCK_STALE_SEC:
            try:
                total += float(json.loads(lock.read_text(encoding="utf-8")).get("budget", 0))
            except (ValueError, TypeError, AttributeError):
                total += 1.5   # 以前の形のロック（時刻だけ）：既定の見直しの上限で見込む
    except OSError:
        pass
    return round(total, 2)


def committed_today(root: Path) -> float:
    """1日の上限と比べる額：使った額＋予約。"""
    return round(spent_today(root) + reserved(root), 2)


@locked
def start_run_within_limits(root: Path, cid: str, budget: float, anyway: bool = False) -> str:
    """同時実行の上限・1日の費用上限（予約を含む）・同じタスクの二重起動・着手条件を確かめ、通れば実行を記録して予約する。
    確かめてから記録するまでを1つのロックの中で行う（同時に2つ起動されても両方は通らない。確かめたあとに不足が足されても通らない）。
    約束の状態もここで確かめ直す（起動前の検査のあと、記録までの間に承認・取り下げで閉じられたら起動しない）。"""
    state = next((r.get("状態", "") for r in mem.commitment_rows(root) if r.get("ID") == cid), None)
    if state is None:
        fail(f"{cid} が約束台帳に無い")
    if state in mem.CLOSED:
        fail(f"{cid} は既に {state}（起動しない）")
    if not anyway:
        unmet = unmet_conditions(root, cid)
        if unmet:
            fail(f"{cid} の着手条件がそろっていない：{'／'.join(unmet)}")
    lim = limits(root)
    running = running_runs(root)
    if any(x.get("task") == cid for x in running):
        fail(f"{cid} は実行中（止めてやり直すなら --stop）")
    if len(running) >= lim["max_running"]:
        fail(f"同時実行の上限 {lim['max_running']} に達している（実行中：{', '.join(x['実行ID'] for x in running)}）")
    spent, res = spent_today(root), reserved(root)
    if spent + res + budget > lim["daily_usd"]:
        fail(f"今日の費用 {spent:g} USD＋実行中の予約 {res:g} USD＋今回の上限 {budget:g} USD が1日の上限 {lim['daily_usd']:g} USD を超える")
    rid = run_start(root, cid)
    p = state_dir(root) / f"{rid}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    mem.write_text_atomic(p, json.dumps({"budget": budget, "reserved_at": round(time.time(), 1)}))
    return rid


def spent_today(root: Path) -> float:
    """今日使った額：今日始めた作業セッションの費用（実行履歴）＋定期の見直しなどの費用（.state/costs.jsonl）。"""
    total = 0.0
    d = mem.today()
    try:
        for ln in (state_dir(root) / "costs.jsonl").read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(ln)
                if e.get("date") == d:
                    total += float(e.get("cost") or 0)
            except (ValueError, TypeError):
                pass
    except OSError:
        pass
    for p in sorted((work_dir(root) / "tasks").glob("C-*.md")) if (work_dir(root) / "tasks").exists() else []:
        for r in table_rows(read(p), "実行履歴"):
            if not r.get("開始", "").startswith(d) or r.get("結果") in ("", "実行中"):
                continue   # 実行中のものは予約（reserved）で数える
            try:
                total += float(r.get("費用"))
            except (TypeError, ValueError):
                # 終わったが費用が分からない（終了不明・停止・失敗）：上限まで使ったかもしれないので予約額で数える。
                # 0 にすると、落ちて再起動を繰り返すたびに1日の上限が効かなくなる（R-022 の独立レビュー）
                total += _run_budget(root, r.get("実行ID", ""), p.stem)
    return round(total, 2)


def _run_budget(root: Path, rid: str, cid: str) -> float:
    """その実行に予約した上限（.state の budget）。記録が無ければ、そのタスクを起動するときの上限で見込む。"""
    try:
        return float(json.loads((state_dir(root) / f"{rid}.json").read_text(encoding="utf-8")).get("budget"))
    except (OSError, ValueError, TypeError):
        return task_budget(root, cid)


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
            new = version_of(root, place, use)
            if new is None or new == old:
                continue
            closed = st in mem.CLOSED
            if use == "不足":
                if _met(old) == _met(new) and not (_met(new) and _ans_n(old) and _ans_n(new) and _ans_n(old) != _ans_n(new)):
                    continue   # 担当の作業の途中（open→in-progress 等）・以前の版（done）から答えの版つきへの書き換え
                if _met(new) and not _met(old):
                    kind = "再開" if not closed else None
                else:   # そろっていたのに戻った（再オープン）：その条件で作った成果を見直す
                    kind = "完了済みの見直し" if closed else ("実行中に変化" if running else "見直し")
                if kind is None:
                    continue
            elif use == "待ち" and not closed:
                kind = "再開"
            elif use == "後工程" and not closed and _vstate(new) == "done":
                kind = "着手"
            elif use == "前提" and not closed:
                kind = "実行中に変化" if running else "見直し"
            elif use == "前提" and closed:
                kind = "完了済みの見直し"
            else:
                continue
            out.append({"task": cid, "label": r.get("情報", ""), "place": place, "use": use, "old": old, "new": new,
                        "kind": kind, "running": running, "task_state": st,
                        # 変化の出来事の識別：どの評価（版と、ref touch のたびに増える世代）からどの版へ。値だけで識別すると
                        # 100→200→100→200 の最後が最初の 200 と同じ扱いになる（外部レビュー #6）。判定しても版を進めていない
                        # （touch していない）間は同じ識別なので二度は提案しない。時刻だけでは同じ分の往復を分けられない
                        "key": f"{cid}|{place}|{old}@{r.get('評価日', '')}→{new}",
                        "legacy_key": f"{cid}|{place}|{new}",
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


REDECIDABLE = ("要承認", "保留")   # 人の確認待ち・後回し：あとで採用・却下などに決め直せる


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
    cur = next((x for x in table_rows(lines, "提案") if x.get("ID") == sid), None)
    if cur is None:
        fail(f"{sid} が無い")
    if cur.get("判定") and not cur["判定"].startswith(REDECIDABLE):
        if cur["判定"] == label:
            return   # 同じ判定の二度目（再送・二重クリック）：何もしない
        fail(f"{sid} はもう「{cur['判定']}」と判定されている（変えるなら新しい提案にする）")   # 外部レビュー #9
    _set_row(lines, "提案", "ID", sid, {"判定": label, "判定理由": reason})
    write(p, lines)
    ok(f"{sid} を「{label}」に（{reason}）")


@locked
def adopt(root: Path, sid: str, who: str, content: str = "", due: str | None = None, rel: str | None = None,
          reason: str = "人がボードで採用") -> tuple:
    """提案を採用し、担当に任せる約束を足す。すでに採用済みなら新しく作らずにその約束を返す（再送・二重クリック・
    別の画面からの操作で同じ仕事が2件にならないように。外部レビュー #9）。確かめて足して判定するまでを1つのロックで行う。
    戻り値：(C-ID, 新しく作ったか)"""
    row = next((x for x in proposal_rows(root) if x.get("ID") == sid), None)
    if not row:
        fail(f"{sid} が無い")
    m = re.match(r"(?:採用|統合)→(C-\d{3,})", row.get("判定", ""))
    if m:
        return m.group(1), False
    if row.get("判定") and not row["判定"].startswith(REDECIDABLE):
        fail(f"{sid} はもう「{row['判定']}」と判定されている")
    from argparse import Namespace
    before = {x.get("ID") for x in mem.commitment_rows(root)}
    mem.commitment_add(root, Namespace(kind="given", content=content or row.get("何をするか", ""), who=who, due=due,
                                       follow=None, rel=rel, state=None))
    cid = next(x.get("ID") for x in mem.commitment_rows(root) if x.get("ID") not in before)
    decide(root, sid, "adopt", cid, reason)
    return cid, True


def scan(root: Path) -> list[dict]:
    """台帳から機械的に見つかる仕事。観察による提案（会議の準備・不足作業・繰り返す問題）は /scout が足す。"""
    today_d = date.today()
    out = []
    rows = {r.get("ID"): r for r in mem.commitment_rows(root)}
    nmap = needs_map(root)
    for r in rows.values():
        if r.get("状態") in mem.CLOSED:
            continue
        cid, content = r.get("ID", ""), _content(r)
        follow = r.get("フォロー日", "")
        who = r.get("相手") or r.get("依頼者") or ""
        h = human_need(root, cid, rows, nmap) if cid in nmap else None
        if h and h["kind"] == "person" and h["blocks"]:
            # 仕事を止めている相手の不足：下書きが無い → 作る／送っていない → ボードと朝のブリーフに出す（提案はしない）／
            # 送ったあとフォロー日 → 催促の下書き（送る前に「催促」を出さない。独立レビュー R-037 の M4）
            rec = read_need_rec(root, cid)
            last = rec["drafts"][-1] if rec["drafts"] else None
            stop = f"（{', '.join(h['blocks'])} を止めている・期限 {r.get('期限', 'なし')}）"
            if last is None:
                out.append({"key": f"下書き|{cid}", "what": f"{who} への連絡の下書きを作る：{cid}「{content}」{stop}",
                            "why": "相手に頼む不足に下書きが無い", "evidence": f"約束台帳 {cid}・不足の記録",
                            "effect": "人がすぐ送れる", "who": "@chief（割り振り）", "task": cid})
            elif last.get("送った") and re.fullmatch(r"\d{4}-\d{2}-\d{2}", follow or "") \
                    and date.fromisoformat(follow) <= today_d:
                out.append({"key": f"フォロー|{cid}|{follow}", "what": f"{who} への催促の下書きを作る：{cid}「{content}」{stop}",
                            "why": f"送った（{last['送った'][:10]}）のに返事が無く、フォロー日 {follow} が来た",
                            "evidence": f"約束台帳 {cid}・不足の記録 版{last['版']}", "effect": "止まっている仕事を進める",
                            "who": "@chief（割り振り）", "task": cid})
        elif r["kind"] == "given" and not who.startswith("@") and re.fullmatch(r"\d{4}-\d{2}-\d{2}", follow or ""):
            if date.fromisoformat(follow) <= today_d and r.get("状態") != "done":
                out.append({"key": f"フォロー|{cid}|{follow}", "what": f"{who} に {cid}「{content}」の進み具合を確認する（催促の下書き）",
                            "why": f"フォロー日 {follow} が来た", "evidence": f"約束台帳 {cid}", "effect": "返答待ちの停滞を防ぐ",
                            "who": "@chief", "task": cid})
        if h and h["blocks"]:
            continue   # 仕事を止めている人・相手の不足は、答え・下書き・送ったで動く（「着手する」の提案は出さない）
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
        mid = esc(f"{c['task']}|{c['place']}|{c['old']}→{c['new']}#")
        if any((r.get("起因") == esc(c["legacy_key"]) and not r.get("判定")) or r.get("起因", "").startswith(mid)
               for r in proposal_rows(root)):
            continue   # 以前の形の識別で同じ出来事の提案がある（C-x|場所|新 は未判定のもの、C-x|場所|旧→新#n はすべて）
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


def unmet_conditions(root: Path, cid: str, t: dict | None = None) -> list[str]:
    """着手条件のうち満たしていないもの（外部レビュー #10）。後工程：前工程の約束が done でない。
    待ち：待っていたものが届いた（その変化の提案を採用・統合した）ことになっていない。前提は着手条件ではない（変われば見直す）。"""
    t = t if t is not None else read_task(root, cid)
    out = []
    adopted = None
    for x in t.get("refs", []):
        place = x.get("場所", "")
        if x.get("使い方") == "後工程" and version_of(root, place) != "done":
            now = version_of(root, place)
            out.append(f"前工程 {place} が取り下げられた（進めるかは人が決める）" if now == "dropped"
                       else f"前工程 {place} が done でない（今 {now}）")
        elif x.get("使い方") == "不足":
            v = version_of(root, place, "不足")
            if not _met(v):
                out.append(f"不足 {place}「{x.get('情報', '').split('｜')[0] or place}」" +
                           ("は答えの記録が無いまま閉じた（ボードの「答える」で答えを残す）" if _vstate(v) in NEED_MET else "がそろっていない"))
        elif x.get("使い方") == "待ち":
            if adopted is None:
                adopted = {sp.split("／")[0] + "／" + "／".join(sp.split("／")[1:-1])
                           for sp in (q.get("起因", "") for q in (proposal_rows(root) if (work_dir(root) / "proposals.md").exists() else [])
                                      if q.get("判定", "").startswith(("採用", "統合")))}
            if f"{cid}／{esc(place)}" not in adopted:
                out.append(f"待っている「{x.get('情報') or place}」がまだ届いていない")
    return out


def _needs_met_since(root: Path, t: dict, run: dict) -> list[str]:
    """その実行のときは満たしていなかった不足のうち、今は満たしているもの。"""
    used = _parse_snapshot(run.get("使った版", ""))
    out = []
    for x in t.get("refs", []):
        if x.get("使い方") != "不足":
            continue
        place = x.get("場所", "")
        then, now = used.get(place, "open"), version_of(root, place, "不足")
        if not _met(now) or _vstate(now) != "done":
            continue
        if _met(then) and not (_ans_n(then) and _ans_n(now) and _ans_n(then) != _ans_n(now)):
            continue   # その実行のときから満たしていて、答えも同じ
        if not _met(then) and _answered_before_report(root, place, run.get("実行ID", ""), x):
            continue
        out.append(place)
    return out


def open_needs(root: Path, cid: str, t: dict | None = None) -> list[dict]:
    """その仕事を止めている、まだそろっていない不足（約束）。"""
    if t is None:
        t = read_task(root, cid) if task_path(root, cid).exists() else {}
    rows = {r.get("ID"): r for r in mem.commitment_rows(root)}
    out = []
    for x in t.get("refs", []):
        if x.get("使い方") != "不足":
            continue
        r = rows.get(x.get("場所", ""), {})
        if r.get("状態") in NEED_MET and (not _human_kind(r) or read_need_rec(root, x.get("場所", ""))["answers"]):
            continue
        out.append({"id": x.get("場所", ""), "content": x.get("情報", "").split("｜")[0], "info": x.get("情報", ""),
                    "who": r.get("相手") or r.get("依頼者") or ("自分" if r.get("kind") == "task" else ""),
                    "due": r.get("期限", ""), "state": r.get("状態", "なし")})
    return out


def needs_map(root: Path) -> dict:
    """不足の逆引き：不足の約束 C-ID → それが止めている仕事の C-ID（止める関係の正本は仕事の側の参照だけ）。"""
    out: dict = {}
    for p in sorted((work_dir(root) / "tasks").glob("C-*.md")) if (work_dir(root) / "tasks").exists() else []:
        for x in table_rows(read(p), "参照"):
            if x.get("使い方") == "不足":
                out.setdefault(x.get("場所", ""), []).append(p.stem)
    return out


def _clean_need(s: str) -> str:
    """表に書く前に、区切りに使う文字（| と ｜）を「／」へ（情報欄は「｜」で内容・理由・確かめ方を区切る）。"""
    return (s or "").strip().replace("|", "／").replace("｜", "／")


def _norm_need(s: str) -> str:
    """二度押しの判定用。空白・読点・括弧を除き、数字の間ではない . と , も除く（3.5万円 と 35万円 は別）。"""
    s = re.sub(r"[\s、。・（）()「」]", "", _clean_need(s))
    return re.sub(r"(?<!\d)[.,]|[.,](?!\d)", "", s).lower()


def _need_order(root: Path, cid: str, nid: str, content: str, due: str | None, check: str) -> None:
    order_set(root, nid, {"目的": f"{cid} の不足をそろえる：{content}",
                          "成果物": "報告（そろえた内容と出所・記憶への記録先の案）",
                          "完了条件": "そろえた内容に出所がある。" + (f"確かめ方：{check}" if check else ""),
                          "実行してよい操作": "読み取り・Web 調査・報告の作成",
                          "期限": due, "レビュー": "none（不足の収集）",
                          "行き詰まったら": "そろえられない理由と、誰に何を聞けばよいかを報告の「確認してほしいこと」に書いて終える（新しい不足は作らない）"})


@locked
def need(root: Path, cid: str, who: str = "", content: str = "", due: str | None = None, why: str = "", check: str = "",
         link: str = "", follow: str | None = None) -> tuple:
    """エージェントに任せた仕事 cid に足りないものを約束にして、参照「不足」で止める（外部レビュー #12・設計 v2）。
    担当：@handle（集める仕事。作業依頼も書く）／自分（自分のタスク）／人の名前（人に出した指示。フォロー日が要る）。
    link を渡すと、既にある約束をこの仕事の不足にも足す。戻り値：(不足の C-ID, 新しく作ったか)。
    冪等：同じ仕事に開いている同じ内容の不足があればそれを返す（二度押し・確認役と人の同時。ロックの中で読み直す）。
    書く順は 約束 → 作業依頼 → 参照（最後の参照が確定の印）。途中で落ちても、やり直せば残った約束を使って続きを書く。"""
    rows = {r.get("ID"): r for r in mem.commitment_rows(root)}
    row = rows.get(cid)
    if not row:
        fail(f"{cid} が約束台帳に無い")
    if row.get("状態") in mem.CLOSED:
        fail(f"{cid} は既に {row.get('状態')}")
    if row.get("kind") != "given" or not (row.get("相手") or "").startswith("@"):
        # 着手・完了の関門と再開はエージェントに任せた仕事のためのもの。自分のタスク・受けた指示は人が進める
        fail(f"{cid} はエージェントに任せた仕事ではない（不足で止められるのは、出した指示で相手が @handle のものだけ）")
    nmap = needs_map(root)
    if cid in nmap:
        # 集める仕事が不足に当たった：新しい不足を作らない（連鎖で費用を使い続けない。独立レビュー R-032 の 5）
        fail(f"{cid} は別の仕事の不足を集める仕事。新しい不足は作らない（集められなければ人へ確認：work.py accept … ask）")
    t = read_task(root, cid) if task_path(root, cid).exists() else {"refs": []}
    opened = open_needs(root, cid, t)
    if link:
        if not CID_RE.fullmatch(link) or link not in rows:
            fail(f"{link} が約束台帳に無い")
        if link == cid:
            fail("自分自身は不足にできない")
        if any(x.get("場所") == link for x in t["refs"]):
            return link, False
        if any(x.get("使い方") == "不足" for x in (read_task(root, link)["refs"] if task_path(root, link).exists() else [])):
            fail(f"{link} は不足を持つ仕事。不足にはできない（循環・連鎖になる）")
        if rows[link].get("状態") not in NEED_MET and len(opened) >= MAX_OPEN_NEEDS:
            fail(f"{cid} の開いている不足が {len(opened)} 件（上限 {MAX_OPEN_NEEDS}）。人へ確認する")
        label = _clean_need(rows[link].get("内容") or rows[link].get("タスク") or link)
        ref_add(root, cid, link, "不足", label + (f"｜理由：{_clean_need(why)}" if why else "") + f"｜{ADDED_MARK}{_now_sec()}")
        return link, False
    content = _clean_need(content)
    if not content:
        fail("--content（足りないもの）が空")
    same = next((n for n in opened if _norm_need(n["content"]) == _norm_need(content)), None)
    if same:
        if (rows.get(same["id"], {}).get("相手") or "").startswith("@") and not task_path(root, same["id"]).exists():
            _need_order(root, cid, same["id"], content, due, check)   # 前回は作業依頼の前で落ちた
        return same["id"], False
    if len(opened) >= MAX_OPEN_NEEDS:
        fail(f"{cid} の開いている不足が {len(opened)} 件（上限 {MAX_OPEN_NEEDS}）。人へ確認する")
    who = (who or "").strip()
    if not who:
        fail("--who（誰がそろえるか：@handle／自分／人の名前）が要る")
    text = f"{content}（{cid} の不足）"
    # 前回、約束を足したあと参照の前で落ちた：その約束を使い直す（2件にしない）
    nid = next((x.get("ID") for x in rows.values() if x.get("状態") not in mem.CLOSED
                and _norm_need(x.get("内容") or x.get("タスク") or "") == _norm_need(text) and x.get("ID") not in nmap), None)
    if not nid:
        from argparse import Namespace
        if who == "自分":
            ns = Namespace(kind="task", content=text, who=None, due=due, follow=None, rel=row.get("関連") or None, state=None)
        else:
            if not who.startswith("@") and not (follow or due):
                fail("人に頼む不足は --due か --follow が要る（催促の日）")
            ns = Namespace(kind="given", content=text, who=who, due=due, follow=None if who.startswith("@") else (follow or due),
                           rel=row.get("関連") or None, state=None)
        before = set(rows)
        mem.commitment_add(root, ns)
        nid = next(x.get("ID") for x in mem.commitment_rows(root) if x.get("ID") not in before)
    if who.startswith("@") and not task_path(root, nid).exists():
        _need_order(root, cid, nid, content, due, check)
    info = content + (f"｜理由：{_clean_need(why)}" if why else "") + (f"｜確かめ方：{_clean_need(check)}" if check else "") \
        + f"｜{ADDED_MARK}{_now_sec()}"
    ref_add(root, cid, nid, "不足", info)
    return nid, True


# ---------------------------------------------------------------- 不足の記録（段階3：人と相手。設計 §10 v2）
# 人・相手の不足（自分のタスク・人に出した指示）は、答え・連絡の下書き（版つき）・送った印を不足ごとの記録
# memory/work/needs/C-xxx.md に1か所だけ持つ。止めている仕事には写さない（関係の正本は仕事の側の参照「不足」）。
# 書くのは answer・draft・sent だけ（手で直さない）。answer・sent は人だけ、draft は無人の割り振り役も（送っていない版が無いときだけ）。


def need_rec_path(root: Path, nid: str) -> Path:
    return work_dir(root) / "needs" / f"{nid}.md"


def _now_sec() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def read_need_rec(root: Path, nid: str) -> dict:
    """不足の記録を読む：{"answers": [{版, 日時, 誰が, 種類, body}], "drafts": [{版, 作成, 書き手, 宛先, 手段, 連絡先, 件名, 送った, body}]}。"""
    out = {"answers": [], "drafts": []}
    p = need_rec_path(root, nid)
    if not p.exists():
        return out
    sec, cur = None, None
    for line in read(p):
        if line.startswith("## "):
            sec = {"## 回答": "answers", "## 下書き": "drafts"}.get(line.strip())
            cur = None
            continue
        m = re.match(r"^### (回答|下書き) (\d+)\s*$", line)
        if m and sec:
            cur = {"版": int(m.group(2)), "body": []}
            out[sec].append(cur)
            continue
        if cur is None:
            continue
        kv = re.match(r"^- ([^:：]+)[:：] ?(.*)$", line)
        if kv and not cur["body"]:
            cur[kv.group(1).strip()] = kv.group(2).strip()
        elif line.startswith(">"):
            cur["body"].append(line[2:] if line.startswith("> ") else line[1:])
    for xs in out.values():
        for x in xs:
            x["body"] = "\n".join(x["body"]).strip("\n")
    return out


def _write_need_rec(root: Path, nid: str, rec: dict) -> None:
    lines = [f"# 不足 {nid}", "",
             f"> 不足の約束 {nid} の答え・連絡の下書き・送った印。`work.py answer / draft / sent` が書く（手で直さない）。",
             "> 止めている仕事は、その仕事の作業記録の参照「不足」が正本。", "", "## 回答", ""]

    def block(kind, x, keys):
        out = [f"### {kind} {x['版']}"] + [f"- {k}: {_one_line(x.get(k, ''))}" for k in keys]
        out += [("> " + b) if b else ">" for b in ((x.get("body") or "").splitlines() or [""])]
        return out + [""]

    for x in rec["answers"]:
        lines += block("回答", x, ("日時", "誰が", "種類"))
    lines += ["## 下書き", ""]
    for x in rec["drafts"]:
        lines += block("下書き", x, ("作成", "書き手", "宛先", "手段", "連絡先", "件名", "送った"))
    p = need_rec_path(root, nid)
    p.parent.mkdir(parents=True, exist_ok=True)
    write(p, lines)


def _one_line(s) -> str:
    """記録の項目は1行（改行・行区切りの文字はすべて空白に）。本文の外に「### 下書き 2」「- 送った:」を書かせない。"""
    return " ".join(str(s or "").splitlines()).strip()


def _body(s: str) -> str:
    return "\n".join((s or "").replace("\\n", "\n").splitlines()).strip()


def last_answer(root: Path, nid: str, rec: dict | None = None) -> dict | None:
    rec = rec if rec is not None else read_need_rec(root, nid)
    return rec["answers"][-1] if rec["answers"] else None


def _ts(s: str) -> float | None:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return time.mktime(time.strptime(s or "", fmt))
        except ValueError:
            continue
    return None


def _ans_n(v: str | None) -> str:
    return (v or "").partition("#")[2]


ADDED_MARK = "足した："


def _ref_added_at(ref: dict | None) -> float | None:
    """参照「不足」を足した時刻（情報欄の「｜足した：…」）。以前の参照には無い（None）。"""
    for part in ((ref or {}).get("情報", "") or "").split("｜"):
        if part.startswith(ADDED_MARK):
            return _ts(part[len(ADDED_MARK):].strip())
    return None


def _answered_before_report(root: Path, nid: str, rid: str, ref: dict | None = None) -> bool:
    """人・相手の不足の答え（または無しで進める）が、その実行の報告より前にあり、その不足もその仕事に報告より前に
    足してあった＝その実行のときは満たしていた（作業役は報告の前に work.py show で不足の答えを読み直す）。
    実行中にボードで答えても成果を古くしない（独立レビュー R-037 の M7）。報告のあとに、答え済みの不足を link で足したら古い（実装の A）。
    時刻は秒で記録するので、報告と同じ秒の答え・参照は「後」に倒す（古い成果を完了にするより、やり直しの方が安い）。"""
    ans = last_answer(root, nid)
    report = results_dir(root) / f"{rid}.md"
    if not ans or not report.exists():
        return False
    mtime = report.stat().st_mtime
    t = _ts(ans.get("日時", ""))
    if t is None or t + 1 > mtime:
        return False
    added = _ref_added_at(ref)
    return added is None or added + 1 <= mtime


def _blocking_open(nid: str, rows: dict, nmap: dict) -> list[str]:
    return [c for c in nmap.get(nid, []) if rows.get(c, {}).get("状態") not in mem.CLOSED]


def human_need(root: Path, nid: str, rows: dict | None = None, nmap: dict | None = None) -> dict | None:
    """仕事を止めている人・相手の不足なら {"kind": "self"|"person", "who", "blocks"（開いた仕事）, "all"}（集める仕事＝@handle は除く）。"""
    rows = rows if rows is not None else {r.get("ID"): r for r in mem.commitment_rows(root)}
    nmap = nmap if nmap is not None else needs_map(root)
    r = rows.get(nid)
    if not r or nid not in nmap:
        return None
    who = (r.get("相手") or "").strip()
    if r.get("kind") == "given" and who.startswith("@"):
        return None
    kind = {"task": "self", "given": "person"}.get(r.get("kind"))
    if kind is None:
        return None
    return {"kind": kind, "who": who or "自分", "blocks": _blocking_open(nid, rows, nmap), "all": nmap.get(nid, [])}


def close_blocker(root: Path, cid: str, state: str) -> str:
    """人・相手の不足を、答え（または無しで進める理由）なしに状態ボタン・かんばん・CLI で閉じようとしたら理由を返す。"""
    if state not in mem.CLOSED:
        return ""
    h = human_need(root, cid)
    if not h or not h["blocks"]:
        return ""   # 止めている仕事がもう無い（閉じた）：普通の約束と同じに閉じてよい
    how = "work.py answer" if state == "done" else "work.py answer … --without 理由"
    word = "完了" if state == "done" else "取り下げ"
    return (f"{cid} は {', '.join(h['blocks'])} を止めている不足。答えずに{word}にすると、止めている仕事は答えを読めない。"
            f"ボードの「答える」「無しで進める」か `{how}` で閉じる")


def _human_only(what: str) -> None:
    if os.environ.get("AI_AGENCY_UNATTENDED"):
        fail(f"無人実行では{what}しない（人だけ）")


@locked
def answer(root: Path, nid: str, text: str, without: bool = False, by: str = "人") -> str:
    """人・相手の不足に答える（または無しで進めると決める）。記録に答えを足してから約束を done（無しなら dropped）。
    二度目：同じ本文なら何もしない、違う本文なら先の答えを返して断る（別の画面で上書きしない）。開き直したあとは新しい版。"""
    _human_only("不足に答え")
    text = _body(text)
    if not text:
        fail("答え（無しで進めるなら理由）が空")
    rows = {r.get("ID"): r for r in mem.commitment_rows(root)}
    if nid not in rows:
        fail(f"{nid} が約束台帳に無い")
    h = human_need(root, nid, rows)
    if not h:
        r = rows[nid]
        if r.get("kind") == "given" and (r.get("相手") or "").startswith("@"):
            fail(f"{nid} はエージェントが集める不足。報告を承認して閉じる（答えで閉じない）")
        fail(f"{nid} は仕事を止めている不足ではない（普通の約束は状態を変える）")
    kind = "無しで進める" if without else "回答"
    state = rows[nid].get("状態", "")
    rec = read_need_rec(root, nid)
    prev = last_answer(root, nid, rec)
    same = prev is not None and prev.get("種類") == kind and prev.get("body", "").strip() == text
    if state in mem.CLOSED and prev:
        if same:
            return f"{nid} はもう{kind}済み（変更なし）"
        fail(f"{nid} は {prev.get('日時', '')} に{prev.get('種類', '')}済み：「{prev.get('body', '')}」。変えるなら先に開き直す")
    # 閉じているが答えの記録が無い（以前の set-state・止めている仕事が無いときに閉じた）：答えを残す
    if not same:   # 同じ本文が最後にある＝前回は記録のあと状態の前で落ちた（または開き直して同じ答え）：足さない
        rec["answers"].append({"版": len(rec["answers"]) + 1, "日時": _now_sec(), "誰が": _clean_need(by) or "人",
                               "種類": kind, "body": text})
        _write_need_rec(root, nid, rec)
    mem.commitment_set_state(root, nid, "dropped" if without else "done")
    ok(f"{nid} に{kind}（{', '.join(h['blocks']) or 'なし'} を止めていた）")
    return f"{nid} に{kind}"


@locked
def draft(root: Path, nid: str, via: str, subject: str, body: str, address: str = "", by: str = "") -> str:
    """相手に頼む不足の連絡の下書き（新しい版）。宛先は約束の「相手」。送るのは人。
    無人：まだ送っていない版があれば書かない（人が直した本文を消さない）。人：送っていない最新の版を書き換える。
    送ったあとの版は書き換えず、次の版（催促）を足す。"""
    rows = {r.get("ID"): r for r in mem.commitment_rows(root)}
    h = human_need(root, nid, rows)
    if not h or h["kind"] != "person":
        fail(f"{nid} は相手に頼む不足ではない（下書きは人に出した指示の不足だけ）")
    if rows[nid].get("状態") in mem.CLOSED:
        fail(f"{nid} は既に {rows[nid].get('状態')}（もう連絡は要らない）")
    body = _body(body)
    if not body:
        fail("本文が空")
    unattended = bool(os.environ.get("AI_AGENCY_UNATTENDED"))
    by = by or ("見直し（無人）" if unattended else "人")
    rec = read_need_rec(root, nid)
    last = rec["drafts"][-1] if rec["drafts"] else None
    unsent = last is not None and not last.get("送った")
    if unsent and unattended:
        return f"{nid} には送っていない下書き（版{last['版']}）がある。無人では書き換えない"
    entry = {"作成": _now_sec(), "書き手": _clean_need(by), "宛先": _clean_need(h["who"]), "手段": _clean_need(via) or "未定",
             "連絡先": _clean_need(address), "件名": _clean_need(subject), "送った": "", "body": body}
    if unsent:
        entry["版"] = last["版"]
        rec["drafts"][-1] = entry
    else:
        entry["版"] = len(rec["drafts"]) + 1
        rec["drafts"].append(entry)
    _write_need_rec(root, nid, rec)
    kind = "催促の下書き" if any(d.get("送った") for d in rec["drafts"]) else "下書き"
    ok(f"{nid} の{kind}（版{entry['版']}・{h['who']} へ・{entry['手段']}）。送るのは人")
    return f"{nid} の{kind} 版{entry['版']}"


def default_follow(due: str, today: date | None = None) -> str:
    """送ったあとのフォロー日：2日後。期限がそれより前なら期限の日、期限を過ぎていれば翌日。"""
    from datetime import timedelta
    today = today or date.today()
    f = today + timedelta(days=2)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", due or ""):
        d_ = date.fromisoformat(due)
        if d_ < f:
            f = max(d_, today + timedelta(days=1))
    return f.isoformat()


@locked
def sent(root: Path, nid: str, follow: str | None = None, version: int | None = None) -> str:
    """人が相手に送った：送っていない最新の版に「送った」を付け、フォロー日を決める。約束の状態は変えない（送ったの正本は記録）。
    送っていない版が無ければ何もしない（二度押しで送っていない催促を記録しない）。"""
    _human_only("送ったことに")
    rows = {r.get("ID"): r for r in mem.commitment_rows(root)}
    h = human_need(root, nid, rows)
    if not h or h["kind"] != "person":
        fail(f"{nid} は相手に頼む不足ではない")
    if rows[nid].get("状態") in mem.CLOSED:
        fail(f"{nid} は既に {rows[nid].get('状態')}")
    follow = mem.check_date(follow, "フォロー日", allow_none=False) if follow else default_follow(rows[nid].get("期限", ""))
    rec = read_need_rec(root, nid)
    last = rec["drafts"][-1] if rec["drafts"] else None
    if version is not None and (last is None or last["版"] != version):
        fail(f"{nid} の下書きは版{last['版'] if last else 'なし'}になっている（画面は版{version}）。見直してから送った")
    if last is None or last.get("送った"):
        return f"{nid} に送っていない下書きは無い（変更なし）"
    mem.commitment_set_follow(root, nid, follow)   # 先にフォロー日（ここで落ちても、やり直しで送った印が付く）
    last["送った"] = _now_sec()
    _write_need_rec(root, nid, rec)
    ok(f"{nid} の下書き 版{last['版']} を送った（フォロー日 {follow}）")
    return f"{nid} 版{last['版']} を送った・フォロー日 {follow}"


def needs_overview(root: Path) -> list[dict]:
    """仕事を止めている人・相手の不足の一覧（読むだけ）：朝のブリーフ・返事の照合（/intake・commitment-track）・見直しが使う。
    答えの記録が無いまま閉じたもの（以前の set-state）も、止めている仕事が開いていれば出す。"""
    rows = {r.get("ID"): r for r in mem.commitment_rows(root)}
    nmap = needs_map(root)
    out = []
    for nid in sorted(nmap):
        h = human_need(root, nid, rows, nmap)
        if not h or not h["blocks"]:
            continue
        r = rows[nid]
        rec = read_need_rec(root, nid)
        closed = r.get("状態") in mem.CLOSED
        if closed and rec["answers"]:
            continue
        last = rec["drafts"][-1] if rec["drafts"] else None
        sent_ = [x for x in rec["drafts"] if x.get("送った")]
        if h["kind"] == "self":
            waiting = "あなたが答える"
        elif closed:
            waiting = "答えを記録する（閉じたが答えの記録が無い）"
        elif last is None:
            waiting = "下書きを作る"
        elif not last.get("送った"):
            waiting = f"下書き 版{last['版']} を送る（人）"
        else:
            waiting = f"{h['who']} の返事を待つ（送った {sent_[-1]['送った'][:10]}）"
        if h["kind"] == "self" and closed:
            waiting = "答えを記録する（閉じたが答えの記録が無い）"
        out.append({"id": nid, "kind": h["kind"], "who": h["who"], "content": _content(r), "due": r.get("期限", ""),
                    "follow": r.get("フォロー日", ""), "state": r.get("状態", ""), "blocks": h["blocks"], "next": waiting,
                    "draft": ({"version": last["版"], "sent": last.get("送った", ""), "via": last.get("手段", "")} if last else None),
                    "sent": sent_[-1]["送った"][:10] if sent_ else ""})
    return out


def needs_view(root: Path, cid: str) -> list[dict]:
    """作業役・割り振り役が読む、その仕事の不足と答え（show が出す）。答えは不足の記録が正本で、作業記録には写さない。"""
    rows = {r.get("ID"): r for r in mem.commitment_rows(root)}
    t = read_task(root, cid) if task_path(root, cid).exists() else {"refs": []}
    out = []
    for x in t.get("refs", []):
        if x.get("使い方") != "不足":
            continue
        nid = x.get("場所", "")
        r = rows.get(nid, {})
        item = {"id": nid, "content": x.get("情報", "").split("｜")[0], "info": x.get("情報", ""), "state": r.get("状態", "なし"),
                "who": r.get("相手") or ("自分" if r.get("kind") == "task" else "")}
        ans = last_answer(root, nid)
        if ans and r.get("状態") in NEED_MET:
            item["answer"] = {"kind": ans.get("種類"), "at": ans.get("日時"), "by": ans.get("誰が"), "text": ans.get("body")}
        elif (r.get("相手") or "").startswith("@") and r.get("状態") == "done":
            item["answer"] = {"kind": "集めた", "text": f"{nid} の報告（memory/work/results/{nid}-R*.md）と作業記録を読む"}
        out.append(item)
    return out


DEFER_MARK = INFO_MARK + "見送り："
RERUN_VERDICTS = ("差し戻し", "古い条件", "条件待ち")


def launch_waiting(root: Path) -> list[dict]:
    """任せたタスクのうち、次に起動するはずなのに動いていないもの（外部レビュー #5）：
    作業依頼まで書いたが一度も起動していない（同時実行・費用の上限で断られた、採用と起動を分けた等）／
    最後の実行が差し戻し・古い条件で、次の実行が無い。
    除くもの：閉じた・相手待ち（waiting）の約束、待ち・後工程の参照があるもの（変化の提案で再開する）、
    最後の記録が「見送り」のもの（`work.py defer`。新しい途中の変更が入れば戻る）。"""
    rows = {r.get("ID"): r for r in mem.commitment_rows(root)}
    nmap = needs_map(root)
    out = []
    for p in sorted((work_dir(root) / "tasks").glob("C-*.md")) if (work_dir(root) / "tasks").exists() else []:
        r = rows.get(p.stem)
        if not r or r.get("状態") in mem.CLOSED or r.get("状態") == "waiting" or r.get("kind") != "given" \
                or not (r.get("相手") or "").startswith("@"):
            continue
        if p.stem in nmap and all(rows.get(c, {}).get("状態") in mem.CLOSED for c in nmap[p.stem]):
            continue   # 不足を集める仕事だが、待っている仕事がもう無い（取り下げ・完了）。費用を使って起こさない
        t = read_task(root, p.stem)
        if unmet_conditions(root, p.stem, t):   # 着手条件がそろうまでは変化の提案で動く
            continue
        if t["notes"] and t["notes"][-1][17:].startswith(DEFER_MARK):
            continue
        runs = t["runs"]
        if not runs:
            why = "作業依頼はあるが、まだ起動していない"
        elif runs[-1].get("結果") == "実行中":
            continue
        elif runs[-1].get("判定", "").startswith(RERUN_VERDICTS):
            why = f"{runs[-1].get('実行ID')} が{runs[-1].get('判定', '')[:4]}で、次の実行が無い"
        elif _needs_met_since(root, t, runs[-1]):
            # 最後の実行は不足がそろわないまま作った。今はそろった（再開の提案を処理・touch したあとでも拾う。独立レビュー R-032 の 2）
            why = f"{runs[-1].get('実行ID')} のあとに不足がそろった（{', '.join(_needs_met_since(root, t, runs[-1]))}）"
        else:
            continue
        handle = (r.get("相手") or "")[1:]
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", handle) or not (root / ".claude" / "agents" / f"{handle}.md").exists():
            # 起動しても断られる（担当がいない）。見直しを起こしても進まないので、理由を出して起こさない
            out.append({"task": p.stem, "who": r.get("相手", ""), "why": why, "startable": False,
                        "blocked": f"担当 {r.get('相手')} がいない（.claude/agents にない）"})
            continue
        out.append({"task": p.stem, "who": r.get("相手", ""), "why": why, "startable": True,
                    "budget": task_budget(root, p.stem)})
    return out


# 作業依頼の「上限」欄の USD：「2.5 USD」「2.5ドル」「$2.5」「USD 2.5」、または数だけ（「2.5」）。「30分」「合計10件」などは読まない
USD_RE = re.compile(r"(?:\$|USD\s*)(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*(?:USD|ドル|\$)", re.I)
NUM_ONLY_RE = re.compile(r"\s*(\d+(?:\.\d+)?)\s*")


def order_budget(root: Path, cid: str) -> float | None:
    """作業依頼の「上限」欄の USD（範囲 1〜20 に揃える）。書いていない・読めない（「既定（work/settings.md）」など）なら None。
    この欄は割り振り役・人が書いた値だけ（起動した額は書き戻さない。今回の額は実行の記録と作業セッションへの指示で渡す）。"""
    if not task_path(root, cid).exists():
        return None
    v = read_task(root, cid).get("order", {}).get("上限", "")
    m = USD_RE.search(v) or NUM_ONLY_RE.fullmatch(v)
    if not m:
        return None
    n = float(next(g for g in m.groups() if g))
    return clamp_run_budget(n) if math.isfinite(n) else None


def task_budget(root: Path, cid: str, review: str | None = None) -> float:
    """そのタスクを起動するときの1回の上限の既定。起動（run_task.default_budget）と起動待ちの見込み（launch_waiting）は必ずこれで決める。
    環境変数 MAX_BUDGET_USD → 作業依頼の「上限」→ settings.md（レビューなしは run_usd、それ以外は run_usd_review）。
    作業依頼の上限を読まないと、割り振り役が書いた上限と実際の上限が別々になる（R-024 の独立レビュー）。
    起動した額を作業依頼へ書き戻すと「今回だけの額」が次の既定・見込みに残る（R-027 の独立レビュー）ので、書き戻さない。"""
    if env_run_budget() is not None:
        return env_run_budget()
    ob = order_budget(root, cid)
    if ob is not None:
        return ob
    lim = limits(root)
    if review is None:
        review = read_task(root, cid).get("order", {}).get("レビュー", "").strip() if task_path(root, cid).exists() else ""
    return lim["run_usd"] if review.startswith("none") else lim["run_usd_review"]


@locked
def defer(root: Path, cid: str, reason: str) -> None:
    """起動待ちのタスクを、今は起動しないと決めた（人の判断待ち・委任範囲の外など）。理由を残し、起動待ちから外す。
    新しい途中の変更が入れば、また起動待ちに戻る。"""
    if not (reason or "").strip():
        fail("見送りの理由が要る（何を待つか）")
    if not task_path(root, cid).exists():
        fail(f"{cid} の作業記録が無い")
    note(root, cid, DEFER_MARK + reason.strip())


def status(root: Path) -> dict:
    lim = limits(root)
    props = proposal_rows(root)
    return {"limits": lim, "spent_today": spent_today(root), "running": running_runs(root),
            "unjudged_runs": unjudged_runs(root), "changes": changes(root), "launch_waiting": launch_waiting(root),
            "proposals_open": [r for r in props if not r.get("判定") or r.get("判定", "").startswith("要承認")],
            "reserved": reserved(root),
            "can_start": len(running_runs(root)) < lim["max_running"] and committed_today(root) < lim["daily_usd"]}


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
    p.write_text(json.dumps({**_last(root), "at": time.time(), "when": mem.now_hm()}, ensure_ascii=False), encoding="utf-8")
    return True


def _last(root: Path) -> dict:
    try:
        return json.loads((state_dir(root) / "last-dispatch.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def memory_ready(root: Path) -> bool:
    """観察する材料があるか（session-start と同じ基準：プロフィール設定済みで、案件か人物が1つ以上）。
    /setup 前は観察しても何も見つからないので、定期の見直しは観察を省く。"""
    m = root / "memory"
    dash = m / "dashboard.md"
    if not dash.exists() or any("**名前**: （未設定）" in line for line in read(dash)):
        return False
    people = [p for p in (m / "people").glob("*.md") if p.name != "_template.md"] if (m / "people").exists() else []
    return bool(list((m / "projects").glob("*/overview.md")) if (m / "projects").exists() else []) or bool(people)


def pending(root: Path, dispatch_budget: float = 0.0) -> dict:
    """トークンを使わずに分かる、割り振り役・確認役の仕事。機械的な提案はここで足してしまう（重複はしない）。
    何も無く観察の間隔も来ていなければ、定期の見直しは Claude を起動しない（空振りに費用を使わない）。"""
    made = propose_from_changes(root) + propose_from_scan(root)
    undecided = [r["ID"] for r in proposal_rows(root) if r.get("ID", "").startswith("S-") and not r.get("判定")]
    unjudged = [r.get("実行ID", "") for r in unjudged_runs(root)]
    lw = launch_waiting(root)
    waiting = [x["task"] for x in lw]
    lim = limits(root)
    spent, free = committed_today(root), len(running_runs(root)) < lim["max_running"]
    # 起動の判定（run_task.check_capacity）と同じ式で、実際に起動できるものがあるときだけ見直しを起こす
    can_start = free and any(x["startable"] and spent + dispatch_budget + x["budget"] <= lim["daily_usd"] for x in lw)
    observe = (time.time() - _last(root).get("observed_at", 0) >= lim["observe_h"] * 3600 - 60
               and memory_ready(root))
    # 起動待ちは、起動できる枠と費用があるときだけ見直しを起こす（起動できないのに毎回 Claude を起動しない）
    return {"proposed": made, "undecided": undecided, "unjudged": unjudged, "waiting": waiting, "observe": observe,
            "work": bool(undecided or unjudged or can_start)}


# ---------------------------------------------------------------- CLI
UNATTENDED_OK = {"order", "ref", "changes", "scan", "propose", "decide", "note", "handoff", "accept", "status", "tick", "show", "pending",
                 "defer", "adopt", "need", "draft", "needs"}   # need・draft は割り振り役（確認役）が使う。作業役の allowed_tools には入れない
# answer・sent は人だけ（無人が不足を閉じる・送ったことにする道を作らない。独立レビュー R-037 の H3）


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
    s = sub.add_parser("adopt", help="提案を採用して担当に任せる約束を足す（採用済みなら既存の C-ID を返す）")
    s.add_argument("id"); s.add_argument("--who", required=True); s.add_argument("--content", default="")
    s.add_argument("--due", default=None); s.add_argument("--rel", default=None); s.add_argument("--reason", required=True)
    s = sub.add_parser("need", help="仕事に足りないものを約束にして止める（そろうまで起動・完了しない）")
    s.add_argument("id"); s.add_argument("--who", default=""); s.add_argument("--content", default="")
    s.add_argument("--due", default=None); s.add_argument("--follow", default=None)
    s.add_argument("--why", default=""); s.add_argument("--check", default=""); s.add_argument("--link", default="")
    s = sub.add_parser("answer", help="人・相手の不足に答える（人だけ）。--without で無しで進める（理由）")
    s.add_argument("id"); s.add_argument("text"); s.add_argument("--without", action="store_true"); s.add_argument("--by", default="人")
    s = sub.add_parser("draft", help="相手に頼む不足の連絡の下書き（送るのは人）")
    s.add_argument("id"); s.add_argument("--via", default="未定"); s.add_argument("--address", default="")
    s.add_argument("--subject", default=""); s.add_argument("--body", required=True); s.add_argument("--by", default="")
    s = sub.add_parser("needs", help="仕事を止めている人・相手の不足の一覧（答える・下書き・送る・返事待ち）")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("sent", help="人が下書きを送った（人だけ）。フォロー日を決める")
    s.add_argument("id"); s.add_argument("--follow", default=None); s.add_argument("--version", type=int, default=None)
    s = sub.add_parser("note"); s.add_argument("id"); s.add_argument("text")
    s.add_argument("--info", action="store_true", help="成果物を変えない記録（鮮度の判定に数えない）")
    s = sub.add_parser("handoff"); s.add_argument("id"); s.add_argument("text")
    s = sub.add_parser("defer", help="起動待ちのタスクを今は起動しない（理由を残す）"); s.add_argument("id"); s.add_argument("reason")
    s = sub.add_parser("accept"); s.add_argument("run"); s.add_argument("verdict"); s.add_argument("--reason", default="")
    s = sub.add_parser("applied"); s.add_argument("run")
    s = sub.add_parser("status"); s.add_argument("--json", action="store_true")
    s = sub.add_parser("show"); s.add_argument("id")
    sub.add_parser("tick")
    s = sub.add_parser("pending"); s.add_argument("--json", action="store_true")
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
            if a.sub == "add" and a.use == "不足":
                fail("不足は `work.py need ID --link C-…`（または --content）で足す（循環・連鎖・上限の検査を通す）")
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
        elif a.cmd == "adopt":
            cid, made = adopt(root, a.id, a.who, a.content, a.due, a.rel, a.reason)
            print(f"{cid}（{'新しく足した' if made else '採用済み・新しくは作らない'}）")
        elif a.cmd == "need":
            nid, made = need(root, a.id, a.who, a.content, a.due, a.why, a.check, a.link, a.follow)
            print(f"{nid}（{'新しく足した' if made else '既にある不足'}）が {a.id} を止める")
        elif a.cmd == "answer":
            print(answer(root, a.id, a.text, a.without, a.by))
        elif a.cmd == "draft":
            print(draft(root, a.id, a.via, a.subject, a.body, a.address, a.by))
        elif a.cmd == "needs":
            items = needs_overview(root)
            if a.json:
                print(json.dumps({"items": items}, ensure_ascii=False))
            else:
                for x in items:
                    print(f"- {x['id']}「{x['content']}」{x['who']}・期限 {x['due']}：{x['next']}（{', '.join(x['blocks'])} を止めている）")
                print(f"計 {len(items)} 件")
        elif a.cmd == "sent":
            print(sent(root, a.id, a.follow, a.version))
        elif a.cmd == "note":
            note(root, a.id, a.text, a.info)
        elif a.cmd == "handoff":
            handoff(root, a.id, a.text)
        elif a.cmd == "defer":
            defer(root, a.id, a.reason)
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
            shown = read_task(root, a.id) if task_path(root, a.id).exists() else {}
            shown["needs"] = needs_view(root, a.id)   # 不足と答え（答えの正本は不足の記録。作業記録には写さない）
            print(json.dumps(shown, ensure_ascii=False, indent=1))
        elif a.cmd == "tick":
            sys.exit(0 if tick(root) else 1)
        elif a.cmd == "pending":
            pd = pending(root)
            print(json.dumps(pd, ensure_ascii=False) if a.json else
                  f"未判定の提案 {len(pd['undecided'])}・未確認の実行 {len(pd['unjudged'])}・観察 {'する' if pd['observe'] else 'まだ'}")
    except MemError as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
