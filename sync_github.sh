#!/bin/sh
# Mirror the current state of main to GitHub (ArifFadillah1/3rdParty).
#
# The GitLab history must never be pushed there: tracked files carried live
# store credentials before they were consolidated into credentials.py, and the
# GitHub repo is public. So instead of pushing main itself, each run makes one
# snapshot commit on the history-free github-main branch whose tree is exactly
# main's tree, and pushes that. credentials.py is untracked and therefore can
# never be included.
set -e
cd "$(git rev-parse --show-toplevel)"

main_sha=$(git rev-parse main)
tree=$(git rev-parse 'main^{tree}')

# ── Secret gate ──────────────────────────────────────────────────────────────
# Everything below pushes main's tree to a PUBLIC repo, so refuse to sync if
# anything in that tree looks like a credential. Scans the tree being pushed
# (not the working dir, where the untracked credentials.py legitimately lives).
# A finding only skips the sync — the local commit itself is untouched.
leak_patterns='-----BEGIN [A-Z ]*PRIVATE KEY-----
"client_secret":[[:space:]]*"[A-Fa-f0-9]\{16,\}"
Basic [A-Za-z0-9+/=]\{24,\}
eyJ[A-Za-z0-9_-]\{10,\}\.eyJ
AKIA[0-9A-Z]\{16\}'
leaks=$(echo "$leak_patterns" | while IFS= read -r p; do
    git grep -n -e "$p" main -- 2>/dev/null || true
done)
if [ -n "$leaks" ]; then
    echo "sync_github: possible secret in main's tree — NOT pushing to GitHub:" >&2
    echo "$leaks" | sed 's/^/  /' >&2
    echo "Remove the secret (and rewrite the commit) before syncing." >&2
    exit 1
fi

# Deeper scan when gitleaks is installed (brew install gitleaks): run it on an
# export of exactly the tree being pushed.
if command -v gitleaks >/dev/null 2>&1; then
    scan_dir=$(mktemp -d)
    trap 'rm -rf "$scan_dir"' EXIT
    git archive main | tar -x -C "$scan_dir"
    if ! gitleaks detect --source "$scan_dir" --no-git --redact --exit-code 1 >/dev/null 2>&1; then
        echo "sync_github: gitleaks found possible secrets in main's tree — NOT pushing." >&2
        echo "Run: gitleaks detect --source <repo> --no-git --redact   for details." >&2
        exit 1
    fi
fi

if [ "$(git rev-parse 'github-main^{tree}')" = "$tree" ]; then
    echo "github-main already matches main - nothing to sync."
    exit 0
fi

# The last synced main commit is recorded as a Synced-from trailer, so the
# snapshot message can cover exactly the main commits synced since. The usual
# case is one commit (the post-commit hook runs per commit), and its message
# is reused verbatim so the GitHub history reads like the real one; a
# multi-commit catch-up gets a summary subject with one bullet per commit.
prev=$(git log -1 github-main --pretty=%B | sed -n 's/^Synced-from: //p')
count=$(git rev-list --count ${prev:+$prev..}main)

if [ "$count" -eq 1 ]; then
    msg=$(git log -1 --pretty=%B main)
else
    msg=$(printf 'Sync %s commits from local main\n\n%s' \
          "$count" "$(git log --reverse --pretty='- %s' ${prev:+$prev..}main)")
fi

new=$(printf '%s\n\nSynced-from: %s' \
      "$msg" "$main_sha" | git commit-tree -p github-main "$tree")
git update-ref refs/heads/github-main "$new"
git push github github-main:main
