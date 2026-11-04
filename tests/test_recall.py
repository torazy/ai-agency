"""tools/recall.py（探す・読む・点検する）の振る舞い。段階的開示の L1/L2 が正しく・短く返ることを守る。"""

import json
import unittest

from helpers import TempRepo, d


class RecallBase(TempRepo):
    def setUp(self):
        super().setUp()
        self.seed_people()
        self.seed_project()
        self.mem("commitment", "add", "--kind", "received", "--content", "ベンダー比較表の提出", "--who", "架空部長",
                 "--due", d(9), "--rel", "P:kintai-saas")
        self.mem("commitment", "add", "--kind", "received", "--content", "保守費の5年総額を追加", "--who", "鈴木花子",
                 "--due", d(7), "--rel", "P:kintai-saas")
        self.write("memory/projects/kintai-saas/meetings/2026-10-01.md",
                   "# 定例 2026-10-01\n\n## 議論\n- 架空部長: 保守費は数字の裏付けが要る\n- 鈴木さん: 会議室を押さえる\n")
        self.write("memory/projects/kintai-saas/archive/decisions-2025.md",
                   "# 過去分\n\n## 2025-04-01: 旧システムの延命\n- **状態**: 検証済\n- **決定内容**: 保守契約を1年延長\n")
        self.recall = self.load("tools/recall.py", "recall_under_test")


class WhoTest(RecallBase):
    def test_resolves_by_many_names(self):
        ents = [e for e in self.recall.load_entities(self.root) if e.kind == "person"]
        def who(name):
            return [e.key for e, _ in self.recall.resolve(name, ents)]
        self.assertEqual(who("架空部長"), ["kakuu-ichiro-kakuu-corp-bucho"])        # 姓＋役職
        self.assertEqual(who("一郎部長"), ["kakuu-ichiro-kakuu-corp-bucho"])        # 呼び名
        self.assertEqual(who("kakuu@example.invalid"), ["kakuu-ichiro-kakuu-corp-bucho"])  # メール
        self.assertEqual(who("架空"), ["kakuu-ichiro-kakuu-corp-bucho"])            # 姓だけ（1人）
        self.assertEqual(who("花子さん"), ["suzuki-hanako-kakuu-corp-soumu"])
        self.assertEqual(len(who("鈴木")), 2)                                        # 同姓2人＝曖昧
        self.assertEqual(who("山田"), [])                                             # 新規

    def test_cli_exit_codes(self):
        self.assertEqual(self.recall_cli("who", "架空部長").returncode, 0)
        self.assertEqual(self.recall_cli("who", "鈴木").returncode, 2)
        self.assertEqual(self.recall_cli("who", "山田").returncode, 3)


class FindTest(RecallBase):
    def test_alias_expansion_and_sections(self):
        r = self.recall.find_hits(self.root, ["部長", "保守費"])
        self.assertTrue(any("架空 一郎" in n for n in r["resolved"]))
        top = r["hits"][0]
        self.assertTrue(top["path"].endswith("meetings/2026-10-01.md"))
        self.assertEqual(top["heading"], "議論")
        self.assertTrue(any("保守費は数字の裏付け" in s["text"] for s in top["snippets"]))

    def test_table_requires_same_row(self):
        # 台帳には「架空部長」の行と「保守費」の行が別々にある → 表では同じ行に揃わないので出さない
        r = self.recall.find_hits(self.root, ["架空部長", "保守費"], types={"commitment"})
        self.assertEqual(r["total"], 0)
        r = self.recall.find_hits(self.root, ["鈴木花子", "保守費"], types={"commitment"})
        self.assertEqual(r["total"], 1)

    def test_filters_limit_and_archive(self):
        self.assertEqual(self.recall.find_hits(self.root, ["保守"], types={"decision"})["total"], 0)
        self.assertEqual(self.recall.find_hits(self.root, ["保守"], types={"decision"}, archive=True)["total"], 1)
        self.assertLessEqual(len(self.recall.find_hits(self.root, ["kintai"], limit=2)["hits"]), 2)
        self.assertEqual(self.recall.find_hits(self.root, ["存在しない語"])["total"], 0)
        self.assertEqual(self.recall_cli("find", "存在しない語").returncode, 1)

    def test_project_alias(self):
        r = self.recall.find_hits(self.root, ["勤怠刷新"])
        self.assertTrue(any("勤怠SaaS刷新" in n for n in r["resolved"]))


