"""テスト共通の道具。標準ライブラリだけ（unittest）。

インスタンスにも配布されるため、リポジトリの memory/（インスタンスでは実データ）には触らない。
固定データは tests/fixtures/memory（本体の雛形の写し。ずれは test_structure が検出する）。
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FIXTURE_MEMORY = Path(__file__).resolve().parent / "fixtures" / "memory"
TODAY = date.today()


def d(offset: int) -> str:
    """今日から offset 日後の YYYY-MM-DD。"""
    return (TODAY + timedelta(days=offset)).isoformat()


class TempRepo(unittest.TestCase):
    """雛形の memory/ と tools/ を一時フォルダに複製して、その上で試す。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        shutil.copytree(FIXTURE_MEMORY, self.root / "memory")
        shutil.copytree(REPO / "tools", self.root / "tools",
                        ignore=shutil.ignore_patterns("__pycache__", "dev", "*.pyc"))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # ---- 実行
    def run_py(self, script: str, *args: str, stdin: str | None = None, env: dict | None = None,
               check: bool = False) -> subprocess.CompletedProcess:
        e = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        e.pop("AI_AGENCY_UNATTENDED", None)
        if env:
            e.update(env)
        r = subprocess.run([sys.executable, str(self.root / script), *args], cwd=self.root, input=stdin,
                           capture_output=True, text=True, encoding="utf-8", env=e)
        if check and r.returncode != 0:
            self.fail(f"{script} {args} → {r.returncode}\n{r.stdout}\n{r.stderr}")
        return r

    def mem(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return self.run_py("tools/mem.py", *args, check=check)

    def recall_cli(self, *args: str) -> subprocess.CompletedProcess:
        return self.run_py("tools/recall.py", *args)

    def load(self, rel: str, name: str):
        """一時フォルダ側のモジュールを読み込む（REPO 側ではなく複製を使う）。"""
        spec = importlib.util.spec_from_file_location(name, self.root / rel)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        assert spec.loader
        spec.loader.exec_module(mod)
        return mod

    # ---- ファイル
    def read(self, rel: str) -> str:
        return (self.root / rel).read_text(encoding="utf-8")

    def write(self, rel: str, text: str) -> None:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    # ---- 小さな記憶を組み立てる
    def seed_people(self) -> None:
        self.mem("person", "new", "kakuu-ichiro-kakuu-corp-bucho", "--name", "架空 一郎", "--title", "部長",
                 "--org", "架空株式会社 総務部", "--email", "kakuu@example.invalid", "--alias", "一郎部長")
        self.mem("person", "new", "suzuki-hanako-kakuu-corp-soumu", "--name", "鈴木 花子", "--title", "担当",
                 "--org", "架空株式会社 総務部", "--email", "suzuki@example.invalid")
        self.mem("person", "new", "suzuki-taro-vendor-co-eigyo", "--name", "鈴木 太郎", "--title", "営業",
                 "--org", "ベンダー株式会社", "--email", "taro@vendor.invalid")

    def seed_project(self, slug: str = "kintai-saas", title: str = "勤怠SaaS刷新", alias: str = "勤怠刷新",
                     status_date: str | None = None) -> None:
        self.write(f"memory/projects/{slug}/overview.md",
                   f"# {title}（{slug}）\n\n## 概要\n- **目的**: 紙の勤怠をなくす\n- **状態**: 進行中\n"
                   f"- **期限**: {d(120)}\n- **別名**: {alias}\n\n## サービス座標\n| サービス | 場所 | URL/ID |\n|---|---|---|\n"
                   f"| Backlog | プロジェクト | KINTAI |\n")
        self.write(f"memory/projects/{slug}/status.md",
                   f"# 状況（{title}）\n\n**最終更新**: {status_date or d(0)}\n\n## 直近のマイルストーン\n"
                   "| 日付 | 内容 | 状態 |\n|---|---|---|\n")


def dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def ev_json(tool: str, command: str | None = None, file_path: str | None = None, mode: str = "default",
            hook: str = "PostToolUse", sid: str = "abcdef1234") -> str:
    """フックに渡すイベント JSON（Bash は command、Write/Edit は file_path）。"""
    inp = {"command": command} if command is not None else {"file_path": file_path}
    return json.dumps({"tool_name": tool, "tool_input": inp, "hook_event_name": hook,
                       "permission_mode": mode, "session_id": sid}, ensure_ascii=False)
