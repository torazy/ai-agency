"""tools/import_legacy.py（旧版インスタンスからの移行）。データだけを写し、構造は今の版、上書きはしない。"""

import shutil
import tempfile
import unittest
from pathlib import Path

from helpers import FIXTURE_MEMORY, TempRepo

OLD_COMMITMENTS = """# 約束台帳（commitments.md）

## 1. 自分のタスク（細かなもの）

| 追加日 | タスク | 期限 | 状態 |
|---|---|---|---|
| 2026-05-20 | 旧版のタスク | 2026-06-01 | open |

## 2. 受けた指示・依頼（上司・他者 → 自分）

| 受領日 | 依頼者 | 内容 | 期限 | 状態 |
|---|---|---|---|---|
| 2026-05-21 | 田中部長 | 旧版の依頼 | 2026-06-05 | open |

## 3. 出した指示・依頼（自分 → 部下・他者）

| 依頼日 | 相手 | 内容 | 期限 | フォロー日 | 状態 |
|---|---|---|---|---|---|
| （未登録） | | | | | |
"""


class ImportLegacyTest(TempRepo):
    def setUp(self):
        super().setUp()
        self._old = tempfile.TemporaryDirectory()
        self.old = Path(self._old.name)
        m = self.old / "memory"
        shutil.copytree(FIXTURE_MEMORY, m)                       # 旧版も雛形から始まったことにする
        (m / "commitments.md").write_text(OLD_COMMITMENTS, encoding="utf-8")
        (m / "situation.md").write_text((m / "situation.md").read_text(encoding="utf-8") + "\n- 旧版の優先事項: 予算案を月末までに\n", encoding="utf-8")
        (m / "people" / "tanaka-kenji-abc-corp-bucho.md").write_text("# 田中 健司\n\n- **氏名**: 田中 健司\n- **関係の種別**: 社内上司\n", encoding="utf-8")
        (m / "people" / "_template.md").write_text("# 旧い雛形\n", encoding="utf-8")
        (m / "projects" / "old-proj").mkdir(parents=True)
        (m / "projects" / "old-proj" / "overview.md").write_text("# 旧案件（old-proj）\n\n## 概要\n- **状態**: 進行中\n", encoding="utf-8")
        (m / "projects" / "old-proj" / "decisions.md").write_text("# 決定\n\n## 2026-05-31: 旧方針\n- **決定内容**: A案\n", encoding="utf-8")
        (m / "task-log").mkdir()
        (m / "task-log" / "2026-05.md").write_text("**操作**: [x] y ── 承認済\n", encoding="utf-8")
        (m / "service-capabilities.md").write_text("# 旧形式の能力表\n| a | b |\n", encoding="utf-8")
        (m / "dashboard.md").write_text("# 稼働ダッシュボード\n\n- **名前**: 旧版の名前\n", encoding="utf-8")
        (m / "weird-note.md").write_text("# 今の版に無いファイル\n", encoding="utf-8")

    def tearDown(self):
        self._old.cleanup()
        super().tearDown()

    def run_import(self, *extra):
        return self.run_py("tools/import_legacy.py", "--from", str(self.old), *extra)

    def test_plan_changes_nothing(self):
        before = {p: p.read_bytes() for p in (self.root / "memory").rglob("*") if p.is_file()}
        r = self.run_import()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("計画だけ表示した", r.stdout)
        after = {p: p.read_bytes() for p in (self.root / "memory").rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_apply_copies_data_only_and_migrates(self):
        r = self.run_import("--apply")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        mem = self.root / "memory"
        # データは写る
        self.assertTrue((mem / "people" / "tanaka-kenji-abc-corp-bucho.md").exists())
        self.assertTrue((mem / "projects" / "old-proj" / "overview.md").exists())
        self.assertIn("旧版の優先事項", self.read("memory/legacy-import/situation.md"))   # 構造が版で違うので載せ替え用に置く
        self.assertNotIn("旧版の優先事項", self.read("memory/situation.md"))
        # 旧い台帳は ID 列が付き、決定には状態と ID が付く（migrate）
        c = self.read("memory/commitments.md")
        self.assertIn("| C-001 |", c)
        self.assertIn("旧版の依頼", c)
        dec = self.read("memory/projects/old-proj/decisions.md")
        self.assertIn("- **状態**:", dec)
        self.assertIn("- **ID**: D-001", dec)
        # 構造・設定・ログは写らない
        self.assertEqual(self.read("memory/people/_template.md"), (FIXTURE_MEMORY / "people" / "_template.md").read_text(encoding="utf-8"))
        self.assertFalse((mem / "task-log" / "2026-05.md").exists())
        self.assertNotIn("旧形式の能力表", self.read("memory/service-capabilities.md"))
        # 旧版の登録簿と知らないファイルは legacy-import へ。報告が残る
        self.assertTrue((mem / "legacy-import" / "dashboard.md").exists())
        self.assertTrue((mem / "legacy-import" / "weird-note.md").exists())
        self.assertIn("旧版からの移行", self.read("memory/legacy-import/IMPORT-REPORT.md"))
        self.mem("check")

    def test_never_overwrites_own_content_and_is_idempotent(self):
        self.mem("inbox", "add", "新しい版で先に入れたもの")
        own_situation = self.read("memory/situation.md") + "\n- 新しい版で書いた優先事項\n"
        self.write("memory/situation.md", own_situation)
        self.write("memory/insights.md", self.read("memory/insights.md") + "\n- 新しい版で書いた気づき\n")
        (self.old / "memory" / "insights.md").write_text(self.read("memory/insights.md").replace("新しい版で書いた気づき", "旧版の気づき"), encoding="utf-8")
        self.run_import("--apply")
        self.assertEqual(self.read("memory/situation.md"), own_situation)          # 上書きしない
        self.assertIn("新しい版で書いた気づき", self.read("memory/insights.md"))    # 両方に中身 → 上書きしない
        self.assertIn("旧版の気づき", self.read("memory/legacy-import/insights.md"))
        first = {p.relative_to(self.root).as_posix(): p.read_bytes() for p in (self.root / "memory").rglob("*")
                 if p.is_file() and p.name != "IMPORT-REPORT.md"}
        r = self.run_import("--apply")                                            # 2回目
        self.assertEqual(r.returncode, 0)
        second = {p.relative_to(self.root).as_posix(): p.read_bytes() for p in (self.root / "memory").rglob("*")
                  if p.is_file() and p.name != "IMPORT-REPORT.md"}
        self.assertEqual(first, second)

    def test_customized_delegation_is_never_overwritten(self):
        d = self.old / "memory" / "delegation.md"
        d.write_text(d.read_text(encoding="utf-8") + "\n| Slack 定型返信 | #general のみ | 2026-05-01 | 誤字修正だけ |\n", encoding="utf-8")
        before = self.read("memory/delegation.md")
        self.run_import("--apply")
        self.assertEqual(self.read("memory/delegation.md"), before)
        self.assertIn("Slack 定型返信", self.read("memory/legacy-import/delegation.md"))

    def test_refuses_template_and_missing_memory(self):
        (self.root / ".ai-agency-template").write_text("", encoding="utf-8")
        self.assertNotEqual(self.run_import("--apply").returncode, 0)
        (self.root / ".ai-agency-template").unlink()
        self.assertNotEqual(self.run_py("tools/import_legacy.py", "--from", str(self.root / "nowhere")).returncode, 0)

    def test_legacy_import_is_not_searched_or_reviewed(self):
        self.run_import("--apply")
        recall = self.load("tools/recall.py", "recall_after_import")
        self.assertEqual(recall.find_hits(self.root, ["旧版の名前"])["total"], 0)
        self.assertFalse(any("legacy-import" in f.target for f in recall.review_findings(self.root)))


if __name__ == "__main__":
    unittest.main()
