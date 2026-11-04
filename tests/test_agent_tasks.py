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


class RunTaskTest(TempRepo):
    """tools/run_task.py：着手の検査・状態・ボードからの起動（claude は起動しない）。"""

    def setUp(self):
        super().setUp()
        self.write(".claude/agents/pm.md", "---\nname: pm\n---\n")
        self.mem("commitment", "add", "--kind", "given", "--who", "@pm", "--content", "スケジュール案", "--due", d(5))
        self.mem("commitment", "add", "--kind", "task", "--content", "自分の作業", "--due", d(5))
        self.rt = self.load("tools/run_task.py", "run_task_t")

    def test_check_task(self):
        self.assertEqual(self.rt.check_task(self.root, "C-001")["相手"], "@pm")
        for cid, why in [("C-002", "エージェントに任せた"), ("C-099", "無い"), ("x; rm", "形が違う")]:
            with self.assertRaises(ValueError) as cm:
                self.rt.check_task(self.root, cid)
            self.assertIn(why, str(cm.exception))
        self.rt.write_status(self.root, "C-001", state="running", start=time.time())
        with self.assertRaises(ValueError):
            self.rt.check_task(self.root, "C-001")                      # 二重起動しない
        self.rt.check_task(self.root, "C-001", child=True)              # 起動した本人は通す
        self.rt.write_status(self.root, "C-001", start=time.time() - 4 * 3600)
        self.assertEqual(self.rt.read_status(self.root, "C-001")["state"], "unknown")
        self.mem("commitment", "set-state", "C-001", "done")
        with self.assertRaises(ValueError):
            self.rt.check_task(self.root, "C-001")
        (self.root / ".ai-agency-template").write_text("", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "本体"):
            self.rt.check_task(self.root, "C-002")

    def test_allowed_tools_stay_read_only(self):
        tools = self.rt.allowed_tools()
        self.assertIn("Edit(memory/task-results/**)", tools)
        self.assertFalse([t for t in tools if t.startswith(("Write", "Edit")) and t != "Edit(memory/task-results/**)"])
        self.assertFalse([t for t in tools if t.startswith("Bash(") and "mem.py" in t
                          and not any(op in t for op in ("list", "check", "show"))])
        self.assertNotIn("Bash", tools)

    def test_board_run_and_result(self):
        srv = self.load("tools/board/server.py", "board_runtask")
        calls = []
        real = srv.run_task.launch
        srv.run_task.launch = lambda root, cid, budget: (calls.append((cid, budget)), real.__globals__["check_task"](root, cid),
                                                         {"id": cid, "agent": "@pm", "budget": budget})[-1]
        self.assertEqual(srv.do_post(self.root, "/api/task/run", {"id": "C-001", "budget": "0.5"})[0], 200)
        self.assertEqual(calls, [("C-001", 0.5)])
        self.assertEqual(srv.do_post(self.root, "/api/task/run", {"id": "C-002"})[0], 400)       # 自分のタスク
        self.assertEqual(srv.do_post(self.root, "/api/task/run", {"id": "C-001", "budget": "50"})[0], 400)
        self.assertEqual(srv.do_post(self.root, "/api/task/run", {"id": "C-001", "budget": "abc"})[0], 400)
        # 報告が届くと「確認待ち」、失敗はログつき
        self.rt.write_status(self.root, "C-001", state="done", start=time.time() - 60, end=time.time(), exit=0)
        self.write("memory/task-results/C-001.md", "# C-001 スケジュール案\n\n## 結論\n- 3案\n")
        row = next(r for r in srv.api_commitments(self.root)["rows"] if r["ID"] == "C-001")
        self.assertEqual((row["has_result"], row["agent_state"]), (True, "review"))
        self.assertIn("3案", srv.api_task_result(self.root, "C-001")["text"])
        self.assertEqual(srv.api_task_result(self.root, "../x"), {"error": "ID が不正"})
        self.rt.write_status(self.root, "C-001", state="running", start=time.time())
        row = next(r for r in srv.api_commitments(self.root)["rows"] if r["ID"] == "C-001")
        self.assertEqual(row["agent_state"], "running")

    def test_session_start_mentions_waiting_reports(self):
        self.write("memory/task-results/C-001.md", "# 報告\n")
        self.write("memory/task-results/C-002.md", "# 自分のタスクには付かない想定だが、未完了なら出る\n")
        self.mem("commitment", "set-state", "C-002", "done")
        out = self.run_py("tools/hooks/session-start.py").stdout
        self.assertIn("[任せたタスク] 報告が届いて確認待ち：C-001（", out)
        self.assertIn("承認済みで記憶への反映待ち：C-002。", out)                      # 完了にしたが、まだ記憶に入れていない
        self.assertEqual(self.run_py("tools/run_task.py", "--applied", "C-002").returncode, 0)
        self.assertNotEqual(self.run_py("tools/run_task.py", "--applied", "C-003").returncode, 0)
        out = self.run_py("tools/hooks/session-start.py").stdout
        self.assertNotIn("反映待ち", out)

    def test_cli_rejects_without_claude_side_effects(self):
        r = self.run_py("tools/run_task.py", "C-002")
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse((self.root / "memory/task-results/C-002.json").exists())


class BoardGuardTest(TempRepo):
    def test_request_allowed(self):
        srv = self.load("tools/board/server.py", "board_guard")
        f = srv.request_allowed
        self.assertEqual(f("GET", {"Host": "127.0.0.1:8765"}, 8765), "")
        self.assertEqual(f("GET", {"Host": "localhost:8765"}, 8765), "")
        self.assertNotEqual(f("GET", {"Host": "attacker.example:8765"}, 8765), "")     # DNS リバインディング
        self.assertNotEqual(f("GET", {"Host": "127.0.0.1:9999"}, 8765), "")
        ok = {"Host": "127.0.0.1:8765", "Content-Type": "application/json"}
        self.assertEqual(f("POST", ok, 8765), "")
        self.assertNotEqual(f("POST", {**ok, "Content-Type": "text/plain"}, 8765), "")   # フォーム送信型の CSRF
        self.assertNotEqual(f("POST", {**ok, "Origin": "https://attacker.example"}, 8765), "")
        self.assertEqual(f("POST", {**ok, "Origin": "http://localhost:8765"}, 8765), "")


if __name__ == "__main__":
    unittest.main()
