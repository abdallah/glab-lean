"""Unit tests for glab-lean's parsers and helpers. Fixtures are synthetic."""
import importlib.machinery
import importlib.util
import io
import json
import os
import subprocess
import sys
import types
import unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "..", "bin", "glab-lean")
loader = importlib.machinery.SourceFileLoader("glab_lean", PATH)
spec = importlib.util.spec_from_loader("glab_lean", loader)
gl = importlib.util.module_from_spec(spec)
loader.exec_module(gl)

TS = "2026-01-01T00:00:00.000000Z"
RAW_LOG = "\n".join([
    f"{TS} 00O \x1b[0KRunning with gitlab-runner 18.0.0\x1b[0;m",
    f"{TS} 00O section_start:1700000000:prepare_executor\r\x1b[0K\x1b[36;1mPreparing\x1b[0;m",
    f"{TS} 00O section_end:1700000001:prepare_executor\r\x1b[0K",
    f"{TS} 01O $ make test",
    f"{TS} 01O progress 10%\rprogress 100%",
    f"{TS} 01O part one",
    f"{TS} 01O+ and part two",
    f"{TS} 01E \x1b[31mError: something broke\x1b[0m",
])

PLAN = """\
Note: Objects have changed outside of Terraform

  # aws_s3_bucket.logs has changed
  ~ resource "aws_s3_bucket" "logs" {
    }

Terraform will perform the following actions:

  # aws_iam_policy.new will be created
  + resource "aws_iam_policy" "new" {
      + name = "new"
    }

  # module.app.aws_iam_role.this[0] will be updated in-place
  ~ resource "aws_iam_role" "this" {
        id   = "role"
      ~ tags = {
          - "old" = "x" -> null
        }
        # (3 unchanged attributes hidden)
    }

  # aws_instance.web must be replaced
-/+ resource "aws_instance" "web" {
      ~ ami = "ami-1" -> "ami-2" # forces replacement
    }

  # aws_sqs_queue.old will be destroyed
  - resource "aws_sqs_queue" "old" {
    }

  # data.aws_caller_identity.me will be read during apply
  # aws_s3_bucket.a has moved to aws_s3_bucket.b

Plan: 1 to add, 1 to change, 2 to destroy.
╷
│ Error: Invalid reference
│
│ A reference to a resource type must be followed by an attribute.
╵
"""


class CleanLog(unittest.TestCase):
    def setUp(self):
        self.lines = gl.clean_log(RAW_LOG)

    def test_strips_timestamps_and_ansi(self):
        self.assertIn("Running with gitlab-runner 18.0.0", self.lines)
        self.assertFalse(any("\x1b" in l or l.startswith("2026-") for l in self.lines))

    def test_sections_become_headers(self):
        self.assertIn("== prepare_executor", self.lines)
        self.assertFalse(any("section_end" in l for l in self.lines))

    def test_carriage_return_keeps_last_frame(self):
        self.assertIn("progress 100%", self.lines)
        self.assertNotIn("progress 10%", " ".join(self.lines))

    def test_continuation_lines_join(self):
        self.assertIn("part one and part two", self.lines)

    def test_no_double_blank_lines(self):
        self.assertFalse(any(a == b == "" for a, b in zip(self.lines, self.lines[1:])))


class TerraformParse(unittest.TestCase):
    def setUp(self):
        self.lines = PLAN.split("\n")
        self.res, self.totals, self.errs = gl.tf_parse(self.lines)

    def test_symbols(self):
        got = {name: sym for _, sym, name in self.res}
        self.assertEqual(got["aws_iam_policy.new"], "+")
        self.assertEqual(got["module.app.aws_iam_role.this[0]"], "~")
        self.assertEqual(got["aws_instance.web"], "-/+")
        self.assertEqual(got["aws_sqs_queue.old"], "-")
        self.assertEqual(got["data.aws_caller_identity.me"], "<=")
        self.assertEqual(got["aws_s3_bucket.logs"], "drift~")
        self.assertTrue(any(s == "->" and "has moved to aws_s3_bucket.b" in n for _, s, n in self.res))

    def test_totals_and_errors(self):
        self.assertEqual(self.totals, ["Plan: 1 to add, 1 to change, 2 to destroy."])
        self.assertEqual(len(self.errs), 1)
        self.assertIn("Invalid reference", self.errs[0])
        self.assertIn("must be followed by an attribute", self.errs[0])

    def test_block_changed_lines_only(self):
        i = next(i for i, s, n in self.res if n.startswith("module.app"))
        block = gl.tf_block(self.lines, i, full=False)
        text = "\n".join(block)
        self.assertIn('- "old" = "x" -> null', text)
        self.assertNotIn('id   = "role"', text)
        self.assertNotIn("unchanged attributes hidden", text)
        self.assertNotIn("aws_instance", text)

    def test_block_full(self):
        i = next(i for i, s, n in self.res if n.startswith("module.app"))
        self.assertIn('        id   = "role"', gl.tf_block(self.lines, i, full=True))

    def test_block_keeps_forces_replacement(self):
        i = next(i for i, s, n in self.res if n == "aws_instance.web")
        self.assertTrue(any("forces replacement" in l for l in gl.tf_block(self.lines, i, False)))


class Helpers(unittest.TestCase):
    def test_dur(self):
        self.assertEqual(gl.dur(None), "-")
        self.assertEqual(gl.dur(75.4), "1m15s")
        self.assertEqual(gl.dur(3725), "1h02m")

    def test_cut(self):
        self.assertEqual(gl.cut("a  b\nc", 10), "a b c")
        self.assertEqual(len(gl.cut("x" * 50, 10)), 10)

    def test_dig(self):
        self.assertEqual(gl.dig({"a": {"b": 1}}, "a.b"), 1)
        self.assertIsNone(gl.dig({"a": None}, "a.b"))

    def test_per_page(self):
        self.assertEqual(gl.per_page("x?per_page=100&a=1"), 100)
        self.assertEqual(gl.per_page("x"), 20)

    def test_on_head(self):
        self.assertTrue(gl.on_head({"sha": "a"}, {"sha": "a"}))
        with mock.patch.object(gl, "glab", return_value={"parent_ids": ["a"]}):
            self.assertTrue(gl.on_head({"sha": "m", "ref": "refs/merge-requests/1/merge"}, {"sha": "a"}))
        self.assertFalse(gl.on_head({"sha": "b", "ref": "main"}, {"sha": "a"}))

    def test_kv(self):
        self.assertEqual(gl.kv(["A=1", "B=x=y"]), [{"key": "A", "value": "1"}, {"key": "B", "value": "x=y"}])