class ShowAndMapTest(RecallBase):
    def test_show_section_and_toc(self):
        out = self.recall_cli("show", "memory/projects/kintai-saas/meetings/2026-10-01.md", "--section", "議論").stdout
        self.assertIn("保守費は数字の裏付け", out)
        self.assertNotIn("# 定例", out)
        self.write("memory/memos/long.md", "# 長いメモ\n\n## 前半\n" + "x\n" * 80 + "## 後半\n- 結論\n")
        out = self.recall_cli("show", "memory/memos/long.md").stdout
        self.assertIn("の目次", out)
        self.assertIn("## 後半", out)

    def test_map(self):
        out = self.recall_cli("map").stdout
        self.assertIn("kintai-saas", out)
        out = self.recall_cli("map", "--type", "people").stdout
        self.assertIn("架空部長", out)


class ReviewTest(TempRepo):
    def test_pristine_template_has_no_findings(self):
        recall = self.load("tools/recall.py", "recall_review_clean")
        self.assertEqual([f.__dict__ for f in recall.review_findings(self.root)], [])

    def test_detects_planted_problems(self):
        self.seed_people()
        self.seed_project(status_date=d(-45))                                     # 古い状況
        self.mem("commitment", "add", "--kind", "received", "--content", "監査ログ設計書の提出", "--who", "架空部長",
                 "--due", d(9), "--rel", "P:kintai-saas")
        self.mem("commitment", "add", "--kind", "received", "--content", "x", "--who", "謎野課長",   # 解決できない相手
                 "--due", d(3), "--rel", "P:no-such-project")                                      # 存在しない案件
        st = self.read("memory/projects/kintai-saas/status.md")
        self.write("memory/projects/kintai-saas/status.md", st + f"| {d(12)} | 監査ログ設計書の提出（架空部長向け） | open |\n")  # 食い違い
        self.write("memory/projects/kintai-saas/meetings/2026-10-01.md", "# 定例\n\n## アクション\n- 鈴木: 資料を作る（期限 来週）\n")  # 取り出し漏れ
        dup = self.read("memory/people/suzuki-taro-vendor-co-eigyo.md").replace("taro@vendor.invalid", "suzuki@example.invalid")
        self.write("memory/people/suzuki-taro-vendor-co-eigyo.md", dup)                         # 重複メール
        recall = self.load("tools/recall.py", "recall_review_dirty")
        fs = recall.review_findings(self.root)
        areas = {(f.area, f.auto) for f in fs}
        details = " ".join(f.detail for f in fs)
        self.assertIn(("食い違い", True), areas)
        self.assertIn("監査ログ設計書", details)
        self.assertIn("no-such-project", details)
        self.assertIn("謎野課長", details)
        self.assertIn(("古さ", False), areas)
        self.assertIn(("取り出し漏れ", True), areas)
        self.assertIn(("重複", False), areas)

    def test_report_is_capped_and_written(self):
        self.seed_people()
        for i in range(8):
            self.write(f"memory/memos/2026-01-0{i + 1}-m.md", f"# メモ\n- 来週までに資料{i}\n")
        out = self.recall_cli("review", "--out", "memory/briefs/review.md").stdout
        self.assertIn("要対応", out)
        text = self.read("memory/briefs/review.md")
        self.assertIn("ほか 3 件", text)           # 区分ごとに上位5件
        full = self.recall_cli("review", "--area", "取り出し漏れ").stdout
        self.assertEqual(full.count("[取り出し漏れ]"), 8)


if __name__ == "__main__":
    unittest.main()
