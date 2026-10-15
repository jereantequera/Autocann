---
name: ship-issues
description: Routine that works the Autocann GitHub backlog end to end — picks the next issues labelled `ready`, has Haiku agents implement them in isolated worktrees, reviews and tests the result, opens a PR with screenshots and squash-merges it. Meant to run every 2 hours (scheduled), or on demand when asked to "work the issues", "ship the backlog" or "trabajar las issues".
---

# Ship issues

One run = take up to **2** `ready` issues from `jereantequera/Autocann`, ship
each as its own PR with screenshots, and merge it when it is safe to. You are
the reviewer: Haiku agents write the code, you spec, check and decide.

## Ground rules

- **Never touch production.** The production backend (port 5055 on this Pi) and control
  loop run as plain processes on this Pi. Never restart, stop, deploy, or
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
- Do not create pull requests or merge anything outside this routine's scope.

## 1. Pick the work

```bash
git fetch origin main
gh pr list --state open --json number,title,headRefName,labels
gh issue list --state open --label ready --json number,title,labels,createdAt,assignees --limit 50
```

- First, deal with leftovers: an open PR from an earlier run (branch
  `issue/<n>-…`) whose checks you can now finish → finish it before new work.
- Skip issues labelled `needs-human`, `in-progress`, or with an assignee.
- Order: `p1` → `p2` → `p3` → unlabelled, then oldest first. Take up to 2.
- If there is nothing `ready`, say so in one line and stop. Don't invent work
  (that is the `product-manager` skill's job).

Mark each picked issue: `gh issue edit <n> --add-label in-progress`.

## 2. Spec it

Read the issue and the code it touches. Write a brief for the agent with:
the goal, the files to change, the acceptance criteria copied from the issue,
the tests to add, the commands that must pass (`make test`, `make lint`, or the
narrower `make tent-test` / `cargo test -p …`), the server rule above, "write
everything in English, UI copy in Rioplatense Spanish", and "commit with a
gitmoji-prefixed English message on the current branch; do not push".

If the issue is unclear or turns out to need a product decision, don't guess:
comment on the issue with the question, swap `in-progress` for `needs-human`,
move on.

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

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

Title: short, English, no issue number.

## 7. Merge

Merge with `gh pr merge <pr> --squash --delete-branch` **only if all** hold:

- full `make test` and `make lint` pass on the final commit;
- the issue is not labelled `control` and the diff doesn't change control
  behaviour (setpoints, VPD math, relay logic, watchdogs, `autocann/control/`);
- no migrations of stored data, secrets, or deploy/systemd changes;
- you looked at the screenshots (for UI changes) and they are right.

Otherwise leave the PR open, request review from the repo owner with a
comment that says what to check, and label the issue `needs-human`.

After merging: `gh issue edit <n> --remove-label in-progress` (the issue
closes via "Closes #n"), remove the agent's worktree, and
`git fetch origin main` before the next issue so it starts from the new main.

## 8. Report

Short summary for the user, in Spanish: issues taken, PR links, merged or
left open (and why), anything that needs them — including that merges are not
deployed to the Pi until someone does `make tent-deploy` / restarts the
backend.
