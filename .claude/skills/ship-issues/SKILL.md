---
name: ship-issues
description: Routine that works the Autocann GitHub backlog end to end — picks the next `ready` or `needs-human` issues, has Haiku agents implement them in isolated worktrees, reviews and tests the result, and opens a PR with screenshots. `ready` PRs are squash-merged when safe; `needs-human` PRs are left for a person with the open questions written down, and the routine follows up on their answers. Meant to run every 2 hours (scheduled), or on demand when asked to "work the issues", "ship the backlog" or "trabajar las issues".
---

# Ship issues

One run = follow up on answered PRs, then take up to **2** issues from
`jereantequera/Autocann` and ship each as its own PR with screenshots. You are
the reviewer: Haiku agents write the code, you spec, check and decide.

Two kinds of work:

- **`ready`** — scoped and decided. PR, and merge it when step 7 allows.
- **`needs-human`** — needs a decision or a person's review. Still do the
  work: make the most reasonable assumptions, open the PR, and write the open
  questions and assumptions in it. **Never merge it**; a person answers in the
  PR and merges. When they answer, a later run picks it up again (step 0).

Never work issues labelled `epic` (tracking only; work is in their slices) or
`hardware` (needs hands on the physical tent).

## Ground rules

- **Never touch the live tent.** There is no production yet, but this Pi runs
  the local test tent: its control loop drives real relays, and its backend
  (port 5000, sometimes a preview on 5055) runs as plain processes here. Never
  restart, stop, deploy, or
  `pkill`/`killall` anything by name; stop your own servers by PID only. Ports
  5000 and 5055 are off limits. Merging does **not** deploy — say so in the
  report.
- **Every agent brief** that may start a server must repeat that rule
  verbatim: "start it with `& echo $!`, stop only that PID, never pkill/killall
  by name, never use ports 5000 or 5055".
- **English** for code, comments, commits, PRs. UI copy stays Rioplatense
  Spanish.
- **Never merge red.** If tests or lint fail and two fix rounds don't fix it,
  leave the PR open as draft, comment why, label the issue `needs-human`.
- **Never merge a `needs-human` or `control` PR**, even if it is green and a
  person approved it: merging those is the person's call.
- Do not create pull requests or merge anything outside this routine's scope.

## 0. Follow up on PRs waiting for a person

```bash
gh pr list --state open --author "@me" --json number,title,headRefName,isDraft,reviewDecision,updatedAt
```

For each open PR from this routine (branch `issue/<n>-…`), read the comments
and reviews since its last commit (`gh pr view <pr> --comments`,
`gh api repos/jereantequera/Autocann/pulls/<pr>/comments`). If a person
answered a question or asked for changes:

1. Check out the branch in a worktree, rebase on `origin/main`.
2. Send the answers to a Haiku agent as a brief (same rules as step 2/3),
   review the result (step 4), refresh screenshots if the UI changed (step 5).
3. Push, and reply in the PR: what changed per answer, and any questions that
   are still open. Update the PR's "Questions for review" section.
4. Don't merge it (it is `needs-human` or `control`); if nothing is open any
   more, say so in the reply ("ready to merge from my side").

Follow-ups count toward the 2 items per run. A PR with no new human input is
left alone.

## 1. Pick the work

```bash
git fetch origin main
gh label create in-progress --color 1d76db --description "Being worked by the ship-issues routine" 2>/dev/null
gh pr list --state open --json number,title,headRefName,labels
gh issue list --state open --label ready --json number,title,labels,body,createdAt,assignees --limit 50
gh issue list --state open --label needs-human --json number,title,labels,body,createdAt,assignees --limit 50
```

- First, deal with leftovers: an open PR from an earlier run (branch
  `issue/<n>-…`) whose checks you can now finish → finish it before new work.
- Skip issues labelled `epic`, `hardware`, `in-progress`, or with an
  assignee.
- **Skip issues whose dependencies are still open:** a body line like
  "Depends on #51" (or "Depends on #52 and #55") where any of those issues is
  open. They become workable once the dependency's PR is merged.
- **Skip issues someone is already on**, even without the label: an open PR
  that mentions it (`gh pr list --state open --search "<n>"`), a remote branch
  for it (`git ls-remote --heads origin "issue/<n>-*"`), or a recent comment
  saying it's being worked. Also skip it if a PR merged in the last day
  already touches the same thing — comment on the issue asking whether it's
  still needed.
- Order: all `ready` issues first, then `needs-human`; within each,
  `p1` → `p2` → `p3` → unlabelled, then oldest first. Take up to 2 (minus
  follow-ups done in step 0).
- A `needs-human` issue too big for one PR (roughly > 400 changed lines):
  build the smallest useful first slice and say in the PR what's left.
- **Don't pick two issues that touch the same files** in one run (compare the
  files each issue names, or what a quick read of the code says it will
  change). Take the higher-ranked one; the other waits for the next run.
