# glab-lean

`glab-lean` is a GitLab CLI for AI coding agents. It prints short, fixed-shape summaries of merge
requests, pipelines, job logs, and Terraform plans, so an agent spends far fewer tokens reading
GitLab state. It ships with an [Agent Skill](https://docs.claude.com/en/docs/claude-code/skills)
that tells Claude Code (or any compatible agent) to use it.

It wraps [`glab`](https://gitlab.com/gitlab-org/cli), so it uses your existing `glab` login and
works with gitlab.com and self-managed instances.

## Why

Agents that use `glab api` or a GitLab MCP server tend to do three expensive things:

- Read raw JSON and full job logs, then trim them with `head`.
- Write the same `jq` or Python filter snippets in every session.
- Poll CI with `sleep` loops, spending a turn on every check.

Typical output sizes, measured on real projects:

| Task | Raw | `glab-lean` |
|---|---|---|
| Jobs in a pipeline (`/pipelines/:id/jobs`) | 104,278 chars | 342 chars |
| Terraform plan job log | 118,523 chars | 9 lines |
| Failed job log | 10,274 chars | 1,785 chars |
| MR overview (`glab mr view`) | 5,018 chars | 1,710 chars |

## Requirements

- Python 3.8 or later. It uses only the standard library.
- `glab`, logged in with `glab auth login`.

## Install

```bash
git clone https://github.com/<you>/glab-lean.git
cd glab-lean
./install.sh           # symlinks into ~/.local/bin and ~/.claude/skills
./install.sh --copy    # or copy the files instead
```

To install somewhere else, set `BIN_DIR` and `SKILLS_DIR`.

### Let Claude Code run it without prompts

To let Claude Code run the read-only commands without asking, add these rules to
`permissions.allow` in `~/.claude/settings.json`:

```json
"Bash(glab-lean mr *)", "Bash(glab-lean threads *)", "Bash(glab-lean diff *)",
"Bash(glab-lean mrs *)", "Bash(glab-lean pipe *)", "Bash(glab-lean pipes *)",
"Bash(glab-lean job *)", "Bash(glab-lean tf *)", "Bash(glab-lean wait *)",
"Bash(glab-lean api *)"
```

Leave `run`, `play`, `retry`, `cancel`, `merge`, `note`, `reply`, and `resolve` off the list. They
change state in GitLab.

## Usage

Commands default to the current branch's MR or pipeline. To target another project, pass
`-R group/project`.

```console
$ glab-lean mr
!42 Add request signing
opened  feature/signing → main  by @dev  updated 2026-01-01T10:00
approvals: 1 left, approved by none
pipeline 1001 failed
threads: 2 unresolved of 5
blocked: ci_must_pass
files (3, +120 -30):
  ...

$ glab-lean pipe 1001
pipeline 1001 failed  ref feature/signing  sha 1a2b3c4d  4m30s  source push
jobs: 21 success, 1 failed, 13 manual
  5005  test/unit  failed  script_failure
  5010..5022  deploy/review ×13  manual  (--all for each)

$ glab-lean tf 5030
job 5030 plan [plan] success  4m21s  pipeline 1001  ref main
  +   aws_iam_policy.ci
  ~   module.oidc.aws_iam_role.this[0]
Plan: 1 to add, 1 to change, 0 to destroy.

$ glab-lean tf 5030 -r module.oidc.aws_iam_role.this   # only the changed lines
```

| Command | Purpose |
|---|---|
| `mr`, `threads`, `diff`, `mrs` | MR readiness, unresolved threads, diffstat or file diffs, MR lists |
| `pipe`, `pipes` | Pipeline summary with only non-green jobs; recent pipelines |
| `job` | Job status, error lines, and tail; `--grep`, `--tail`, `--section` |
| `tf` | Terraform/OpenTofu plan summary, drift, totals, and errors; `-r` for one resource |
| `wait` | Wait silently for a job, pipeline, or MR to finish, then print the summary |
| `run`, `play`, `retry`, `cancel`, `merge` | Pipeline and MR actions, one line each |
| `note`, `reply`, `resolve` | MR comments and threads |
| `api` | Any GET endpoint as TSV, with `-f` fields and `-w` filters |

Run `glab-lean <command> --help` for flags.

`wait` exits `0` on success, `1` on failure, and `124` on timeout. It's meant for background
commands.

## Job log cache

`job` and `tf` save each finished job's cleaned log under `~/.cache/glab-lean/`, so an agent can
search it without downloading it again. The logs are stored as plain text with owner-only
permissions, and they contain whatever the job printed, including any secrets it leaked. To move
the cache, set `GLAB_LEAN_CACHE`. To clear it, delete the directory.

## Development

```bash
python3 -m unittest discover -s tests
```

## License

MIT
