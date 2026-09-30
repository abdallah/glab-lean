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

## Supported agents

The CLI works with any agent that can run shell commands. The skill uses the open
[Agent Skills](https://agentskills.io) format, so agents that support skills load it on their own:

| Agent | User-level skills directory |
|---|---|
| Claude Code | `~/.claude/skills/` |
| Codex | `~/.agents/skills/` |
| Gemini CLI | `~/.agents/skills/` (or `~/.gemini/skills/`) |
| OpenCode | `~/.agents/skills/`, `~/.claude/skills/`, or `~/.config/opencode/skills/` |

`install.sh` installs the skill into `~/.claude/skills/` and `~/.agents/skills/`, which covers all
four. For an agent without skill support, add the snippet in
[Other agents](#other-agents) to its instructions file.

## Install

```bash
git clone https://github.com/abdallah/glab-lean.git ~/.local/share/glab-lean
~/.local/share/glab-lean/install.sh           # symlinks, so `git pull` updates it
~/.local/share/glab-lean/install.sh --copy    # or copy the files instead
```

To install somewhere else, set `BIN_DIR` and `SKILLS_DIRS` (a space-separated list).

### Setup steps for AI agents

If you're an AI agent asked to set up `glab-lean`, do the following:

1. Check the prerequisites: `python3 --version` must report 3.8 or later, and `glab auth status`
   must show a logged-in host. If `glab` isn't logged in, stop and ask the user to run
   `glab auth login`, because it's interactive.
2. Clone and install:

   ```bash
   git clone https://github.com/abdallah/glab-lean.git ~/.local/share/glab-lean
   ~/.local/share/glab-lean/install.sh
   ```

3. Confirm that `glab-lean --help` runs. If it's not found, `~/.local/bin` isn't on `PATH`; tell
   the user rather than editing their shell profile.
4. From inside a GitLab repo, run `glab-lean pipes --limit 1` to confirm API access.
5. Optional: offer to add the read-only allow rules below. Don't change the user's agent settings
   without asking.
6. The skill loads in new sessions. Tell the user to restart any running agent sessions.

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
change state in GitLab. Other agents have their own approval settings; allow the same read-only
subcommands there.

### Other agents

For an agent that doesn't support skills, add this to its instructions file, such as `AGENTS.md`
or `GEMINI.md`:

```markdown
## GitLab

Use the `glab-lean` CLI for GitLab reads and CI before `glab api` or a GitLab MCP server:
`glab-lean mr`, `threads`, `diff`, `pipe`, `job ID`, `tf JOB`, and `api PATH -f fields`.
Run `glab-lean --help` for the full list. Wait on CI with `glab-lean wait job|pipe|mr ID` as a
background command instead of sleep loops. For job logs, search the cached log file it prints
instead of fetching the trace again.
```

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
| `api` | GET a REST path as TSV, with `-f` fields and `-w` filters |

Run `glab-lean <command> --help` for flags.

`wait` exits `0` on success, `1` on failure, and `124` on timeout. It's meant for background
commands.

## Security

- **Same host only:** `glab-lean` only sends relative REST paths such as `projects/:id/…` to
  `glab api`. It refuses absolute URLs and `-R` values that aren't `group/project`, so a
  prompt-injected command can't send your token to another host.
- **Read-only commands stay read-only:** `mr`, `threads`, `diff`, `mrs`, `pipe`, `pipes`, `job`,
  `tf`, `wait`, and `api` only send GET requests.
- **Output is sanitized:** terminal escape sequences and invisible Unicode controls are stripped
  from everything it prints, including MR text and job logs.
- **Untrusted content:** MR text, comments, and job logs are written by other people. The skill
  tells agents to treat them as data, never as instructions.

## Job log cache

`job` and `tf` save each finished job's cleaned log under `~/.cache/glab-lean/` (a running job's
log goes to a separate `.partial.log` file), so an agent can
search it without downloading it again. The logs are stored as plain text with owner-only
permissions, and they contain whatever the job printed, including any secrets it leaked. To move
the cache, set `GLAB_LEAN_CACHE`. To clear it, delete the directory.

## Measuring savings

`scripts/usage-report` reads your Claude Code transcripts and compares GitLab tool calls before
and after the first `glab-lean` call: calls per session, tokens per call, polling calls, and an
estimated cost in input-token equivalents. Run it with `--snapshot` (for example, from a nightly
cron job) to keep the records after Claude Code deletes old transcripts.

```bash
~/.local/share/glab-lean/scripts/usage-report
```

## Development

```bash
python3 -m unittest discover -s tests
```

## License

MIT
