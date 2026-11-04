"""外部レビュー（2026-11-04）で再現した不具合の回帰テスト。

同時保存の消失・決定の部分一致での誤承認・アーカイブ後の ID 再利用・人物観察の重複・取り込み範囲・
承認ログの判定・無人時の書き換え・Backlog 書き込みの記録漏れ・画面 API の入力検証。
"""

import json
import os
import subprocess
import sys
import threading
import unittest
from argparse import Namespace

from helpers import TempRepo, d, ev_json


class ConcurrencyTest(TempRepo):
    def test_threads_do_not_lose_writes(self):
        mem = self.load("tools/mem.py", "mem_threads")
        mem.ok = lambda msg: mem.LAST_OK.append(msg)
        errors = []

        def worker(tag):
            for i in range(25):
                try:
                    mem.inbox_add(self.root, f"{tag}-{i} 同時保存", "test")
                except Exception as e:  # noqa: BLE001
                    errors.append(e)

        ts = [threading.Thread(target=worker, args=(t,)) for t in "ABC"]
        [t.start() for t in ts]
        [t.join() for t in ts]
        text = self.read("memory/inbox.md")
        ids = [ln.split()[3] for ln in text.splitlines() if ln.startswith("- [ ] ")]
        self.assertEqual(errors, [])
        self.assertEqual(len(ids), 75)
        self.assertEqual(len(set(ids)), 75)

    def test_processes_do_not_lose_writes(self):
        """別プロセス（ボードと Claude の CLI が同時に書く状況）でも消えない。"""
        script = (
            "import sys; sys.path.insert(0, r'{tools}');\n"
            "import mem; mem.ok = lambda m: None\n"
            "from pathlib import Path\n"
            "for i in range(15): mem.commitment_add(Path(r'{root}'), __import__('argparse').Namespace("
            "kind='task', content=f'{{sys.argv[1]}}-{{i}}', who=None, due=None, follow=None, rel=None, state=None))\n"
        ).format(tools=self.root / "tools", root=self.root)
        procs = [subprocess.Popen([sys.executable, "-c", script, tag], cwd=self.root) for tag in ("P1", "P2", "P3")]
        for p in procs:
            self.assertEqual(p.wait(timeout=120), 0)
        rows = json.loads(self.mem("commitment", "list", "--json").stdout)
        self.assertEqual(len(rows), 45)
        self.assertEqual(len({r["ID"] for r in rows}), 45)

    def test_no_temp_or_lock_left(self):
        self.mem("inbox", "add", "x")
        leftovers = [p.name for p in (self.root / "memory").rglob(".*") if p.is_file() and p.name != ".gitkeep"]
        self.assertEqual(leftovers, [])


class DecisionIdTest(TempRepo):
    def add(self, title):
        return self.mem("decision", "add", "--project", "p", "--title", title, "--content", "c", "--reason", "r",
                        "--prediction", "x", "--verify-date", d(30)).stdout

    def test_partial_title_never_updates_another_decision(self):
        self.add("CRM導入見送り")
        self.add("CRM導入")
        path = "memory/projects/p/decisions.md"
        self.mem("decision", "set-status", "p", "CRM導入", "decided")  # 完全一致は1件
        text = self.read(path)
        blocks = {b.split("\n")[0]: b for b in text.split("## ")[1:]}
        self.assertIn("提案", next(v for k, v in blocks.items() if k.endswith("CRM導入見送り")))
        self.assertIn("決定", next(v for k, v in blocks.items() if k.endswith(": CRM導入")))
        r = self.mem("decision", "set-status", "p", "CRM", "decided", check=False)  # 部分一致は拒否して候補を示す
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("D-00", r.stderr)

    def test_ids_are_unique_across_projects_and_dup_title_refused(self):
        self.mem("decision", "add", "--project", "a", "--title", "x", "--content", "c", "--reason", "r",
                 "--prediction", "p", "--verify-date", d(30))
        self.mem("decision", "add", "--project", "b", "--title", "y", "--content", "c", "--reason", "r",
                 "--prediction", "p", "--verify-date", d(30))
        self.assertIn("D-002", self.read("memory/projects/b/decisions.md"))
        self.assertNotEqual(self.mem("decision", "add", "--project", "a", "--title", "x", "--content", "c", "--reason", "r",
                                     "--prediction", "p", "--verify-date", d(30), check=False).returncode, 0)

    def test_migrate_assigns_ids_to_old_decisions(self):
        self.write("memory/projects/old/decisions.md",
                   "# 決定\n\n## 2026-01-01: 旧方針\n- **状態**: 決定\n- **決定内容**: a\n\n## 2026-02-01: 次の方針\n- **決定内容**: b\n")
        self.mem("migrate")
        text = self.read("memory/projects/old/decisions.md")
        self.assertIn("- **ID**: D-001", text)
        self.assertIn("- **ID**: D-002", text)
        before = text
        self.mem("migrate")
        self.assertEqual(before, self.read("memory/projects/old/decisions.md"))  # 冪等
        self.mem("decision", "set-status", "old", "D-002", "decided")


