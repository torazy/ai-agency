"""エージェントに任せるタスク（約束台帳の @handle）と、エージェントの動きの記録（agent-activity フック → ボード）。"""

import json
import time
import unittest

from helpers import TempRepo, d


class AgentTaskMemTest(TempRepo):
    def setUp(self):
        super().setUp()
        self.write(".claude/agents/generalist.md", "---\nname: generalist\n---\n")
        self.write(".claude/agents/legal.md", "---\nname: legal\n---\n")

    def test_given_to_agent_needs_no_follow_and_checks_handle(self):
        self.mem("commitment", "add", "--kind", "given", "--who", "@generalist", "--content", "競合3社の料金調査", "--due", d(5))
        c = self.read("memory/commitments.md")
        self.assertIn(f"| @generalist | 競合3社の料金調査 | {d(5)} | {d(5)} |", c)      # フォロー日は期限と同じ
        r = self.mem("commitment", "add", "--kind", "given", "--who", "@nobody", "--content", "x", "--due", d(3), check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("@generalist", r.stdout + r.stderr)                                      # いるエージェントを示す
        self.assertNotEqual(self.mem("commitment", "add", "--kind", "given", "--who", "@../x", "--content", "x",
                                     check=False).returncode, 0)
        # 人に出す指示は従来どおりフォロー日が必要
        self.assertNotEqual(self.mem("commitment", "add", "--kind", "given", "--who", "山田", "--content", "見積",
                                     "--due", d(5), check=False).returncode, 0)
        self.mem("check")

    def test_set_content(self):
        self.mem("commitment", "add", "--kind", "task", "--content", "PSR を出す（意味は確認中）", "--due", d(2))
        self.mem("commitment", "set-content", "C-001", "パイプライン状況報告を出す")
        self.assertIn("| パイプライン状況報告を出す |", self.read("memory/commitments.md"))
        self.assertNotEqual(self.mem("commitment", "set-content", "C-001", "", check=False).returncode, 0)
        self.assertNotEqual(self.mem("commitment", "set-content", "C-099", "x", check=False).returncode, 0)
        r = self.run_py("tools/mem.py", "commitment", "set-content", "C-001", "y", env={"AI_AGENCY_UNATTENDED": "1"})
        self.assertNotEqual(r.returncode, 0)                                                     # 無人実行では書き換えない


def hook_ev(hook, **kw):
    return json.dumps({"hook_event_name": hook, "session_id": "s1", **kw}, ensure_ascii=False)


class AgentActivityHookTest(TempRepo):
    def hook(self, event, env=None):
        r = self.run_py("tools/hooks/agent-activity.py", stdin=event, env=env)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(json.loads(r.stdout), {"suppressOutput": True})

    def events(self):
        p = self.root / "memory/task-log/agent-runs.jsonl"
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []

    def test_foreground_background_failure_and_stop(self):
        self.hook(hook_ev("PreToolUse", tool_name="Agent", tool_use_id="t1",
                          tool_input={"subagent_type": "generalist", "description": "C-007 の下調べ", "prompt": "C-008 も見る"}))
        self.hook(hook_ev("PostToolUse", tool_name="Agent", tool_use_id="t1", tool_input={}))
        self.hook(hook_ev("PreToolUse", tool_name="Agent", tool_use_id="t2",
                          tool_input={"description": "長い調査", "prompt": "x", "run_in_background": True}))
        self.hook(hook_ev("PostToolUse", tool_name="Agent", tool_use_id="t2", tool_input={"run_in_background": True}))
        self.hook(hook_ev("PostToolUseFailure", tool_name="Task", tool_use_id="t3", tool_input={}))
        self.hook(hook_ev("SubagentStop", agent_type="general-purpose", agent_id="a1"), env={"AI_AGENCY_UNATTENDED": "1"})
        self.hook(hook_ev("PostToolUse", tool_name="Bash", tool_input={"command": "ls"}))       # 関係ないものは記録しない
        self.hook("not json")                                                                       # 壊れた入力でも邪魔しない
        ev = self.events()
        self.assertEqual([e["ev"] for e in ev], ["start", "end", "start", "launched", "fail", "stop"])
        self.assertEqual(ev[0]["agent"], "generalist")
        self.assertEqual(ev[0]["tasks"], ["C-007", "C-008"])
        self.assertEqual(ev[2]["agent"], "general-purpose")
        self.assertTrue(ev[2]["bg"])
        self.assertTrue(ev[5].get("unattended"))


class BoardActivityTest(TempRepo):
    def setUp(self):
        super().setUp()
        self.write(".claude/agents/generalist.md", "---\nname: generalist\n---\n")
        self.mem("commitment", "add", "--kind", "given", "--who", "@generalist", "--content", "調査A", "--due", d(5))
        self.mem("commitment", "add", "--kind", "given", "--who", "@generalist", "--content", "調査B", "--due", d(5))
        self.mem("commitment", "add", "--kind", "given", "--who", "山田", "--content", "見積", "--due", d(5), "--follow", d(4))
        self.mem("commitment", "add", "--kind", "task", "--content", "体裁を整える", "--due", d(2))
        now = time.time()
        rows = [
            {"ts": now - 300, "sid": "s", "ev": "start", "tuid": "a", "agent": "generalist", "desc": "C-001 調査A", "tasks": ["C-001"], "bg": False},
            {"ts": now - 200, "sid": "s", "ev": "end", "tuid": "a"},
            {"ts": now - 60, "sid": "s", "ev": "start", "tuid": "b", "agent": "generalist", "desc": "C-002 調査B", "tasks": ["C-002"], "bg": True},
            {"ts": now - 59, "sid": "s", "ev": "launched", "tuid": "b"},
            {"ts": now - 9 * 3600, "sid": "s", "ev": "start", "tuid": "c", "agent": "legal", "desc": "放置", "tasks": [], "bg": False},
        ]
        self.write("memory/task-log/agent-runs.jsonl", "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\nbroken\n")
        self.srv = self.load("tools/board/server.py", "board_activity")
        self.srv.mem.ok = lambda m: self.srv.mem.LAST_OK.append(m)

    def test_activity_states(self):
        a = self.srv.api_activity(self.root)
        self.assertEqual([r["tuid"] for r in a["running"]], ["b"])
        states = {r["tuid"]: r["state"] for r in a["recent"]}
        self.assertEqual(states, {"a": "done", "c": "unknown"})                 # 終了の記録が無く時間がたったものは不明
        self.assertEqual(a["by_agent"]["generalist"]["running"], 1)
        self.assertEqual(a["by_agent"]["generalist"]["today"], 2)

    def test_background_stop_closes_run(self):
        with open(self.root / "memory/task-log/agent-runs.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "sid": "s", "ev": "stop", "agent": "generalist"}) + "\n")
        self.assertEqual(self.srv.api_activity(self.root)["running"], [])

    def test_commitments_assignee_and_agent_state(self):
        rows = {r["ID"]: r for r in self.srv.api_commitments(self.root)["rows"]}
        self.assertEqual((rows["C-001"]["assignee"], rows["C-001"]["agent_state"]), ("agent", "review"))   # 終わった→確認待ち
        self.assertEqual((rows["C-002"]["assignee"], rows["C-002"]["agent_state"]), ("agent", "running"))
        self.assertEqual(rows["C-003"]["assignee"], "person")
        self.assertEqual(rows["C-004"]["assignee"], "self")
        self.mem("commitment", "set-state", "C-001", "done")
        rows = {r["ID"]: r for r in self.srv.api_commitments(self.root)["rows"]}
        self.assertEqual(rows["C-001"]["agent_state"], "")                    # 完了にしたら確認待ちは消える

    def test_content_endpoint(self):
        self.assertEqual(self.srv.do_post(self.root, "/api/commitment/content", {"id": "C-004", "content": "体裁と誤字"})[0], 200)
        self.assertIn("| 体裁と誤字 |", self.read("memory/commitments.md"))
        self.assertEqual(self.srv.do_post(self.root, "/api/commitment/content", {"id": "C-004"})[0], 400)


if __name__ == "__main__":
    unittest.main()