def fake_run(pages):
    """Return a subprocess.run stand-in that serves JSON pages by `page=` query."""
    calls = []

    def run(cmd, input=None, capture_output=None):
        calls.append(cmd)
        path = cmd[-1]
        page = int(path.split("page=")[-1]) if "page=" in path.split("per_page=")[-1] or "&page=" in path else 1
        body = json.dumps(pages[page - 1] if page <= len(pages) else [])
        return types.SimpleNamespace(returncode=0, stdout=body.encode(), stderr=b"")
    return run, calls


class Api(unittest.TestCase):
    def test_paginates_until_short_page(self):
        run, calls = fake_run([[{"id": 1}, {"id": 2}], [{"id": 3}]])
        with mock.patch.object(gl.subprocess, "run", run):
            rows = gl.glab("projects/:id/jobs?per_page=2", paginate=True)
        self.assertEqual([r["id"] for r in rows], [1, 2, 3])
        self.assertEqual(len(calls), 2)

    def test_error_surfaces_last_stderr_line(self):
        def run(cmd, input=None, capture_output=None):
            return types.SimpleNamespace(returncode=1, stdout=b"", stderr=b"noise\n404 Not Found")
        with mock.patch.object(gl.subprocess, "run", run):
            with self.assertRaises(gl.ApiError) as e:
                gl.glab("projects/:id/jobs/1")
        self.assertIn("404 Not Found", str(e.exception))

    def test_body_goes_to_stdin_not_argv(self):
        seen = {}

        def run(cmd, input=None, capture_output=None):
            seen["cmd"], seen["input"] = cmd, input
            return types.SimpleNamespace(returncode=0, stdout=b"{}", stderr=b"")
        with mock.patch.object(gl.subprocess, "run", run):
            gl.glab("projects/:id/notes", "POST", {"body": "secret; rm -rf /"})
        self.assertNotIn("secret; rm -rf /", " ".join(seen["cmd"]))
        self.assertEqual(json.loads(seen["input"]), {"body": "secret; rm -rf /"})

    def test_api_where_and_fields(self):
        rows = [{"id": 1, "status": "failed", "pipeline": {"id": 9}},
                {"id": 2, "status": "success", "pipeline": {"id": 9}}]
        with mock.patch.object(gl, "glab", return_value=rows):
            out = io.StringIO()
            with redirect_stdout(out):
                gl.cmd_api(types.SimpleNamespace(path="x", paginate=False, fields="id,pipeline.id",
                                                 where=["status!=success"], limit=50))
        self.assertEqual(out.getvalue(), "1\t9\n")


class Security(unittest.TestCase):
    def run_cli(self, *argv):
        return subprocess.run([sys.executable, PATH, *argv], capture_output=True, text=True)

    def test_refuses_absolute_urls_and_odd_paths(self):
        for bad in ("http://evil.example/x", "https://evil.example/x", "//evil.example/x",
                    "/projects/1", "-XPOST", "graphql", "projects/1/../../x", "projects/1#frag"):
            with self.assertRaises(gl.ApiError, msg=bad):
                gl.check_path(bad)
        for good in ("projects/:id/jobs?scope[]=failed", "projects/group%2Fproj/pipelines/1",
                     "merge_requests?scope=created_by_me", "version"):
            gl.check_path(good)

    def test_api_url_never_reaches_glab(self):
        with mock.patch.object(gl.subprocess, "run") as run:
            with self.assertRaises(gl.ApiError):
                gl.glab("https://evil.example/steal")
        run.assert_not_called()

    def test_repo_must_be_a_path(self):
        p = self.run_cli("-R", "https://evil.example/g/p", "pipes")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("not a URL", p.stderr)

    def test_ids_are_validated(self):
        self.assertNotEqual(self.run_cli("job", "1/../../x").returncode, 0)
        self.assertNotEqual(self.run_cli("resolve", "1", "x/../../issues").returncode, 0)
        self.assertNotEqual(self.run_cli("mr", "abc").returncode, 0)
        self.assertEqual(gl.mr_iid("!42"), "42")

    def test_sanitize_strips_terminal_controls(self):
        evil = ("a\x1b]52;c;ZXZpbA==\x1b\\b\x1b]8;;http://x\x07c\x1bP1$qm\x1b\\d"
                "\x1b[>0ce\x9b31mf\x1bcg\u202eh\u200bi\rj\x07k")
        self.assertEqual(gl.sanitize(evil), "abcdefghijk")
        self.assertEqual(gl.sanitize("tab\tand\nnewline"), "tab\tand\nnewline")

    def test_stdout_is_sanitized(self):
        buf = io.StringIO()
        gl.SafeOut(buf).write("title\x1b]52;c;x\x07!")
        self.assertEqual(buf.getvalue(), "title!")

    def test_paginate_stops_on_objects_and_caps_pages(self):
        run, calls = fake_run([{"id": 1, **{f"k{i}": i for i in range(30)}}])
        with mock.patch.object(gl.subprocess, "run", run):
            self.assertEqual(gl.glab("projects/:id/merge_requests/1", paginate=True)["id"], 1)
        self.assertEqual(len(calls), 1)

        def endless(cmd, input=None, capture_output=None):
            return types.SimpleNamespace(returncode=0, stdout=json.dumps([{"id": 1}] * 100).encode(), stderr=b"")
        with mock.patch.object(gl.subprocess, "run", endless), redirect_stdout(io.StringIO()):
            rows = gl.glab("projects/:id/jobs?per_page=100", paginate=True)
        self.assertEqual(len(rows), 100 * gl.MAX_PAGES)

    def test_per_page_over_cap_still_paginates(self):
        run, calls = fake_run([[{"id": i} for i in range(100)], [{"id": 100}]])
        with mock.patch.object(gl.subprocess, "run", run):
            self.assertEqual(len(gl.glab("projects/:id/jobs?per_page=500", paginate=True)), 101)