class IdReuseAndDedupeTest(TempRepo):
    def test_archived_ids_are_not_reused(self):
        for i in range(3):
            self.mem("commitment", "add", "--kind", "task", "--content", f"t{i}")
        p = self.root / "memory/commitments.md"
        lines = p.read_text(encoding="utf-8").splitlines()
        moved = [ln for ln in lines if ln.startswith("| C-003")]
        p.write_text("\n".join(ln for ln in lines if not ln.startswith("| C-003")) + "\n", encoding="utf-8")
        self.write("memory/archive/commitments-2026.md", "\n".join(moved) + "\n")
        self.mem("commitment", "add", "--kind", "task", "--content", "new")
        self.assertIn("| C-004 |", self.read("memory/commitments.md"))

    def test_same_observation_is_recorded_once(self):
        self.seed_people()
        for _ in range(2):
            self.mem("person", "observe", "suzuki-hanako-kakuu-corp-soumu", "--situation", "予算会議", "--judgment", "根拠を求めた")
        self.assertEqual(self.read("memory/people/suzuki-hanako-kakuu-corp-soumu.md").count("根拠を求めた"), 1)

    def test_dates_and_identifiers_are_validated(self):
        self.assertNotEqual(self.mem("commitment", "add", "--kind", "task", "--content", "x", "--due", "明日", check=False).returncode, 0)
        self.assertNotEqual(self.mem("commitment", "add", "--kind", "task", "--content", "x", "--due", "2026-02-30", check=False).returncode, 0)
        self.mem("commitment", "add", "--kind", "task", "--content", "x", "--due", d(3))
        self.assertNotEqual(self.mem("person", "observe", "../../README", "--situation", "s", "--judgment", "j", check=False).returncode, 0)
        self.assertNotEqual(self.mem("inbox", "add", "   ", check=False).returncode, 0)


class UnattendedTest(TempRepo):
    def test_only_append_and_read_are_allowed(self):
        self.mem("commitment", "add", "--kind", "task", "--content", "x")
        env = {"AI_AGENCY_UNATTENDED": "1"}
        ok = [("inbox", "add", "無人で拾った"), ("commitment", "add", "--kind", "task", "--content", "y"),
              ("commitment", "list"), ("sync", "mark", "gmail"), ("check",)]
        ng = [("commitment", "set-state", "C-001", "done"), ("commitment", "set-due", "C-001", d(5)),
              ("decision", "add", "--project", "p", "--title", "t", "--content", "c", "--reason", "r", "--prediction", "x", "--verify-date", d(9)),
              ("person", "new", "a-b-c-d", "--name", "A B"), ("migrate",)]
        for args in ok:
            self.assertEqual(self.run_py("tools/mem.py", *args, env=env).returncode, 0, args)
        for args in ng:
            r = self.run_py("tools/mem.py", *args, env=env)
            self.assertNotEqual(r.returncode, 0, args)
            self.assertIn("無人実行", r.stderr)

    def test_dispatch_allows_only_records_and_sessions(self):
        spec = __import__("importlib.util").util.spec_from_file_location("dispatch_t", self.root / "tools/schedule/dispatch.py")
        mod = __import__("importlib.util").util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        tools = mod.allowed_tools()
        self.assertFalse([t for t in tools if "mem.py *" in t or "set-state" in t or "set-content" in t])
        self.assertFalse([t for t in tools if t.startswith(("Write", "Edit")) and t != "Edit(memory/briefs/**)"])
        self.assertNotIn("Bash", tools)
        self.assertTrue(all(b < 128 for b in (self.root / "tools/schedule/dispatch.ps1").read_bytes()))
        (self.root / ".ai-agency-template").write_text("", encoding="utf-8")
        r = self.run_py("tools/schedule/dispatch.py", "--force")      # 本体では何もしない（claude を呼ばない）
        self.assertEqual(r.returncode, 0)
        self.assertIn("本体", r.stdout)

    def test_schedule_scripts_do_not_allow_whole_mem_py(self):
        for name in ("morning-brief.ps1", "morning-brief.sh"):
            s = (self.root / "tools/schedule" / name).read_text(encoding="utf-8")
            self.assertNotIn("mem.py *", s, name)
            self.assertNotIn("set-state", s, name)


