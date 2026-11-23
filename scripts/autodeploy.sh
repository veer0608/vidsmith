#!/usr/bin/env bash
# Put `main` on this box when it has moved, from the box itself.
#
# Run every few minutes by deploy/vidsmith-autodeploy.timer. Pulling from here
# rather than pushing over ssh means a deploy needs no open port, no key and no
# security-group rule matching whoever merged: every deploy used to stop on an
# ssh timeout because a home connection's address had moved. Merging a PR is the
# whole deploy now; `vidsmith deploy` stays for doing it by hand.
#
# What it will not do, in order:
# - touch anything when `main` has not moved (the usual case: one `git fetch`);
# - move a checkout that is on another branch, or whose `main` has diverged;
# - restart while a render is running, because a restart kills it. It waits for
#   the next tick instead. A service that does not answer at all is not
#   rendering, so that does not stop it: the new commit may be the fix.
#
# It prints one line per run, so `journalctl -u vidsmith-autodeploy` reads as a
# deploy log, and exits non-zero only when something needs a person.
#
# Everything is inside main(), called on the last line. This script is part of
# the checkout it updates: bash reads a script as it runs, so a pull that
# rewrote this file mid-run would have it execute half of each version. A
# function is parsed whole before any of it runs.
set -euo pipefail

main() {
    local app_dir="${VIDSMITH_APP_DIR:-$HOME/vidsmith}"
    local url="${VIDSMITH_LOCAL_URL:-http://127.0.0.1:8077}"
    cd "$app_dir"

    git fetch --quiet origin main
    local here there
    here=$(git rev-parse HEAD)
    there=$(git rev-parse origin/main)
    if [ "$here" = "$there" ]; then
        return 0
    fi

    local branch
    branch=$(git rev-parse --abbrev-ref HEAD)
    if [ "$branch" != "main" ]; then
        echo "checked out on $branch, not main; leaving it alone"
        return 0
    fi
    if ! git merge-base --is-ancestor HEAD origin/main; then
        echo "local main has diverged from origin/main; not touching it"
        return 1
    fi

    local busy
    busy=$(curl -s -m 10 "$url/api/busy" || true)
    case "$busy" in
        *'"busy":true'*)
            echo "main is at ${there:0:7} but a render is running; trying again next tick"
            return 0 ;;
    esac

    git merge --ff-only --quiet origin/main
    if ! git diff --quiet "$here" HEAD -- requirements.txt requirements-web.txt; then
        .venv/bin/pip install --quiet -r requirements-web.txt
    fi
    bash scripts/fetch-runtime-deps.sh --fonts-only >/dev/null
    sudo systemctl daemon-reload
    sudo systemctl restart vidsmith

    # Not done until the running process says so: a restart can report active
    # while the old process is still the one answering.
    local live="" i
    for i in $(seq 1 40); do
        live=$(curl -s -m 5 "$url/healthz" \
               | sed -n 's/.*"commit":"\([0-9a-f]*\)".*/\1/p' || true)
        if [ -n "$live" ] && [ "${there#"$live"}" != "$there" ]; then
            echo "deployed ${here:0:7} -> ${there:0:7}"
            return 0
        fi
        sleep 3
    done
    echo "pulled ${there:0:7} and restarted, but /healthz reports '${live:-nothing}'"
    return 1
}

main "$@"
exit $?
