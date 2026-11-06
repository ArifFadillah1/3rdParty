#!/bin/sh
# Push main to the public GitHub mirror (ArifFadillah1/3rdParty, remote
# "github"), refusing if the tree being pushed looks like it contains a
# credential.
#
# main's history is the mirror's own history (this clone was taken from
# GitHub), so main is pushed as-is; the earlier history-free snapshot branch
# is no longer needed. credentials.py is untracked and can never be included.
set -e
cd "$(git rev-parse --show-toplevel)"

main_sha=$(git rev-parse main)

# ── Secret gate ──────────────────────────────────────────────────────────────
# Everything below pushes main's tree to a PUBLIC repo, so refuse to sync if
# anything in that tree looks like a credential. Scans the tree being pushed
# (not the working dir, where the untracked credentials.py legitimately lives).
# A finding only skips the push — the local commit itself is untouched.
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

if [ "$(git rev-parse -q --verify github/main 2>/dev/null)" = "$main_sha" ]; then
    echo "github/main already matches main - nothing to push."
    exit 0
fi

git push github main:main