class ApprovalLogTest(TempRepo):
    def logs(self):
        return [ln for f in sorted((self.root / "memory/task-log").glob("*.md"))
                for ln in f.read_text(encoding="utf-8").splitlines() if ln.startswith("**操作**")]

    def test_approved_only_when_permission_prompt_was_shown(self):
        prompted = ev_json("Bash", "python tools/mem.py inbox add a", hook="PermissionRequest")
        self.assertEqual(self.run_py("tools/hooks/permission-marker.py", stdin=prompted).returncode, 0)
        self.run_py("tools/hooks/operation-logger.py", stdin=ev_json("Bash", "python tools/mem.py inbox add a"))
        self.run_py("tools/hooks/operation-logger.py", stdin=ev_json("Bash", "python tools/mem.py inbox add b"))
        self.run_py("tools/hooks/operation-logger.py", stdin=ev_json("Bash", "python tools/mem.py inbox add a"))  # 印は1回で消費
        lines = self.logs()
        self.assertIn("承認済", lines[0])
        self.assertIn("自動許可", lines[1])
        self.assertIn("自動許可", lines[2])

    def test_backlog_custom_tools_are_logged(self):
        for tool in ("backlog_create_issue", "backlog_update_issue", "backlog_add_comment"):
            self.run_py("tools/hooks/operation-logger.py",
                        stdin=json.dumps({"tool_name": f"mcp__backlog__{tool}", "tool_input": {"issue_key": "X-1"},
                                          "hook_event_name": "PostToolUse", "permission_mode": "default", "session_id": "s"}))
        self.run_py("tools/hooks/operation-logger.py",
                    stdin=json.dumps({"tool_name": "mcp__backlog__backlog_search_issues", "tool_input": {},
                                      "hook_event_name": "PostToolUse", "session_id": "s"}))
        cats = [ln.split("[", 1)[1].split("]", 1)[0] for ln in self.logs()]
        self.assertEqual(cats, ["backlog:backlog_create_issue", "backlog:backlog_update_issue", "backlog:backlog_add_comment"])

    def test_template_has_no_account_specific_connector_ids(self):
        cfg = json.loads((self.root / "tools/hooks/operation-log.json").read_text(encoding="utf-8"))
        self.assertEqual([k for k in cfg.get("server_aliases", {}) if k != "$comment"], [])

    def test_backlog_writes_use_form_encoding(self):
        src = (self.root / "tools/backlog-mcp/server.py").read_text(encoding="utf-8")
        self.assertNotIn("json=body", src)


class BoardValidationTest(TempRepo):
    def setUp(self):
        super().setUp()
        self.seed_people()
        self.srv = self.load("tools/board/server.py", "board_validation")
        self.srv.mem.ok = lambda msg: self.srv.mem.LAST_OK.append(msg)

    def post(self, path, body):
        return self.srv.do_post(self.root, path, body)

    def test_rejects_bad_input_with_400(self):
        cases = [("/api/commitment/add", {"kind": "evil", "content": "x"}),
                 ("/api/inbox/add", {}),
                 ("/api/inbox/add", {"text": ["not", "a", "string"]}),
                 ("/api/commitment/due", {"id": "C-001", "due": "明日"}),
                 ("/api/person/observe", {"ident": "../../x", "situation": "s", "judgment": "j"}),
                 ("/api/decision/status", {"project": "p", "status": "decided"}),
                 ("/api/commitment/state", {"id": "C-001; rm", "state": "done"})]
        for path, body in cases:
            code, res = self.post(path, body)
            self.assertEqual((code, res["ok"]), (400, False), (path, body, res))
        self.assertEqual(self.post("/api/nothing", {})[0], 404)

    def test_decision_status_by_id(self):
        self.mem("decision", "add", "--project", "p", "--title", "CRM導入見送り", "--content", "c", "--reason", "r",
                 "--prediction", "x", "--verify-date", d(30))
        self.mem("decision", "add", "--project", "p", "--title", "CRM導入", "--content", "c", "--reason", "r",
                 "--prediction", "x", "--verify-date", d(30))
        ds = {x["title"]: x for x in self.srv.api_decisions(self.root)["decisions"]}
        code, _ = self.post("/api/decision/status", {"project": "p", "id": ds["CRM導入"]["id"], "status": "decided"})
        self.assertEqual(code, 200)
        ds = {x["title"]: x for x in self.srv.api_decisions(self.root)["decisions"]}
        self.assertTrue(ds["CRM導入"]["status"].startswith("決定"))
        self.assertTrue(ds["CRM導入見送り"]["status"].startswith("提案"))


if __name__ == "__main__":
    unittest.main()
