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

if [ "$(git rev-parse 'github-main^{tree}')" = "$tree" ]; then
    echo "github-main already matches main - nothing to sync."
    exit 0
fi

# The last synced main commit is recorded as a Synced-from trailer, so the
# snapshot message can list every main commit it covers.
prev=$(git log -1 github-main --pretty=%B | sed -n 's/^Synced-from: //p')
subjects=$(git log --reverse --pretty='- %s' ${prev:+$prev..}main)

new=$(printf 'Sync from local main\n\n%s\n\nSynced-from: %s' \
      "$subjects" "$main_sha" | git commit-tree -p github-main "$tree")
git update-ref refs/heads/github-main "$new"
git push github github-main:main
