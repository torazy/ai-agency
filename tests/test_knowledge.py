"""知識の置き場（memory/knowledge/：用語集・業務ルール）と、依頼の解読（recall.py decode）。"""

import json
import unittest

from helpers import TempRepo, d


class KnowledgeMemTest(TempRepo):
    def test_term_add_set_and_duplicates(self):
        self.mem("term", "add", "PSR", "--meaning", "パイプライン状況報告", "--aliases", "パイプライン報告", "--kind", "略語")
        g = self.read("memory/knowledge/glossary.md")
        self.assertIn("| PSR | パイプライン報告 | パイプライン状況報告 | 略語 | 全社 |", g)
        self.assertNotEqual(self.mem("term", "add", "psr", "--meaning", "別", check=False).returncode, 0)              # 大小違い
        self.assertNotEqual(self.mem("term", "add", "パイプライン 報告", "--meaning", "x", check=False).returncode, 0)  # 別表記と同じ
        self.assertNotEqual(self.mem("term", "add", "X", "--meaning", "y", "--kind", "謎", check=False).returncode, 0)
        self.mem("term", "set", "パイプライン報告", "meaning", "週次の営業パイプライン報告")                              # 別表記でも引ける
        self.assertIn("週次の営業パイプライン報告", self.read("memory/knowledge/glossary.md"))
        self.mem("check")

    def test_rule_requires_source_and_lifecycle(self):
        self.assertNotEqual(self.mem("rule", "add", "--content", "x", "--source", "", check=False).returncode, 0)
        self.mem("rule", "add", "--content", "経費精算は翌月5営業日までに申請", "--source", "経費規程 第4条", "--effective", d(-30))
        self.assertNotEqual(self.mem("rule", "add", "--content", "経費精算は翌月5営業日までに申請", "--source", "x", check=False).returncode, 0)
        self.mem("rule", "confirm", "R-001")
        self.mem("rule", "set-state", "R-001", "retired")
        self.assertIn("| R-001 | 経費精算は翌月5営業日までに申請 | 全社 | 経費規程 第4条 |", self.read("memory/knowledge/rules.md"))
        self.assertIn("| 廃止 |", self.read("memory/knowledge/rules.md"))
        self.mem("rule", "add", "--content", "経費精算は翌月5営業日までに申請", "--source", "経費規程 改定版")      # 廃止後なら足せる
        self.assertIn("| R-002 |", self.read("memory/knowledge/rules.md"))

    def test_migrate_creates_knowledge_for_old_instances(self):
        (self.root / "memory/knowledge/glossary.md").unlink()
        (self.root / "memory/knowledge/rules.md").unlink()
        out = self.mem("migrate").stdout
        self.assertIn("knowledge/glossary.md を作成", out)
        self.assertTrue((self.root / "memory/knowledge/rules.md").exists())
        self.mem("term", "add", "MTG", "--meaning", "会議")

    def test_unattended_cannot_add_terms_or_rules(self):
        r = self.run_py("tools/mem.py", "term", "add", "X1", "--meaning", "y", env={"AI_AGENCY_UNATTENDED": "1"})
        self.assertNotEqual(r.returncode, 0)


class DecodeTest(TempRepo):
    def setUp(self):
        super().setUp()
        self.seed_people()
        self.seed_project()
        self.mem("term", "add", "PSR", "--meaning", "パイプライン状況報告", "--aliases", "パイプライン報告", "--kind", "略語")
        self.recall = self.load("tools/recall.py", "recall_knowledge")

    def test_decodes_people_projects_terms_and_flags_unknown(self):
        r = self.recall.decode_text(self.root, "架空部長にPSRの件を勤怠刷新の定例で。XYZ-9 と「月締め」も確認")
        kinds = {x["word"]: x["kind"] for x in r["resolved"]}
        self.assertEqual(kinds.get("架空部長"), "person")
        self.assertEqual(kinds.get("PSR"), "term")
        self.assertEqual(kinds.get("勤怠刷新"), "project")
        self.assertIn("XYZ-9", r["unknown"])
        self.assertIn("月締め", r["unknown"])

    def test_ambiguous_person_is_reported(self):
        r = self.recall.decode_text(self.root, "鈴木さんに連絡")
        self.assertEqual(len(r["ambiguous"]), 1)
        self.assertEqual(len(r["ambiguous"][0]["candidates"]), 2)

    def test_find_expands_term_aliases(self):
        self.write("memory/projects/kintai-saas/meetings/2026-10-01.md", "# 定例\n\n## 議論\n- パイプライン報告の様式を変える\n")
        r = self.recall.find_hits(self.root, ["PSR"])
        self.assertTrue(any(h["path"].endswith("meetings/2026-10-01.md") for h in r["hits"]))
        self.assertTrue(any(h["type"] == "term" for h in self.recall.find_hits(self.root, ["PSR"], types={"term"})["hits"]))

    def test_cli(self):
        out = self.recall_cli("decode", "PSR を部長に").stdout
        self.assertIn("パイプライン状況報告", out)


class KnowledgeReviewAndBoardTest(TempRepo):
    def test_review_flags_stale_rules_and_term_collisions(self):
        self.seed_project()
        self.mem("rule", "add", "--content", "古いルール", "--source", "旧規程")
        rp = self.root / "memory/knowledge/rules.md"
        rp.write_text(rp.read_text(encoding="utf-8").replace(f"| {d(0)} | 有効 |", f"| {d(-200)} | 有効 |"), encoding="utf-8")
        self.mem("term", "add", "KP", "--meaning", "a", "--scope", "P:no-such")
        g = self.root / "memory/knowledge/glossary.md"
        lines = g.read_text(encoding="utf-8").splitlines()
        i = next(k for k, ln in enumerate(lines) if ln.startswith("| KP |"))
        lines.insert(i + 1, "| KPI | KP | b | 略語 | 全社 | x | 2026-01-01 |")      # 別表記が既存の用語と同じ（手で書き足した想定）
        g.write_text("\n".join(lines) + "\n", encoding="utf-8")
        recall = self.load("tools/recall.py", "recall_kreview")
        details = " ".join(f.detail for f in recall.review_findings(self.root))
        self.assertIn("確認日から 200 日", details)
        self.assertIn("P:no-such", details)
        self.assertIn("複数の用語に当たる", details)

    def test_board_knowledge_api(self):
        srv = self.load("tools/board/server.py", "board_knowledge")
        srv.mem.ok = lambda m: srv.mem.LAST_OK.append(m)
        self.assertEqual(srv.do_post(self.root, "/api/term/add", {"term": "PSR", "meaning": "報告", "kind_term": "略語"})[0], 200)
        self.assertEqual(srv.do_post(self.root, "/api/term/add", {"term": "X", "meaning": "y", "kind_term": "謎"})[0], 400)
        self.assertEqual(srv.do_post(self.root, "/api/rule/add", {"content": "締めは25日", "source": "経理部長"})[0], 200)
        self.assertEqual(srv.do_post(self.root, "/api/rule/add", {"content": "出典なし"})[0], 400)
        self.assertEqual(srv.do_post(self.root, "/api/rule/confirm", {"rid": "R-001"})[0], 200)
        k = srv.api_knowledge(self.root)
        self.assertEqual(k["terms"][0]["用語"], "PSR")
        self.assertEqual(k["rules"][0]["ID"], "R-001")
        self.assertEqual(k["rules"][0]["confirmed_days"], 0)


if __name__ == "__main__":
    unittest.main()
