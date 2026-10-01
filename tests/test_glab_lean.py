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
                mock.patch.object(gl, "get_diffs", return_value=[]):
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
            job = {"id": 5, "status": "running", "web_url": "https://h/g/p/-/jobs/5"}
            with mock.patch.object(gl, "glab", return_value="partial"):
                _, partial = gl.job_log(job)
            old = os.path.join(os.path.dirname(partial), "4.log")
            open(old, "w").close()
            os.utime(old, (0, 0))
            job["status"] = "success"
            with mock.patch.object(gl, "glab", return_value="done"):
                gl.job_log(job)
            self.assertFalse(os.path.exists(partial))
            self.assertFalse(os.path.exists(old))

    def test_pipe_prefers_mr_head_pipeline(self):
        routes = {"merge_requests?": [{"iid": 7}], "merge_requests/7": {"head_pipeline": {"id": 2}},
                  "pipelines?": [{"id": 1}]}
        with mock.patch.object(gl, "branch", return_value="f"), \
                mock.patch.object(gl, "glab", side_effect=by_path(routes)):
            self.assertEqual(gl.resolve_pipe(), 2)
        routes["merge_requests?"] = []
        with mock.patch.object(gl, "branch", return_value="f"), \
                mock.patch.object(gl, "glab", side_effect=by_path(routes)):
            self.assertEqual(gl.resolve_pipe(), 1)

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

    def test_help_runs(self):
        p = subprocess.run([sys.executable, PATH, "--help"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0)
        self.assertIn("tf", p.stdout)


if __name__ == "__main__":
    unittest.main()