def ns(**kw):
    return types.SimpleNamespace(**kw)


class Regressions(unittest.TestCase):
    """One test per bug found in review."""

    def wait(self, responses, **kw):
        it = iter(responses)

        def fake(path, *x, **k):
            r = next(it)
            if isinstance(r, Exception):
                raise r
            return r
        args = dict(kind="job", id="5", until=None, every=0, timeout=60)
        args.update(kw)
        with mock.patch.object(gl, "glab", side_effect=fake), mock.patch.object(gl, "cmd_job"), \
                mock.patch.object(gl, "pipe_summary"), mock.patch.object(gl.time, "sleep"), \
                redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as e:
            gl.cmd_wait(ns(**args))
        return e.exception.code

    def test_wait_until_stops_on_terminal_state(self):
        self.assertEqual(self.wait([{"status": "failed"}], until="running"), 1)
        self.assertEqual(self.wait([{"status": "pending"}, {"status": "running"}], until="running"), 0)

    def test_wait_exit_codes(self):
        self.assertEqual(self.wait([{"status": "running"}, {"status": "success"}]), 0)
        self.assertEqual(self.wait([{"status": "failed"}]), 1)

    def test_wait_bad_id_fails_fast(self):
        code = self.wait([gl.ApiError("GET projects/:id/jobs/5: 404 Not Found")])
        self.assertIn("404", str(code))

    def test_wait_mr_resolves_once(self):
        with mock.patch.object(gl, "resolve_mr", return_value=7) as r:
            code = self.wait([{"iid": 7, "state": "opened"}, {"iid": 7, "state": "merged"}],
                             kind="mr", id=None, until="merged")
        self.assertEqual(code, 0)
        r.assert_called_once()

    def test_partial_log_is_not_cached_as_final(self):
        with mock.patch.dict(os.environ), mock.patch.object(gl, "CACHE", self.tmpdir()):
            job = {"id": 5, "status": "running", "web_url": "https://h/g/p/-/jobs/5"}
            with mock.patch.object(gl, "glab", return_value="partial"):
                _, path = gl.job_log(job)
            self.assertTrue(path.endswith("5.partial.log"))
            job["status"] = "failed"
            with mock.patch.object(gl, "glab", return_value="partial\nError: boom"):
                lines, path = gl.job_log(job)
            self.assertIn("Error: boom", lines)
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

    def tmpdir(self):
        import tempfile
        d = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, d)
        return d

    def test_explicit_ref_does_not_fall_back(self):
        with mock.patch.object(gl, "glab", return_value=[]), self.assertRaises(SystemExit) as e:
            gl.resolve_pipe(ref="release")
        self.assertIn("no pipelines for ref release", str(e.exception.code))

    def test_diff_stat_counts_dash_lines(self):
        self.assertEqual(gl.diff_stat({"diff": "@@ -1 +1 @@\n----\n++x\n", "new_path": "f"})[:2], (1, 1))

    def test_tf_forget_and_import(self):
        res, _, _ = gl.tf_parse(["  # aws_s3_bucket.a will no longer be managed by Terraform",
                                 "  # aws_s3_bucket.b will be imported",
                                 "  # aws_s3_bucket.c will be removed from the OpenTofu state but will not be destroyed"])
        self.assertEqual([(s, n) for _, s, n in res],
                         [("forget", "aws_s3_bucket.a"), ("import", "aws_s3_bucket.b"), ("forget", "aws_s3_bucket.c")])

    def test_mrs_everywhere_uses_scope_all(self):
        with mock.patch.object(gl, "glab", return_value=[]) as g:
            gl.cmd_mrs(ns(state="opened", limit=5, mine=False, review="bob", source=None, search=None,
                          everywhere=True))
        self.assertIn("scope=all", g.call_args[0][0])

    def test_on_head_merge_ref_checks_parent(self):
        m = {"sha": "head"}
        hp = {"sha": "merge", "ref": "refs/merge-requests/1/merge"}
        with mock.patch.object(gl, "glab", return_value={"parent_ids": ["base", "head"]}):
            self.assertTrue(gl.on_head(hp, m))
        with mock.patch.object(gl, "glab", return_value={"parent_ids": ["base", "old"]}):
            self.assertFalse(gl.on_head(hp, m))
        self.assertFalse(gl.on_head({"sha": "old", "ref": "refs/merge-requests/1/head"}, m))

    def test_diff_accepts_path_without_iid(self):
        a = ns(iid="src/app.py", paths=[], max=10)
        with mock.patch.object(gl, "resolve_mr", return_value=3), \
                mock.patch.object(gl, "get_diffs", return_value=[{"old_path": "src/app.py", "new_path": "src/app.py",
                                                                  "diff": "+x"}]), \
                redirect_stdout(io.StringIO()):
            gl.cmd_diff(a)
        self.assertEqual(a.paths, ["src/app.py"])


def by_path(routes):
    """A glab() stand-in that answers by the first route key found in the path."""
    def fake(path, *x, **k):
        for key, val in routes.items():
            if key in path:
                if isinstance(val, Exception):
                    raise val
                return val
        raise gl.ApiError(f"GET {path}: 404 Not Found")
    return fake