- If there is nothing workable, say so in one line and stop. Don't invent work
  (that is the `product-manager` skill's job).

Claim each picked issue before writing any code, so nobody else (a person, or
another run) picks it up:

```bash
gh issue edit <n> --add-label in-progress
gh issue comment <n> --body "🤖 ship-issues is working on this (branch \`issue/<n>-<slug>\`)."
```

Re-read the issue right after claiming: if someone else claimed it in the
meantime (another `in-progress` comment or an assignee), back off and remove
your label.

## 2. Spec it

Read the issue and the code it touches. Write a brief for the agent with:
the goal, the files to change, the acceptance criteria copied from the issue,
the tests to add, the commands that must pass (`make test`, `make lint`, or the
narrower `make tent-test` / `cargo test -p …`), the server rule above, "write
everything in English, UI copy in Rioplatense Spanish", and "commit with a
gitmoji-prefixed English message on the current branch; do not push".

If a `ready` issue turns out to need a product decision, treat it as
`needs-human`: relabel it, then carry on the same way — pick the most
reasonable option, build it, and put the question in the PR. Write the
assumptions into the brief so the agent builds exactly that.

## 3. Implement (Haiku, isolated)

For each issue launch an `Agent` with `model: "haiku"`, `isolation:
"worktree"`, and your brief. Two issues → launch both in the same message so
they run in parallel. Tell the agent to first run
`git checkout -B issue/<n>-<slug> origin/main`.

## 4. Review

When an agent finishes, in its worktree:

1. `git diff origin/main...HEAD` — read all of it. Check it does what the
   issue asks and nothing else, matches the surrounding code, has tests, no
   debug leftovers, no Spanish in code/comments.
2. Run the gates yourself: `make test` and `make lint` (scope down to the
   touched part if the full run is very slow, but run the full `make test`
   before merging).
3. Problems → send them back to the same agent (`SendMessage`) with exact
   file:line notes. At most 2 rounds; then follow "Never merge red".

## 5. Screenshots

If the change touches anything the user can see (`apps/tent/autocann/web/`,
templates, static, copy), from the worktree:

```bash
apps/tent/scripts/screenshot.sh "$SCRATCH/issue-<n>" <views affected>
```

Look at every image with Read. If it looks wrong, that's a review failure (go
back to step 4). For backend-only or Rust changes, skip images and paste the
relevant test or command output in the PR instead.

### Publishing images

Images live on the orphan branch `pr-assets` (public repo, so raw URLs render
in PRs and issues). Use a separate worktree so you never mix it with code:

```bash
A="$SCRATCH/pr-assets"
if [ ! -d "$A" ]; then
  if git ls-remote --exit-code origin pr-assets >/dev/null; then
    git worktree add "$A" origin/pr-assets -B pr-assets
  else
    git worktree add --detach "$A" && git -C "$A" switch --orphan pr-assets
  fi
fi
git -C "$A" pull --ff-only origin pr-assets 2>/dev/null || true
mkdir -p "$A/pr/issue-<n>" && cp "$SCRATCH/issue-<n>"/*.png "$A/pr/issue-<n>/"
git -C "$A" add -A && git -C "$A" commit -m "📸 Screenshots for issue #<n>" && git -C "$A" push -u origin pr-assets
```

URL: `https://raw.githubusercontent.com/jereantequera/Autocann/pr-assets/pr/issue-<n>/<file>.png`

## 6. Open the PR

Push the branch (`git push -u origin issue/<n>-<slug>`, retry on network
errors with 2/4/8/16 s backoff) and open the PR following
`.github/pull_request_template.md`, in English:

```markdown
## What it solves
<The problem, 1–2 lines.> Closes #<n>.

## What I did
- <change>
- Tests: <what was added>; `make test` and `make lint` pass (<counts>).

## Screenshots
| Desktop | Mobile |
|---|---|
| ![](<url>) | ![](<url>) |

<Anything to do by hand after merging, e.g. restart the backend.>

## Questions for review        <!-- needs-human / control PRs only -->
1. <The decision needed, the option I took, and the alternative.>

## Assumptions                 <!-- needs-human / control PRs only -->
- <What I decided on my own that a person should confirm.>

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

Title: short, English, no issue number.

For `needs-human` and `control` PRs: open them as **draft** (`--draft`) while
questions are open, mark them ready (`gh pr ready`) once nothing is open, and
comment on the issue with the PR link and the questions so whoever watches the
issue sees them.

## 7. Merge

Merge with `gh pr merge <pr> --squash --delete-branch` **only if all** hold:

- full `make test` and `make lint` pass on the final commit;
- the issue is labelled `ready` (not `needs-human`), not labelled `control`,
  and the diff doesn't change control behaviour (setpoints, VPD math, relay
  logic, watchdogs, `autocann/cli/vpd.py`, `autocann/control/`);
- database migrations are additive only (new tables or columns, idempotent
  backfill, covered by a test on an old-schema fixture) — nothing dropped,
  rebuilt or rewritten — and no secrets or deploy/systemd changes;
- you looked at the screenshots (for UI changes) and they are right.

Otherwise leave the PR open, request review from the repo owner with a
comment that says what to check, and label the issue `needs-human`. The
issue keeps `in-progress` while its PR is open, so no other run takes it.

After merging: `gh issue edit <n> --remove-label in-progress` (the issue
closes via "Closes #n"), remove the agent's worktree, and
`git fetch origin main` before the next issue so it starts from the new main.

## 8. Report

Short summary for the user, in Spanish: follow-ups done, issues taken, PR
links, merged or left open (and why), the open questions waiting for them — including that merges are not
deployed to the Pi until someone does `make tent-deploy` / restarts the
backend.
