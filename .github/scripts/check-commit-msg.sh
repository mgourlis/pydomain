#!/usr/bin/env bash
# Check commit message format for Conventional Commits with optional YouTrack prefix

set -euo pipefail

# 1. Ignore comments/empty lines, grab the first real line, and strip whitespace
msg=$(grep -vE '^(#|[[:space:]]*$)' "$1" | head -n 1 | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' || true)

# 2. Check if message is empty
if [ -z "$msg" ]; then
    echo "❌ Commit message is empty or only contains comments."
    exit 1
fi

# 3. Validate format step by step
# Expected: [<PROJ-123>:] <type>[(<scope>)][!]: <description>
if ! echo "$msg" | grep -qE "^([A-Z]+-[0-9]+:[[:space:]]*)?(feat|fix|perf|docs|style|refactor|test|chore|ci|build|revert)(\([a-zA-Z0-9_-]+\))?(!)?: .+$"; then

    # Missing colon after type/scope/!
    if echo "$msg" | grep -qE "^([A-Z]+-[0-9]+:[[:space:]]*)?(feat|fix|perf|docs|style|refactor|test|chore|ci|build|revert)(\([a-zA-Z0-9_-]+\))?(!)?[^:]*$"; then
        echo "❌ Missing colon after type/scope/!."
        echo "   Expected something like: feat: add feature or fix(api)!: breaking change"
        exit 1
    fi

    # Missing description after colon
    if echo "$msg" | grep -qE "^([A-Z]+-[0-9]+:[[:space:]]*)?(feat|fix|perf|docs|style|refactor|test|chore|ci|build|revert)(\([a-zA-Z0-9_-]+\))?(!)?: *$"; then
        echo "❌ Missing description after colon."
        exit 1
    fi

    # Invalid type (Checks if the beginning of the string lacks a valid type, with or without prefix)
    if ! echo "$msg" | grep -qE "^([A-Z]+-[0-9]+:[[:space:]]*)?(feat|fix|perf|docs|style|refactor|test|chore|ci|build|revert)(\(|:|!)"; then
        echo "❌ Invalid commit type."
        echo "   Allowed types: feat, fix, perf, docs, style, refactor, test, chore, ci, build, revert."
        exit 1
    fi

    # Generic fallback
    echo "❌ Invalid commit message format."
    echo "   Actual:   $msg"
    echo "   Expected: [<PROJ-123>:] <type>[(<scope>)][!]: <description>"
    exit 1
fi

echo "✅ Commit message format is valid."
exit 0