class SecondReview(unittest.TestCase):
    """One test per item in the second review."""

    MR = {"iid": 7, "title": "t", "state": "opened", "source_branch": "f", "target_branch": "main",
          "author": {"username": "dev"}, "updated_at": "2026-01-01T00:00", "web_url": "u", "sha": "s",
          "detailed_merge_status": "mergeable"}

    def capture(self, fn, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            fn(*args)
        return out.getvalue()

    def test_mr_failed_discussions_are_not_zero_threads(self):
        routes = {"/discussions": gl.ApiError("boom"), "/approvals": gl.ApiError("boom"),
                  "/diffs": gl.ApiError("boom"), "/changes": gl.ApiError("boom"),
                  "merge_requests/7": self.MR}
        with mock.patch.object(gl, "glab", side_effect=by_path(routes)):
            out = self.capture(gl.cmd_mr, ns(iid="7", files=15, desc=0))
        self.assertIn("threads: unavailable", out)
        self.assertIn("approvals: unavailable", out)
        self.assertIn("files: unavailable", out)
        self.assertNotIn("0 unresolved", out)
        self.assertNotIn("ready to merge", out)

    def test_collapsed_matrix_keeps_error_lines(self):
        jobs = [{"id": i, "name": f"test: [{c}]", "stage": "test", "status": "failed"}
                for i, c in enumerate("abcd", 1)]
        jobs.append({"id": 9, "name": "lint", "stage": "test", "status": "failed"})
        routes = {"/jobs": jobs, "/bridges": [], "pipelines/1": {
            "id": 1, "status": "failed", "ref": "f", "sha": "abcdef12", "web_url": "u"}}
        with mock.patch.object(gl, "glab", side_effect=by_path(routes)), \
                mock.patch.object(gl, "error_excerpt", side_effect=lambda j: ([f"boom {j['id']}"], "x.log")):
            out = self.capture(gl.pipe_summary, 1)
        self.assertIn("test/test ×4", out)
        self.assertIn("| boom 9", out)  # one excerpt per group before a second from the same group
        self.assertIn("| boom 1", out)
        self.assertIn("| boom 2", out)
        self.assertNotIn("boom 3", out)

    def wait(self, responses, **kw):
        return Regressions.wait(self, responses, **kw)

    def test_wait_mr_without_pipeline_gives_up(self):
        mr = {"iid": 7, "state": "opened", "sha": "s"}
        with mock.patch.object(gl, "resolve_mr", return_value=7), mock.patch.object(gl, "NO_PIPE_GRACE", 0):
            self.assertEqual(self.wait([mr], kind="mr", id=None), 1)
        late = dict(mr, head_pipeline={"id": 3, "status": "success", "sha": "s"})
        with mock.patch.object(gl, "resolve_mr", return_value=7):
            self.assertEqual(self.wait([mr, late], kind="mr", id=None), 0)

    def test_wait_manual_and_blocked_agree(self):
        self.assertEqual(self.wait([{"status": "manual"}], kind="pipe"), 0)
        self.assertEqual(self.wait([{"status": "blocked"}], kind="pipe"), 0)

    def test_stderr_is_sanitized(self):
        err, out = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "argv", ["glab-lean", "pipes"]), \
                mock.patch.object(sys, "stderr", err), mock.patch.object(sys, "stdout", out), \
                mock.patch.object(gl, "cmd_pipes", side_effect=lambda a: print("a\x1b]52;c;x\x07b", file=sys.stderr)):
            gl.main()
        self.assertEqual(err.getvalue(), "ab\n")

    def test_threads_general_alone_lists_general_comments(self):
        note = {"author": {"username": "u"}, "created_at": "2026-01-01", "body": "hi"}
        discs = [{"id": "a1", "notes": [dict(note)]},
                 {"id": "b2", "notes": [dict(note, resolvable=True, resolved=False)]}]
        with mock.patch.object(gl, "glab", return_value=discs):
            out = self.capture(gl.cmd_threads, ns(iid="7", all=False, general=True, full=False))
        self.assertIn("!7: 1 general comments", out)
        self.assertIn("[a1] general", out)

    def test_client_errors_are_not_retried(self):
        calls = []

        def run(cmd, input=None, capture_output=None):
            calls.append(cmd)
            return types.SimpleNamespace(returncode=1, stdout=b"", stderr=stderr)
        for stderr, n in ((b"glab: 404 Not Found (HTTP 404)", 1), (b"glab: HTTP 429", 2),
                          (b"glab: 502 Bad Gateway (HTTP 502)", 2)):
            calls.clear()
            with mock.patch.object(gl.subprocess, "run", run), mock.patch.object(gl.time, "sleep"), \
                    self.assertRaises(gl.ApiError):
                gl.glab("projects/:id/jobs/1")
            self.assertEqual(len(calls), n, stderr)

    def test_merge_auto_sends_both_fields(self):
        with mock.patch.object(gl, "glab", return_value={"state": "opened"}) as g:
            self.capture(gl.cmd_merge, ns(iid="7", squash=False, rm_branch=False, auto=True))
        body = g.call_args[0][2]
        self.assertTrue(body["auto_merge"] and body["merge_when_pipeline_succeeds"])

    def test_cache_drops_partial_and_old_logs(self):
        d = Regressions.tmpdir(self)
        with mock.patch.object(gl, "CACHE", d):
            os.makedirs(os.path.join(d, "h_g_p"))
            old, mine = os.path.join(d, "h_g_p", "4.log"), os.path.join(d, "h_g_p", "server.log")
            for f in (old, mine):
                open(f, "w").close()
                os.utime(f, (0, 0))
            job = {"id": 5, "status": "running", "web_url": "https://h/g/p/-/jobs/5"}
            with mock.patch.object(gl, "glab", return_value="partial"):
                _, partial = gl.job_log(job)
            self.assertFalse(os.path.exists(old))
            self.assertTrue(os.path.exists(mine))  # not a glab-lean file: never pruned
            job["status"] = "success"
            with mock.patch.object(gl, "glab", return_value="done"):
                gl.job_log(job)
            self.assertFalse(os.path.exists(partial))

    def test_pipe_prefers_mr_head_pipeline(self):
        routes = {"merge_requests?": [{"iid": 7}], "merge_requests/7": {"head_pipeline": {"id": 2}},
                  "pipelines?": [{"id": 1}]}
        with mock.patch.object(gl, "branch", return_value="f"), \
                mock.patch.object(gl, "glab", side_effect=by_path(routes)):
            self.assertEqual(gl.resolve_pipe(), (2, ":id"))
        routes["merge_requests?"] = []
        with mock.patch.object(gl, "branch", return_value="f"), \
                mock.patch.object(gl, "glab", side_effect=by_path(routes)):
            self.assertEqual(gl.resolve_pipe(), (1, ":id"))

    def test_repo_goes_in_the_path_not_to_glab_r(self):
        run, calls = fake_run([{"id": 1}])
        with mock.patch.object(gl, "REPO", "gitlab.com/g/p"), mock.patch.object(gl.subprocess, "run", run):
            gl.glab("projects/:id/jobs/1")
            with self.assertRaises(gl.ApiError):
                gl.glab("projects/:fullpath/jobs/1")
        self.assertNotIn("-R", calls[0])
        self.assertEqual(calls[0][-1], "projects/gitlab.com%2Fg%2Fp/jobs/1")
        self.assertEqual(len(calls), 1)


