#!/usr/bin/env bash
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

status=0
home_pattern="/""home/|/""Users/|[A-Za-z]:\\\\""Users\\\\"
token_pattern="gh""p_[A-Za-z0-9]{20,}|github_""pat_[A-Za-z0-9_]{20,}"

while IFS= read -r -d '' path; do
    case "$path" in
        raw/*|normalized/*|exports/*|state/*|datasets/*|*.sqlite|*.sqlite3|*.db|*.jsonl|*.part|credentials.env|.env|.env.*)
            if [[ "$path" != ".env.example" ]]; then
                echo "forbidden release path: $path" >&2
                status=1
                continue
            fi
            ;;
    esac

    if [[ -f "$path" ]] && grep -Iq . "$path"; then
        if grep -nE "$home_pattern" "$path"; then
            echo "absolute local path in $path" >&2
            status=1
        fi
        if grep -nE "$token_pattern" "$path"; then
            echo "token-like value in $path" >&2
            status=1
        fi
    fi
done < <(git ls-files --cached --others --exclude-standard -z)

if [[ "$status" -ne 0 ]]; then
    exit "$status"
fi

echo "public tree boundary: ok"