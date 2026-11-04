"""tools/board/server.py の API（サーバーは立てず関数を直接呼ぶ）。書き込みが mem.py と同じ経路で markdown に落ちることを守る。"""

import unittest

from helpers import TempRepo, d


class BoardApiTest(TempRepo):
    def setUp(self):
        super().setUp()
        self.seed_people()
        self.seed_project()
        self.srv = self.load("tools/board/server.py", "board_under_test")

    def test_reads(self):
        root = self.root
        self.assertEqual(len(self.srv.api_people(root)["people"]), 3)
        self.assertEqual(self.srv.api_projects(root)["projects"][0]["slug"], "kintai-saas")
        agents = self.srv.api_agents(root)["agents"]
        self.assertEqual(agents[0]["handle"], "chief")            # .claude/agents が無くても @chief は出る
        self.assertEqual(self.srv.api_review(root)["ask"] + self.srv.api_review(root)["auto"],
                         len(self.srv.api_review(root)["findings"]))
        r = self.srv.api_search(root, {"q": ["架空部長"]})
        self.assertTrue(r["resolved"])
        self.assertEqual(self.srv.api_search(root, {"q": [""]})["total"], 0)

    def test_writes_go_through_mem(self):
        code, res = self.srv.do_post(self.root, "/api/inbox/add", {"text": "画面から追加", "source": "ボード"})
        self.assertEqual((code, res["ok"]), (200, True))
        code, res = self.srv.do_post(self.root, "/api/commitment/add",
                                     {"kind": "task", "content": "画面のタスク", "due": d(3)})
        self.assertEqual(code, 200)
        code, res = self.srv.do_post(self.root, "/api/commitment/state", {"id": "C-001", "state": "dropped"})
        self.assertEqual(code, 200)
        rows = self.srv.api_commitments(self.root)["rows"]
        self.assertEqual(rows[0]["状態"], "dropped")
        self.assertEqual(self.srv.api_projects(self.root)["projects"][0]["open_commitments"], 0)
        code, res = self.srv.do_post(self.root, "/api/person/icon", {"ident": "suzuki-hanako-kakuu-corp-soumu", "icon": "🌸"})
        self.assertEqual(code, 200)
        self.assertEqual(self.srv.api_people(self.root)["people"][1]["icon"], "🌸")
        code, res = self.srv.do_post(self.root, "/api/commitment/state", {"id": "C-999", "state": "done"})
        self.assertEqual((code, res["ok"]), (400, False))
        self.assertEqual(self.srv.do_post(self.root, "/api/unknown", {})[0], 404)


if __name__ == "__main__":
    unittest.main()
