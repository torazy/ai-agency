"""tools/update_instance.py（ZIP を起点にした、git を使わないインスタンス更新）。
コアだけを最新にし、データ・インスタンスで足したファイルは残し、上書き・削除の前に控えを取る。"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from helpers import FIXTURE_MEMORY, REPO, TempRepo


def make_zip_folder(dst: Path) -> None:
    """「GitHub から落として展開した最新の本体」に相当するフォルダを作る。"""
    for name in ("CLAUDE.md", "README.md", "USAGE.md", "OPERATIONS.md", ".mcp.json", ".gitignore", ".gitattributes"):
        if (REPO / name).exists():
            shutil.copy2(REPO / name, dst / name)
    for d in (".claude", "tools", "tests"):
        shutil.copytree(REPO / d, dst / d, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "settings.local.json"))
    shutil.copytree(FIXTURE_MEMORY, dst / "memory")


class UpdateInstanceTest(TempRepo):
    def setUp(self):
        super().setUp()
        self._zip = tempfile.TemporaryDirectory()
        self.zip = Path(self._zip.name)
        make_zip_folder(self.zip)
        # インスタンス＝前回の本体（少し古い）＋自分の記憶
        inst = self.root
        for name in ("CLAUDE.md", "README.md"):
            shutil.copy2(self.zip / name, inst / name)
        shutil.copytree(self.zip / ".claude", inst / ".claude")
        upd = self.load("tools/update_instance.py", "upd_seed")
        upd.write_manifest(inst)                                          # 前回配った一覧
        (inst / "CLAUDE.md").write_text("古い版の常時ルール\n", encoding="utf-8")   # 本体で更新された…ことにするため一覧も合わせる
        man = json.loads((inst / ".ai-agency-core.json").read_text(encoding="utf-8"))
        man["files"]["CLAUDE.md"] = upd.sha(inst / "CLAUDE.md")
        (inst / ".claude/skills/retired-skill").mkdir(parents=True)
        (inst / ".claude/skills/retired-skill/SKILL.md").write_text("---\nname: retired-skill\n---\n", encoding="utf-8")
        man["files"][".claude/skills/retired-skill/SKILL.md"] = upd.sha(inst / ".claude/skills/retired-skill/SKILL.md")  # 前回は配った
        (inst / ".ai-agency-core.json").write_text(json.dumps(man, ensure_ascii=False), encoding="utf-8")
        (inst / ".claude/agents/marketing.md").write_text("---\nname: marketing\n---\n自分で足した専門家\n", encoding="utf-8")  # 一覧に無い
        (inst / "README.md").write_text("インスタンスで直接直した README\n", encoding="utf-8")  # 一覧と食い違う＝直接編集
        (inst / ".claude/settings.local.json").write_text('{"mine": true}', encoding="utf-8")
        (inst / ".env").write_text("SECRET=1\n", encoding="utf-8")
        (inst / "memory/dashboard.md").unlink()                                       # 古い版には登録簿が無かった
        self.seed_people()
        self.mem("commitment", "add", "--kind", "task", "--content", "自分の約束")

    def tearDown(self):
        self._zip.cleanup()
        super().tearDown()

    def run_update(self, *extra):
        import subprocess, sys, os
        return subprocess.run([sys.executable, str(self.zip / "tools/update_instance.py"), "--instance", str(self.root), *extra],
                              cwd=self.zip, capture_output=True, text=True, encoding="utf-8",
                              env=dict(os.environ, PYTHONUTF8="1"))

    def snapshot_data(self):
        out = {}
        for p in list((self.root / "memory").rglob("*")) + [self.root / ".env", self.root / ".claude/settings.local.json"]:
            if p.is_file():
                out[p.relative_to(self.root).as_posix()] = p.read_bytes()
        return out

    def test_plan_changes_nothing(self):
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        r = self.run_update()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("計画だけ表示した", r.stdout)
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()})

    def test_apply_updates_core_keeps_data_and_additions(self):
        data_before = self.snapshot_data()
        r = self.run_update("--apply", "--skip-tests")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        inst = self.root
        self.assertEqual((inst / "CLAUDE.md").read_text(encoding="utf-8"), (self.zip / "CLAUDE.md").read_text(encoding="utf-8"))
        self.assertTrue((inst / "tools/recall.py").exists())                                  # 本体の新しいファイル
        self.assertTrue((inst / "memory/dashboard.md").exists())                              # 古い版に無かった記憶の雛形は追加
        self.assertFalse((inst / ".claude/skills/retired-skill/SKILL.md").exists())           # 本体から消えた
        self.assertTrue((inst / ".claude/agents/marketing.md").exists())                      # 自分で足した
        self.assertEqual(self.snapshot_data().keys() >= data_before.keys(), True)
        for k, v in data_before.items():
            if k != "memory/commitments.md":                                                   # migrate は冪等で台帳を変えない
                self.assertEqual(self.snapshot_data()[k], v, k)
        self.assertIn("自分の約束", self.read("memory/commitments.md"))
        backups = list((inst / ".ai-agency-backup").rglob("*"))
        names = {p.relative_to(next((inst / ".ai-agency-backup").iterdir())).as_posix() for p in backups if p.is_file()}
        self.assertIn("README.md", names)                                                      # 直接直していたものは控えがある
        self.assertIn(".claude/skills/retired-skill/SKILL.md", names)
        man = json.loads((inst / ".ai-agency-core.json").read_text(encoding="utf-8"))
        self.assertIn("tools/update_instance.py", man["files"])

    def test_second_run_is_noop(self):
        self.run_update("--apply", "--skip-tests")
        r = self.run_update()
        for section in ("## 追加", "## 更新", "## 削除"):
            self.assertNotIn(section, r.stdout)

    def test_refuses_wrong_targets(self):
        self.assertNotEqual(self.run_update_from_self().returncode, 0)
        (self.root / ".ai-agency-template").write_text("", encoding="utf-8")
        self.assertNotEqual(self.run_update().returncode, 0)

    def run_update_from_self(self):
        import subprocess, sys
        return subprocess.run([sys.executable, str(self.zip / "tools/update_instance.py"), "--instance", str(self.zip)],
                              cwd=self.zip, capture_output=True, text=True, encoding="utf-8")


class ImportWritesManifestTest(TempRepo):
    def test_first_migration_records_shipped_core(self):
        old = Path(tempfile.mkdtemp())
        shutil.copytree(FIXTURE_MEMORY, old / "memory")
        r = self.run_py("tools/import_legacy.py", "--from", str(old), "--apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        man = json.loads(self.read(".ai-agency-core.json"))
        self.assertIn("tools/mem.py", man["files"])
        shutil.rmtree(old)


if __name__ == "__main__":
    unittest.main()