class JobCommand(unittest.TestCase):
    JOB = {"id": 5, "name": "test", "stage": "test", "status": "failed", "pipeline": {"id": 1},
           "ref": "main", "web_url": "https://h/g/p/-/jobs/5"}
    LINES = ["$ make", "step one", "Error: first", "more"] + [f"line {i}" for i in range(30)]

    def run_job(self, **kw):
        args = dict(id=5, sections=False, section=None, full=False, grep=None, tail=None, head=None,
                    max=30, context=0)
        args.update(kw)
        out = io.StringIO()
        with mock.patch.object(gl, "job_meta", return_value=self.JOB), \
                mock.patch.object(gl, "job_log", return_value=(self.LINES, "/tmp/5.log")), redirect_stdout(out):
            gl.cmd_job(ns(**args))
        return out.getvalue()

    def test_grep_prints_numbered_matches_with_context(self):
        out = self.run_job(grep="Error", context=1)
        self.assertIn("2: step one\n3: Error: first\n4: more", out)

    def test_default_shows_error_lines_of_failed_job(self):
        out = self.run_job()
        self.assertIn("-- error-like lines (1) --\n3: Error: first", out)


class FullReview(unittest.TestCase):
    """One test per finding in the full-file review."""

    def capture(self, fn, *args, **kw):
        out = io.StringIO()
        with redirect_stdout(out):
            fn(*args, **kw)
        return out.getvalue()

    def tmpdir(self):
        return Regressions.tmpdir(self)

    PIPE = {"id": 1, "status": "failed", "ref": "f", "sha": "abcdef12", "web_url": "u"}

    def job(self, i, name="unit", status="failed", **kw):
        return dict({"id": i, "name": name, "stage": "test", "status": status,
                     "web_url": f"https://h/g/p/-/jobs/{i}"}, **kw)

    # 1: cache I/O errors
    def test_unwritable_cache_still_shows_pipe(self):
        d = self.tmpdir()
        os.chmod(d, 0o500)
        self.addCleanup(os.chmod, d, 0o700)
        routes = {"/jobs/9/trace": "Error: boom\nmore", "/jobs": [self.job(9)], "/bridges": [],
                  "pipelines/1": self.PIPE}
        with mock.patch.object(gl, "CACHE", os.path.join(d, "cache")), \
                mock.patch.object(gl, "glab", side_effect=by_path(routes)):
            out = self.capture(gl.pipe_summary, 1)
            lines, path = gl.job_log(self.job(9))
        self.assertIn("9  test/unit  failed", out)
        self.assertIn("| Error: boom", out)
        self.assertIsNone(path)
        self.assertEqual(lines, ["Error: boom", "more"])

    # 2: section markers and the runner's trailing sections
    def test_sections_and_body_end(self):
        lines = ["== get_sources", "fetch", "== step_script", "$ make", "== docker_cleanup", "removed",
                 "== cleanup: removing stale fixtures", "== 2026 CreateUsers: migrating",
                 "FAILED tests/test_api.py::test_login", "== upload_artifacts_on_failure",
                 "ERROR: No files to upload", "== cleanup_file_variables", "ERROR: Job failed: exit code 1"]
        end = gl.body_end(lines)
        self.assertEqual(lines[end], "== upload_artifacts_on_failure")
        self.assertIn(8, gl.error_lines(lines, end))
        step = [s for s in gl.sections(lines) if s[0] == "step_script"][0]
        self.assertEqual(lines[step[2]], "== upload_artifacts_on_failure")  # nested section doesn't end it
        self.assertEqual([s[0] for s in gl.sections(lines)].count("cleanup:"), 0)
        upload = lines[:9] + ["== upload_artifacts_on_success",
                              "ERROR: Uploading artifacts as \"archive\" to coordinator... too large archive"]
        self.assertEqual(gl.body_end(upload), len(upload))

    # 3: wait mr after a push
    def wait_mr(self, responses, local="new", pushed=True, base=None, grace=100):
        """Run `wait mr` against MR responses served in order. GitLab knows the local HEAD when
        `pushed`, and reports `base` (default: the local HEAD) as the merge base."""
        mrs = iter(responses)

        def fake(path, *x, **k):
            if "/repository/commits/" in path:
                if not pushed:
                    raise gl.ApiError(f"GET {path}: 404 Commit Not Found")
                return {"id": local}
            if "/repository/merge_base" in path:
                return {"id": base or local}
            return next(mrs)
        with mock.patch.object(gl, "resolve_mr", return_value=7), mock.patch.object(gl, "NO_PIPE_GRACE", grace), \
                mock.patch.object(gl, "git", return_value=local), mock.patch.object(gl, "current_branch", return_value="f"), \
                mock.patch.object(gl, "glab", side_effect=fake), mock.patch.object(gl, "pipe_summary"), \
                mock.patch.object(gl.time, "sleep"), redirect_stdout(io.StringIO()), \
                self.assertRaises(SystemExit) as e:
            gl.cmd_wait(ns(kind="mr", id=None, until=None, every=0, timeout=60))
        return e.exception.code

    def mr(self, sha, hp=None, state="opened"):
        return {"iid": 7, "state": state, "sha": sha, "source_branch": "f", "head_pipeline": hp}

    def test_wait_mr_ignores_pipeline_from_before_push(self):
        old = self.mr("old", {"id": 1, "status": "success", "sha": "old"})
        new = self.mr("new", {"id": 2, "status": "failed", "sha": "new"})
        self.assertEqual(self.wait_mr([old, new], base="old"), 1)  # the old success isn't the answer

    def test_wait_mr_unpushed_fails_fast_even_without_upstream(self):
        old = self.mr("old", {"id": 1, "status": "success", "sha": "old"})
        self.assertEqual(self.wait_mr([old], pushed=False), 1)

    def test_wait_mr_accepts_branch_ahead_of_local(self):
        ahead = self.mr("bot", {"id": 3, "status": "success", "sha": "bot"})  # a bot pushed on top
        self.assertEqual(self.wait_mr([ahead]), 0)

    def test_wait_mr_stale_pipeline_gives_up_and_merged_stops(self):
        stale = self.mr("new", {"id": 1, "status": "success", "sha": "old", "ref": "f"})
        self.assertEqual(self.wait_mr([stale], grace=0), 1)
        self.assertEqual(self.wait_mr([self.mr("x", state="merged")]), 0)
        self.assertEqual(self.wait_mr([self.mr("x", state="closed")]), 1)

    # 4 and 14: no-ID pipeline resolution
    def test_resolve_pipe_surfaces_api_errors(self):
        with mock.patch.object(gl, "branch", return_value="f"), \
                mock.patch.object(gl, "glab", side_effect=gl.ApiError("GET x: 401 Unauthorized")), \
                self.assertRaises(gl.ApiError):
            gl.resolve_pipe()

    def test_fork_mrs_are_skipped_and_fork_pipelines_use_their_project(self):
        routes = {"merge_requests?": [{"iid": 8, "project_id": 1, "source_project_id": 99}],
                  "pipelines?": [{"id": 100}]}
        with mock.patch.object(gl, "branch", return_value="main"), \
                mock.patch.object(gl, "glab", side_effect=by_path(routes)):
            self.assertEqual(gl.resolve_pipe(), (100, ":id"))
        self.assertEqual(gl.mr_pipeline({"project_id": 1, "head_pipeline": {"id": 500, "project_id": 99}}), (500, "99"))
        self.assertEqual(gl.mr_pipeline({"project_id": 1, "head_pipeline": {"id": 500, "project_id": 1}}), (500, ":id"))
        with mock.patch.object(gl, "CACHE", self.tmpdir()), mock.patch.object(gl, "glab", return_value="x") as g:
            gl.job_log(self.job(3, pipeline={"id": 500, "project_id": 99}))
        self.assertEqual(g.call_args[0][0], "projects/99/jobs/3/trace")

    # 5: error-line filters
    def test_error_filters(self):
        real = ["===== 10 failed, 45 passed =====", "Tests: 20 failed",
                "FAILED tests/test_api.py::test_error_handling - AssertionError",
                "PHP Fatal error: oops in /var/www/app/error_handler.php", "npm ERR! code 1 failed"]
        noise = ["# fail 0", " 0 fail", "0 failed, 3 passed", "failed=0", "Errors: 0", "export ERROR_LEVEL=2",
                 "curl --fail https://x"]
        self.assertEqual(gl.error_lines(real + noise, len(real + noise)), list(range(len(real))))

    # 6: the first error survives teardown noise
    def test_excerpt_keeps_root_cause(self):
        lines = ["== step_script", "src/a.c:10:5: error: 'foo' undeclared", "FAILED test_login",
                 "FAILED test_logout", "2 failed, 5 passed"] + [f"Container c{i} Removed" for i in range(8)] + \
                ["== after_script", "Running after_script", "Network n Removed"]
        with mock.patch.object(gl, "job_log", return_value=(lines, "/tmp/x.log")):
            out, _ = gl.error_excerpt(self.job(1), n=6)
        self.assertEqual(out[0], "src/a.c:10:5: error: 'foo' undeclared")
        self.assertNotIn("Network n Removed", out)

    def test_excerpt_skips_box_drawing_lines(self):
        lines = ["== step_script", "│ Error: Invalid index", "│", "│ on main.tf line 3", "│", "╵"]
        with mock.patch.object(gl, "job_log", return_value=(lines, "/tmp/x.log")):
            out, _ = gl.error_excerpt(self.job(1))
        self.assertFalse([l for l in out if "×" in l])

    # 7: api
    def api(self, data, **kw):
        args = dict(path="x", paginate=False, fields=None, where=None, limit=50)
        args.update(kw)
        with mock.patch.object(gl, "glab", return_value=data):
            return self.capture(gl.cmd_api, ns(**args))

    def test_api_where_matches_json_literals(self):
        rows = [{"id": 1, "allow_failure": False}, {"id": 2, "allow_failure": True}]
        self.assertEqual(self.api(rows, fields="id", where=["allow_failure=false"]), "1\n")
        self.assertEqual(self.api(rows, fields="id", where=["allow_failure!=true"]), "1\n")
        self.assertEqual(self.api({"status": "success"}, where=["status=failed"]), "")
        self.assertIn("approvals_left: 0", self.api({"approvals_left": 0}))
        self.assertEqual(self.api([{"key": "A", "value": "1"}]), "key\tvalue\nA\t1\n")

    def test_non_json_response_is_an_api_error(self):
        def run(cmd, input=None, capture_output=None):
            return types.SimpleNamespace(returncode=0, stdout=b"<html>login</html>", stderr=b"")
        with mock.patch.object(gl.subprocess, "run", run), self.assertRaises(gl.ApiError):
            gl.glab("projects/:id/jobs/1")

    # 8: cache root permissions
    def test_existing_cache_root_keeps_its_mode(self):
        d = self.tmpdir()
        os.chmod(d, 0o755)
        with mock.patch.object(gl, "CACHE", d), mock.patch.object(gl, "glab", return_value="x"):
            gl.job_log(self.job(3, status="success"))
        self.assertEqual(os.stat(d).st_mode & 0o777, 0o755)

    # 9: API errors aren't "not found", and wait keeps its exit code
    def test_pipe_api_error_is_reported_and_wait_keeps_exit_code(self):
        with mock.patch.object(gl, "glab", side_effect=gl.ApiError("GET x: 401 Unauthorized")), \
                self.assertRaises(gl.ApiError):
            gl.pipe_summary(1)
        with mock.patch.object(gl, "pipe_summary", side_effect=gl.ApiError("boom")), \
                mock.patch.object(gl, "glab", return_value={"status": "failed"}), \
                mock.patch.object(sys, "stderr", io.StringIO()), self.assertRaises(SystemExit) as e:
            gl.cmd_wait(ns(kind="pipe", id="1", until="failed", every=0, timeout=60))
        self.assertEqual(e.exception.code, 0)

    # 10: bare --mr
    def test_bare_mr_flag_parses(self):
        for argv, fn in ((["pipe", "--mr"], "cmd_pipe"), (["tf", "--mr"], "cmd_tf")):
            with mock.patch.object(sys, "argv", ["glab-lean", *argv]), mock.patch.object(gl, fn) as f, \
                    mock.patch.object(sys, "stdout", io.StringIO()), mock.patch.object(sys, "stderr", io.StringIO()):
                gl.main()
            self.assertEqual(f.call_args[0][0].mr, "")

    # 11: omitted diffs and unmatched paths
    def test_diff_too_large_and_no_match(self):
        big = {"old_path": "package-lock.json", "new_path": "package-lock.json", "diff": "", "too_large": True}
        self.assertEqual(gl.stat_line(*gl.diff_stat(big)), "+? -?  package-lock.json (diff too large to show)")
        a = ns(iid="7", paths=["./src/app.py"], max=10)
        with mock.patch.object(gl, "get_diffs", return_value=[big]), self.assertRaises(SystemExit) as e:
            gl.cmd_diff(a)
        self.assertIn("no file matching src/app.py", str(e.exception.code))

    # 12: system notes inside a thread
    def test_threads_skip_system_notes(self):
        note = {"author": {"username": "rev"}, "created_at": "2026-01-01", "body": "fix this",
                "resolvable": True, "resolved": False}
        discs = [{"id": "a1", "notes": [note, dict(note, body="Still not fixed"),
                                        dict(note, body="changed this line in version 3", system=True)]}]
        with mock.patch.object(gl, "glab", return_value=discs):
            out = self.capture(gl.cmd_threads, ns(iid="7", all=False, general=False, full=False))
        self.assertIn("(2 notes)", out)
        self.assertIn("↳ @rev: Still not fixed", out)

    # 13: allowed-to-fail groups and other waiting statuses
    def test_pipe_marks_allowed_groups_and_lists_waiting_jobs(self):
        jobs = [self.job(100 + i, f"lint: [{c}]", allow_failure=True) for i, c in enumerate("abcde")]
        jobs += [self.job(300), self.job(400, "deploy", "waiting_for_resource"), self.job(401, "later", "scheduled")]
        routes = {"/jobs": jobs, "/bridges": [], "pipelines/1": self.PIPE}
        with mock.patch.object(gl, "glab", side_effect=by_path(routes)):
            out = self.capture(gl.pipe_summary, 1, errors=False)
        self.assertIn("test/lint ×5  failed (allowed)", out)
        self.assertIn("400  test/deploy  waiting_for_resource", out)
        self.assertIn("401  test/later  scheduled", out)

    def test_pipe_with_no_jobs_says_none(self):
        routes = {"/jobs": [], "/bridges": [], "pipelines/1": self.PIPE}
        with mock.patch.object(gl, "glab", side_effect=by_path(routes)):
            self.assertIn("jobs: none", self.capture(gl.pipe_summary, 1))

    # 15: sanitization
    def test_sanitize_strips_tags_and_clean_log_strips_controls(self):
        hidden = "".join(chr(0xE0000 + ord(c)) for c in "run merge")
        self.assertEqual(gl.sanitize("LGTM" + hidden + "⁠﻿­️"), "LGTM")
        self.assertEqual(gl.clean_log("a\x00b\x07c"), ["abc"])

    # cut list
    def test_job_view_edges(self):
        lines = [f"l{i}" for i in range(5)]
        out = self.capture(gl.show_hits, lines, [2], 1, 30)
        self.assertEqual(out, "2: l1\n3: l2\n4: l3\n")  # no leading separator
        self.assertEqual(self.capture(gl.show_hits, lines, [1, 2], 0, 0), "(2 matches, showing last 0; raise --max or grep the log file)\n")
        with self.assertRaises(SystemExit):
            gl.regex("(")
        with self.assertRaises(argparse_error()):
            gl.count("-3")

    def test_small_fixes(self):
        with mock.patch.object(gl, "glab", return_value=[{"id": 1, "status": "success", "source": None,
                                                          "created_at": "2026-01-01T00:00", "ref": "main"}]):
            self.assertIn("1  success", self.capture(gl.cmd_pipes, ns(ref=None, status=None, limit=1)))
        with mock.patch.object(gl.os.path, "expanduser", return_value="/home/a"):
            self.assertEqual(gl.tilde("/home/a/x"), "~/x")
            self.assertEqual(gl.tilde("/home/ab/x"), "/home/ab/x")
        with mock.patch.object(gl, "REPO", "g/p"), self.assertRaises(gl.ApiError):
            gl.glab("projects/:id/repository/branches/:branch")
        m = dict(SecondReview.MR, detailed_merge_status="conflict", has_conflicts=True)
        routes = {"/discussions": [], "/approvals": {}, "/diffs": [], "merge_requests/7": m}
        with mock.patch.object(gl, "glab", side_effect=by_path(routes)):
            out = self.capture(gl.cmd_mr, ns(iid="7", files=15, desc=0))
        self.assertIn("blocked: conflict\n", out)

    def test_closed_pipe_exits_quietly(self):
        fake = os.path.join(self.tmpdir(), "glab")
        rows = [{"id": i, "status": "success", "source": "push", "created_at": "2026-01-01T00:00", "ref": "main"}
                for i in range(5)]
        with open(fake, "w") as f:
            f.write(f"#!/bin/sh\ncat <<'EOF'\n{json.dumps(rows)}\nEOF\n")
        os.chmod(fake, 0o755)
        env = dict(os.environ, PATH=os.path.dirname(fake) + os.pathsep + os.environ["PATH"])
        # Close the reading end first: the output fits the buffer, so the write fails at the last flush.
        p = subprocess.Popen([sys.executable, PATH, "pipes", "--limit", "5"], stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, env=env)
        p.stdout.close()
        err = p.stderr.read()
        p.wait()
        self.assertEqual(p.returncode, 0)
        self.assertNotIn(b"BrokenPipeError", err)


