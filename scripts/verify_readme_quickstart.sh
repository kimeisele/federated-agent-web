#!/usr/bin/env bash
# Verify the README clean-clone quick-start path.
# Clones the current checked-out repository into a disposable temporary
# directory, follows the exact ordinary-user installation commands, and
# asserts that `faw demo` succeeds in under 10 minutes.
set -euo pipefail

source_repo="$(pwd)"
source_sha="$(git rev-parse HEAD)"
trial_root="$(mktemp -d)"
trap 'rm -rf "$trial_root"' EXIT

echo "=== README quick-start gate ==="
echo "source_commit: $source_sha"

start_seconds="$SECONDS"

# 1. Clone
git clone --quiet "$source_repo" "$trial_root/federated-agent-web"
git -C "$trial_root/federated-agent-web" checkout --quiet --detach "$source_sha"

cd "$trial_root/federated-agent-web"

# 2. Follow the exact README ordinary-user path
unset PYTHONPATH

python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install .

demo_output="$(faw demo 2>&1)"
printf '%s\n' "$demo_output"

if ! grep -Fq "demo: OK" <<<"$demo_output"; then
    echo "FAIL: 'demo: OK' not found in faw demo output"
    exit 1
fi

# 3. Check source tree is clean
if [ -n "$(git status --short)" ]; then
    echo "FAIL: source tree is not clean after faw demo:"
    git status --short
    exit 1
fi

elapsed_seconds="$((SECONDS - start_seconds))"
echo "elapsed_seconds: $elapsed_seconds"

if [ "$elapsed_seconds" -ge 600 ]; then
    echo "FAIL: elapsed time $elapsed_seconds >= 600 seconds"
    exit 1
fi

echo "source_tree_clean: yes"
echo "README quick start: OK"
