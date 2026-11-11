#!/usr/bin/env bash
#
# Push the current commit, rebasing and retrying if the branch moved.
#
# Three workflows commit to main on their own schedules (the weekly catalog,
# the daily usage stats) and people push to it too. A bare `git push` loses
# that race: the push is rejected as non-fast-forward, the step fails, and
# the work the run just did is thrown away. For the catalog that is a week of
# data, and the run would have to be noticed and re-run by hand.
#
# So: push; if rejected, fetch the branch, rebase the one new commit onto it
# and try again, a few times with a growing pause. A real conflict (both sides
# changed the same file) is not retried: the rebase is aborted so the checkout
# is left clean, and the script exits 2 so the caller can tell a conflict from
# a remote that simply would not accept the push (exit 1).
#
# Usage: scripts/git_push_retry.sh [remote] [branch]
#   PUSH_ATTEMPTS  how many pushes to try (default 4)
#   PUSH_BACKOFF   seconds to wait before retry N is N * this (default 3)

set -uo pipefail

remote="${1:-origin}"
branch="${2:-main}"
attempts="${PUSH_ATTEMPTS:-4}"
backoff="${PUSH_BACKOFF:-3}"

i=1
while [ "$i" -le "$attempts" ]; do
  if git push "$remote" "HEAD:$branch"; then
    exit 0
  fi
  if [ "$i" -eq "$attempts" ]; then
    break
  fi
  echo "push rejected (attempt $i of $attempts); rebasing onto $remote/$branch" >&2
  if ! git fetch "$remote" "$branch"; then
    echo "could not fetch $remote/$branch" >&2
  elif ! git rebase FETCH_HEAD; then
    git rebase --abort 2>/dev/null || true
    echo "ERROR: the commit conflicts with what is now on $remote/$branch; not pushed" >&2
    exit 2
  fi
  sleep "$((i * backoff))"
  i=$((i + 1))
done

echo "ERROR: could not push to $remote/$branch after $attempts attempt(s)" >&2
exit 1
