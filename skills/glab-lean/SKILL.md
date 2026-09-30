---
name: glab-lean
license: MIT
compatibility: Needs python3 3.8+ and the glab CLI logged in to a GitLab host.
description: Token-lean GitLab access through the `glab-lean` CLI. Use for ANY GitLab read or CI task before reaching for `glab api`, `glab mr view`, `glab ci trace`, or a GitLab MCP server — checking an MR's merge readiness, reading review threads, diffs, pipeline status, failed job logs, Terraform/OpenTofu plan output, waiting for a pipeline/job/MR to finish, running or retrying pipelines and manual jobs, merging, and replying to threads. Also use instead of writing jq/python over GitLab API JSON.
---

# glab-lean

`glab-lean` prints short, fixed-shape summaries of GitLab state. It wraps `glab api`, so auth and
host come from `glab`. IDs default to the current branch's MR or pipeline. Pass `-R group/project`
to target another repo.

## Rules

1. Use `glab-lean` first. Don't pipe `glab api` JSON through `jq` or Python; use
   `glab-lean api -f … -w …`.
2. To wait for CI, run `glab-lean wait …` as a background command (in Claude Code, the Bash tool's
   `run_in_background: true`). You get notified when it exits. Don't write `sleep`/`until` loops
   and don't poll in the foreground.
3. `glab-lean job` caches the full cleaned log and prints its path. For more detail, search that
   file. Don't fetch the trace again.
4. For Terraform or OpenTofu, run `glab-lean tf`, then `glab-lean tf JOB -r ADDR` for one resource.
   Never print a whole plan log.
5. Prefer `glab-lean` over a GitLab MCP server. MCP responses are unfiltered and can run to tens of
   thousands of characters.
6. Use `glab` for anything `glab-lean` doesn't cover: `glab mr create`, `glab ci lint`,
   `glab variable`, and writes through `glab api -X … --silent`.

## Commands

| Task | Command |
|---|---|
| MR summary: readiness, blockers, approvals, pipeline, threads, files | `glab-lean mr [IID] [--desc N] [--files N]` |
| Unresolved threads, with discussion IDs | `glab-lean threads [IID] [--all] [--full]` |
| Diffstat, or the diff for matching paths | `glab-lean diff [IID] [PATH…] [--max N]` |
| List MRs | `glab-lean mrs [--mine] [--review USER] [--source B] [--search S] [--everywhere]` |
| Pipeline: counts plus non-green jobs | `glab-lean pipe [ID] [--mr [IID]] [--ref B] [--all]` |
| Recent pipelines | `glab-lean pipes [--ref B] [--status S]` |
| Job status plus error lines and tail | `glab-lean job ID` |
| Search or slice a job log | `glab-lean job ID --grep RE [-C N]`, `--tail N`, `--sections`, `--section NAME` |
| Plan summary (`+ ~ - -/+ <=`, drift, totals, errors) | `glab-lean tf JOB`, `glab-lean tf --pipe ID`, `glab-lean tf --mr [IID]` |
| One resource's changed lines | `glab-lean tf JOB -r module.x.aws_iam_role.this [--full]` |
| Wait, then print the summary | `glab-lean wait job ID`, `glab-lean wait pipe ID`, `glab-lean wait mr [IID]` |
| Wait for a state | `glab-lean wait job ID --until running`, `glab-lean wait mr IID --until merged` |
| Run a pipeline | `glab-lean run [--ref B] [-v K=V]…` |
| Start a manual job, retry, cancel | `glab-lean play ID [-v K=V]`, `glab-lean retry ID [--pipe]`, `glab-lean cancel ID [--pipe]` |
| Merge | `glab-lean merge [IID] [--squash] [--rm-branch] [--auto]` |
| Comment, reply, resolve | `glab-lean note IID "text"`, `glab-lean reply IID DISC "text" [--resolve]`, `glab-lean resolve IID DISC` |
| Any GET endpoint as TSV | `glab-lean api 'projects/:id/jobs?scope[]=failed' -f id,name,pipeline.id -w status!=success` |

For `note` and `reply`, pass `-` as the body to read it from stdin; use that for long or
multi-line text.

`glab-lean wait` exits `0` on success, `manual`, or `merged`, `1` on failure or cancel, and `124`
on timeout (default `--timeout 3600`, `--every 20`). With `--until`, it exits `0` when the state is
reached.

`tf` finds plan jobs with `--name plan` (a regex on the job name). Use `--name 'tofu:plan'` or
`--name apply` to pick other jobs; apply jobs report `Apply complete!` and errors.

## Examples

Check whether an MR can merge, then act on the first blocker:

```bash
glab-lean mr 42          # "blocked: ci_must_pass", "pipeline 1001 failed"
glab-lean pipe 1001      # lists the failed job
glab-lean job 5005       # error lines and tail; log path for search
```

Push, then wait for CI without spending turns (as a background command):

```bash
glab-lean wait mr
```

Review a Terraform change:

```bash
glab-lean tf --mr 42
glab-lean tf 5010 -r aws_iam_role.deploy
```

Replace a jq/python snippet:

```bash
glab-lean api 'projects/:id/pipelines/1001/jobs?per_page=100' -f id,name,status -w status!=success
```

Actions such as `run`, `play`, `merge`, `note`, and `reply` change shared state. Follow the usual
confirmation rules before you use them.