class FixReview(unittest.TestCase):
    """One test per finding in the review of the full-review fixes."""

    job = FullReview.job

    def test_excerpt_shows_trailer_errors_in_log_order(self):
        lines = ["== step_script", "build ok", "tests ok", "== upload_artifacts_on_failure",
                 "ERROR: Uploading artifacts as \"archive\" to coordinator... 413 Request Entity Too Large",
                 "== cleanup_file_variables", "ERROR: Job failed: exit code 1"]
        with mock.patch.object(gl, "job_log", return_value=(lines, "/tmp/x.log")):
            out, _ = gl.error_excerpt(self.job(1))
        self.assertTrue(out[-1].endswith("413 Request Entity Too Large"))
        self.assertNotIn("ERROR: Job failed: exit code 1", out)
        lines = ["== step_script", "Error: first"] + [f"step {i} done" for i in range(20)] + ["Error: last"]
        with mock.patch.object(gl, "job_log", return_value=(lines, "/tmp/x.log")):
            out, _ = gl.error_excerpt(self.job(1), n=4)
        self.assertEqual(out, sorted(out, key=lines.index))

    def test_cache_root_created_by_another_thread(self):
        d = os.path.join(Regressions.tmpdir(self), "new")
        real = os.makedirs

        def racing(name, *a, **k):  # another thread creates the directory first
            if name == d and not os.path.isdir(d):
                real(d)
            return real(name, *a, **k)
        with mock.patch.object(gl, "CACHE", d), mock.patch.object(gl, "glab", return_value="log"), \
                mock.patch.object(gl.os, "makedirs", racing):
            _, path = gl.job_log(self.job(1, status="success"))
        self.assertIsNotNone(path)

    def test_failed_branch_pipelines_lookup_falls_back_to_mr(self):
        routes = {"merge_requests?": [{"iid": 7}], "merge_requests/7": {"head_pipeline": {"id": 2}},
                  "pipelines?": gl.ApiError("GET pipelines: 403 Forbidden")}
        with mock.patch.object(gl, "branch", return_value="f"), \
                mock.patch.object(gl, "glab", side_effect=by_path(routes)):
            self.assertEqual(gl.resolve_pipe(), (2, ":id"))

    def test_fork_pipeline_names_its_project(self):
        pipe = dict(FullReview.PIPE, web_url="https://h/forker/proj/-/pipelines/1")
        routes = {"/jobs": [], "/bridges": [], "pipelines/1": pipe}
        out = io.StringIO()
        with mock.patch.object(gl, "glab", side_effect=by_path(routes)), redirect_stdout(out):
            gl.pipe_summary(1, proj="99")
        self.assertIn("pass -R forker/proj", out.getvalue())

    def test_paths_resolve_from_the_current_directory_first(self):
        git = {("rev-parse", "--show-prefix"): "src/app/"}
        with mock.patch.object(gl, "git", side_effect=lambda *a: git.get(a)), mock.patch.object(gl, "REPO", None):
            self.assertEqual(gl.path_candidates("./main.py"), ["src/app/main.py", "main.py"])
            self.assertEqual(gl.path_candidates("../lib/util.py"), ["src/lib/util.py"])


