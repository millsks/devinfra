#!/usr/bin/env bash
# Install this repository's git hooks into the clone you are standing in.
#
#   pixi run bootstrap
#
# .git/hooks is not part of the tree, so every clone starts with no hooks and
# nothing can commit them. This points core.hooksPath at the tracked .githooks/
# directory instead: one line of config, no copying, and therefore no installed
# hook that is a stale copy of the tracked one.
#
# Everything it does is read back afterwards. An installer that appears to work
# and does not is the same silent pass this repository keeps removing.
#
# It is per clone and it is bypassable: `git commit --no-verify` still lands the
# commit. CI remains the authoritative gate.
set -euo pipefail

# Resolved from the working directory rather than from this file's location, so
# the hooks land in the clone the developer is standing in and a run outside a
# work tree refuses instead of guessing one.
if ! toplevel="$(git rev-parse --show-toplevel 2>/dev/null)"; then
    echo "bootstrap: not inside a git work tree — run this from a clone." >&2
    exit 1
fi
cd "$toplevel"

hooks_dir=".githooks"
if [[ ! -d "$hooks_dir" ]]; then
    echo "bootstrap: no ${hooks_dir}/ directory in ${toplevel} — nothing to install." >&2
    exit 1
fi

hooks=("$hooks_dir"/*)
if [[ ! -e "${hooks[0]}" ]]; then
    echo "bootstrap: ${hooks_dir}/ is empty — an installer with no hooks verifies nothing." >&2
    exit 1
fi

# git ignores a hook file it cannot execute, and says nothing about it. Checked
# before any config is written, so a refusal leaves the clone as it was.
unexecutable=()
for hook in "${hooks[@]}"; do
    if [[ ! -x "$hook" ]]; then
        unexecutable+=("$hook")
    fi
done
if ((${#unexecutable[@]} > 0)); then
    echo "bootstrap: not executable: ${unexecutable[*]}" >&2
    echo "bootstrap: git ignores a hook it cannot run — chmod +x it and try again." >&2
    exit 1
fi

# Read without --local, both before and after: --local would only prove that the
# value just written is still where it was written. What decides which hooks git
# runs is the effective value, and with extensions.worktreeConfig enabled a
# worktree-scoped setting outranks the local one — an installer that reported
# success while git ran a different path is the silent pass in a new place.
previous=""
if git config --get core.hooksPath >/dev/null; then
    previous="$(git config --get core.hooksPath)"
fi

git config --local core.hooksPath "$hooks_dir"

actual="$(git config --get core.hooksPath)"
if [[ "$actual" != "$hooks_dir" ]]; then
    echo "bootstrap: core.hooksPath is '${actual}', not '${hooks_dir}' — something outranks the local value." >&2
    exit 1
fi

if [[ "$previous" == "$hooks_dir" ]]; then
    echo "bootstrap: core.hooksPath was already ${hooks_dir} — ${#hooks[@]} hook(s), nothing to change."
elif [[ -n "$previous" ]]; then
    echo "bootstrap: core.hooksPath changed from '${previous}' to '${hooks_dir}' — ${#hooks[@]} hook(s) installed."
else
    echo "bootstrap: core.hooksPath set to ${hooks_dir} — ${#hooks[@]} hook(s) installed."
fi
