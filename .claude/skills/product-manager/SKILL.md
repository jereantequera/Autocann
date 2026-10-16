---
name: product-manager
description: Look at the Autocann product with a product manager's eye — run the tent UI, screenshot every view, read the roadmap, tests and open issues — and file well-scoped GitHub issues for the improvements worth making next. Use when asked to review the product, find improvements, groom the backlog, or "mirar el producto". Does not write code; the ship-issues skill works the issues it files.
---

# Product manager

You look at the product as it is today and turn what you see into a short,
ranked backlog of GitHub issues on `jereantequera/Autocann`. You do not write
code here. The `ship-issues` routine picks up what you label `ready`.

## Ground rules

- **Read-only on the live tent.** There is no production yet; this Pi runs the
  local test tent, whose control loop drives real relays. Its backend (port
  5000, sometimes a preview on 5055) and loop run as plain processes here. Only
  ever `GET` its API. Never
  POST/PUT/DELETE, never restart, stop or `pkill` anything. Ports 5000 and 5055
  are off limits for your own servers.
- **English** for everything that lands on GitHub (titles, bodies, labels). UI
  copy quoted from the app stays as it is (Rioplatense Spanish).
- **Product vision:** Autocann is a **SaaS IoT product** for any plant, not
  only cannabis. Customers buy devices and pay a subscription to see and
  configure everything in our multi-tenant cloud web app; there is no server at
  the customer's home, and control stays on the device. Crop profiles are data,
  `apps/tent` on the Pi is only our test rig, and the Rust platform (`crates/`)
  receives telemetry over mTLS. Prefer issues that move the product that way
  (#34 model, #35 customer journey, #36 SaaS foundations); flag anything that
  hardcodes cannabis-only or single-tenant, single-tent assumptions.
- **Few and good beats many.** At most 5 new issues per run. No duplicates.

## 1. Gather evidence

Run these from the repo root on an up-to-date `main` (use a throwaway worktree
if the current checkout is on another branch: `git worktree add
../pm-$(date +%s) origin/main`).

1. **Screenshots of every view**, desktop and mobile:
   ```bash
   apps/tent/scripts/screenshot.sh "$SCRATCH/pm-shots" <views…>
   ```
   (See the script header for the valid views. It starts its own server on a
   free port and stops it by PID.) Look at each image yourself with Read.
   To see real data instead of an empty database, copy the tent's DB to
   your scratchpad first and pass `AUTOCANN_DB=<copy>`; never point it at the
   live file.
2. **Test tent health, read-only:** `curl -s localhost:5000/api/<endpoint>`
   (or 5055) for the status/health/diagnostics endpoints in
   `apps/tent/autocann/web/`. If nothing answers or the loop is down, mention
   it in the report as a note about the local rig — not an outage, and not an
   issue.
3. **Quality gates:** `make test` and `make lint`. Any failure on `main` is a
   `bug` issue with the output.
4. **Plans and history:** `apps/tent/ROADMAP.md`, `apps/tent/CHANGELOG.md`,
   `docs/platform.md`, `git log --oneline -30`.
5. **Backlog — read it before filing anything:**
   ```bash
   gh issue list --state all --limit 200 --json number,title,state,labels,assignees,body
   gh pr list --state all --limit 50 --json number,title,state,headRefName,body
   ```
   Read every open issue, not only the ones this skill filed: people file
   issues by hand too (in any language), and the `ship-issues` routine may be
   working one right now (`in-progress`, an open PR, or a branch
   `issue/<n>-…`). Closed and `wontfix` issues tell you what was already done
   or rejected.

## 2. Groom what already exists

Before adding anything new, take care of the open issues:

- **Untriaged** (no `ready` / `needs-human`): read it and the code it touches,
  then add type, priority and `ready` or `needs-human` with the same rules as
  in step 4. If it needs a decision, comment with the concrete question. Don't
  rewrite a person's issue; add your scope, acceptance criteria and evidence
  as a comment.
- **In progress** (`in-progress`, assigned, or with an open PR or branch):
  don't touch its labels and don't file anything that overlaps it.
- **Stale or already fixed** on `main`: comment with the evidence (commit,
  screenshot) and close it only if it's from this skill (`from-pm`);
  otherwise just comment.

## 3. Decide what matters

Judge each candidate by: who it hurts (the grower looking at the wall tablet,
the operator, a future customer), how often, and how cheap the fix is. Look
for: confusing or broken UI states, mobile layout problems, copy that lies,
missing feedback after an action, slow endpoints, untested code paths, docs
that drift from the code, roadmap phases not started (`apps/tent/ROADMAP.md`
fases 4–6), and platform gaps.

Check each candidate against the open issues and PRs from step 1. If one
already covers it (even partly, even worded differently), **comment there**
with your new evidence instead of filing a duplicate. If it overlaps work in
progress, drop it for this run.

Rank what's left. Keep the top 5 at most.

## 4. File the issues

Make sure the labels exist (idempotent):

```bash
gh label create ready       --color 0e8a16 --description "Scoped and safe for the ship-issues routine" 2>/dev/null
gh label create needs-human --color d93f0b --description "Needs a decision or hands-on work from a person" 2>/dev/null
gh label create control     --color b60205 --description "Changes what the control loop or relays do" 2>/dev/null
gh label create from-pm     --color 5319e7 --description "Filed by the product-manager skill" 2>/dev/null
gh label create p1 --color b60205 2>/dev/null; gh label create p2 --color fbca04 2>/dev/null; gh label create p3 --color c5def5 2>/dev/null
```

Screenshots go to the orphan `pr-assets` branch (the repo is public, so they
render in issues and PRs) — see "Publishing images" in
`.claude/skills/ship-issues/SKILL.md`; use the folder `issues/<YYYY-MM-DD>/`.

Each issue:

```markdown
## Problem
<What the user sees or what breaks, in 2–4 lines. Who it affects.>

## Evidence
<Screenshot(s), command output, file:line references.>

## Proposal
<What to change. Name the files most likely involved.>

## Acceptance criteria
- [ ] <Observable, testable outcome>
- [ ] Tests cover it; `make test` and `make lint` pass
- [ ] (UI) Screenshot of desktop and mobile in the PR
```

Labels: a type (`bug` / `enhancement` / `documentation`), a priority
(`p1`–`p3`), `from-pm`, and **exactly one of**:

- `ready` — self-contained, one PR, no product decision pending, small enough
  for a Haiku agent with a clear spec (roughly ≤ 300 changed lines).
- `needs-human` — needs a decision, hardware, a deploy, secrets, or is large.
  Say in the body what decision is needed.

Add `control` whenever the change alters control-loop behaviour (setpoints,
VPD math, relay logic, watchdogs). The routine never auto-merges those.

## 5. Report

End with a short summary to the user (in their language, Spanish): what you
looked at, existing issues you triaged or commented on, the issues filed with links and labels, anything urgent, a short note on the
local test tent's state, and what you deliberately didn't file.
