#!/usr/bin/env bash
# Put `main` on this box when it has moved, from the box itself.
#
# Run every few minutes by deploy/vidsmith-autodeploy.timer. Pulling from here
# rather than pushing over ssh means a deploy needs no open port, no key and no
# security-group rule matching whoever merged: every deploy used to stop on an
# ssh timeout because a home connection's address had moved. Merging a PR is the
# whole deploy now; `vidsmith deploy` stays for doing it by hand.
#
# What it will not do:
# - touch anything when the running service is already on `main` (the usual
#   case: one `git fetch` and one local request);
# - move a checkout that is on another branch, or whose `main` has diverged;
# - restart while a render is running, because a restart kills it. It asks
#   twice: before pulling, and again right before the restart, because a pull and
#   a pip install take long enough for a render to start in between.
#
# The second check is why the decision is made against the *running* commit
# (`/healthz`), not only against what is on disk. A run that pulled and then
# found a render waits; the next run sees the checkout already on `main` but the
# service still on the old commit, and does the restart it owes. Comparing only
# HEAD with origin/main would call that "nothing new" forever.
#
# Every run that does something writes one JSON line to the state file
# ($VIDSMITH_AUTODEPLOY_STATE, default jobs/_autodeploy.json), which /healthz
# reports, so a deploy that failed halfway shows up where the box is already
# checked instead of only in `journalctl -u vidsmith-autodeploy`. A run with
# nothing to do writes nothing, so the last real event stays visible.
#
# Everything is inside main(), called on the last line. This script is part of
# the checkout it updates: bash reads a script as it runs, so a pull that
# rewrote this file mid-run would have it execute half of each version. A
# function is parsed whole before any of it runs.
set -Eeuo pipefail

app_dir="${VIDSMITH_APP_DIR:-$HOME/vidsmith}"
url="${VIDSMITH_LOCAL_URL:-http://127.0.0.1:8077}"
state="${VIDSMITH_AUTODEPLOY_STATE:-$app_dir/jobs/_autodeploy.json}"
target=""
recorded=""

json_text() {
    # a JSON string body: backslashes, quotes, and anything below space dropped
    printf '%s' "$1" | tr -d '\000-\037' | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'
}

record() {
    # record STATE OK MESSAGE: STATE is deployed, waiting, refused or failed
    local at
    at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    mkdir -p "$(dirname "$state")"
    printf '{"state":"%s","ok":%s,"at":"%s","commit":"%s","said":"%s"}\n' \
        "$1" "$2" "$at" "${target:0:7}" "$(json_text "$3")" > "$state.tmp"
    mv "$state.tmp" "$state"
    recorded=1
    echo "$3"
}

# Any command that fails under `set -e` lands here before the script exits, so
# a pip install or a merge that breaks is recorded, not only logged. A failure
# main() already recorded with its own words is not overwritten.
trap 'if [ -z "$recorded" ]; then record failed false "failed at: $BASH_COMMAND"; fi' ERR

running() {
    # Read as JSON, the top-level `commit` only. A sed for `"commit":"..."` used
    # to do this, and its greedy `.*` took the *last* match: once /healthz carried
    # this script's own record, whose `commit` is the previous deploy, every
    # deploy compared against that and recorded itself as failed (ad52a22, which
    # had landed fine). The box has python3 for the app's own venv.
    curl -s -m 5 "$url/healthz" | python3 -c '
import json, sys
try:
    body = json.load(sys.stdin)
except ValueError:
    body = {}
print(body.get("commit", "") if isinstance(body, dict) else "")
' 2>/dev/null || true
}

is_on() {
    # is_on SHORT FULL: does the running short sha name the full one
    [ -n "$1" ] && [ "${2#"$1"}" != "$2" ]
}

busy() {
    case "$(curl -s -m 10 "$url/api/busy" || true)" in
        *'"busy":true'*) return 0 ;;
        *) return 1 ;;                 # idle, or not answering: down is not rendering
    esac
}

main() {
    cd "$app_dir"

    git fetch --quiet origin main
    local here live
    here=$(git rev-parse HEAD)
    target=$(git rev-parse origin/main)
    live=$(running)

    if [ "$here" = "$target" ]; then
        # On disk already. Owed a restart only when the service is up and
        # running something else; a service that is down is systemd's to restart.
        if [ -z "$live" ]; then
            return 0
        fi
        if is_on "$live" "$target"; then
            # A record saying this very commit failed, while it is plainly
            # running, is wrong: an earlier run misread /healthz, or the service
            # came up after its health wait gave up. Correct it, so /healthz does
            # not report a failure that is not there.
            if grep -Eq '"ok"[[:space:]]*:[[:space:]]*false' "$state" 2>/dev/null \
                    && grep -Eq "\"commit\"[[:space:]]*:[[:space:]]*\"${target:0:7}\"" "$state" 2>/dev/null; then
                record deployed true "${target:0:7} is running; an earlier run recorded it as failed"
            fi
            return 0
        fi
    fi

    local branch
    branch=$(git rev-parse --abbrev-ref HEAD)
    if [ "$branch" != "main" ]; then
        echo "checked out on $branch, not main; leaving it alone"
        return 0
    fi

    if busy; then
        record waiting true "main is at ${target:0:7} but a render is running; trying again next tick"
        return 0
    fi

    if [ "$here" != "$target" ]; then
        if ! git merge-base --is-ancestor HEAD origin/main; then
            record refused false "local main has diverged from origin/main; not touching it"
            return 1
        fi
        git merge --ff-only --quiet origin/main
        if ! git diff --quiet "$here" HEAD -- requirements.txt requirements-web.txt; then
            .venv/bin/pip install --quiet -r requirements-web.txt
        fi
        bash scripts/fetch-runtime-deps.sh --fonts-only >/dev/null
    fi

    # Again, right before the restart: the pull and install above take long
    # enough for a render to have started. The code is on disk now and the next
    # run restarts into it, because it compares against what is running.
    if busy; then
        record waiting true "pulled ${target:0:7}, but a render started; restarting next tick"
        return 0
    fi

    local was="${live:-${here:0:7}}"
    sudo systemctl daemon-reload
    sudo systemctl restart vidsmith

    # Not done until the running process says so: a restart can report active
    # while the old process is still the one answering.
    local i
    for i in $(seq 1 40); do
        live=$(running)
        if is_on "$live" "$target"; then
            record deployed true "deployed ${was:0:7} -> ${target:0:7}"
            return 0
        fi
        sleep 3
    done
    record failed false "pulled ${target:0:7} and restarted, but /healthz reports '${live:-nothing}'"
    return 1
}

main "$@"
exit $?
