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