def argparse_error():
    import argparse
    return argparse.ArgumentTypeError


class Excerpt(unittest.TestCase):
    def test_repeated_lines_collapse_and_runner_noise_is_skipped(self):
        lines = ["== get_sources", "Fetching changes with git depth set to 20...", "== step_script",
                 'Executing "step_script" stage of the job script', "Using docker image sha256:abc"]
        for loc in ("de", "fr", "ja"):
            lines += [f"{loc} missing app.title", f"{loc} missing app.body"]
        lines += ["check: FAIL — 6 findings", "== upload_artifacts_on_failure", "Uploading artifacts"]
        job = {"id": 9, "status": "failed", "web_url": "https://h/g/p/-/jobs/9"}
        with mock.patch.object(gl, "job_log", return_value=(lines, "/tmp/x/9.log")):
            out, path = gl.error_excerpt(job)
        self.assertEqual(out, ["ja missing app.title  (×3)", "ja missing app.body  (×3)", "check: FAIL — 6 findings"])
        self.assertEqual(path, "/tmp/x/9.log")


class Cli(unittest.TestCase):
    def test_repo_flag_after_subcommand(self):
        with mock.patch.object(sys, "argv", ["glab-lean", "pipes", "-R", "g/p", "--limit", "1"]), \
                mock.patch.object(gl, "cmd_pipes") as fn:
            gl.main()
        self.assertEqual(gl.REPO, "g/p")
        fn.assert_called_once()

    def test_skill_frontmatter_values_are_valid_yaml(self):
        # A plain YAML scalar can't contain ": ", so strict skill loaders reject such a value.
        with open(os.path.join(HERE, "..", "skills", "glab-lean", "SKILL.md")) as f:
            front = f.read().split("---")[1]
        for line in front.strip().splitlines():
            key, _, value = line.partition(": ")
            self.assertFalse(": " in value and not value.startswith('"'), key)

    def test_help_runs(self):
        p = subprocess.run([sys.executable, PATH, "--help"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0)
        self.assertIn("tf", p.stdout)


if __name__ == "__main__":
    unittest.main()
