# Task runner. Install: https://github.com/casey/just

default:
    @just --list

# create the pipeline and TTS environments
setup:
    uv sync
    cd services/tts-chatterbox && uv sync

up *ARGS:
    uv run winston up {{ARGS}}

status:
    uv run winston status

talk *ARGS:
    uv run winston talk {{ARGS}}

check *ARGS:
    uv run winston check {{ARGS}}

lint:
    uvx ruff check src tests sdk scripts custom_components
    ! grep -rn "—" --include='*.py' --include='*.md' --include='*.yaml' src docs examples README.md custom_components

# the checks that need no hardware
test:
    uv run winston check unit

# release VERSION (like 0.1.3): dates its CHANGELOG.md entry, pushes that to main, then the tag CI builds from
release VERSION:
    #!/usr/bin/env bash
    set -euo pipefail
    v={{VERSION}}
    [[ $v =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-]?[0-9A-Za-z.]+)?$ ]] || { echo "$v is not a version like 0.2.0"; exit 1; }
    [[ $(git branch --show-current) == main ]] || { echo "Release from main."; exit 1; }
    [[ -z $(git status --porcelain) ]] || { echo "Commit or stash your changes first."; exit 1; }
    git fetch -q origin main --tags
    [[ $(git rev-list --count HEAD..origin/main) == 0 ]] || { echo "origin/main has commits this does not: pull first."; exit 1; }
    ! git rev-parse -q --verify "refs/tags/v$v" >/dev/null || { echo "v$v exists already."; exit 1; }
    if [[ $v != *-* ]]; then
        grep -qF "## $v (" CHANGELOG.md || { echo "CHANGELOG.md has no entry \"## $v (unreleased)\": write what it brings first."; exit 1; }
        sed -i "s/^## ${v//./\\.} (unreleased)\$/## $v ($(date +%F))/" CHANGELOG.md
        grep -qE "^## ${v//./\\.} \([0-9]{4}-[0-9]{2}-[0-9]{2}\)$" CHANGELOG.md || { echo "Its CHANGELOG.md heading should read \"## $v (unreleased)\" or have a date."; exit 1; }
        git diff --quiet || git commit -qs -m "Release $v" CHANGELOG.md
    fi
    git push -q origin main
    git tag -a "v$v" -m "Winston $v"
    git push -q origin "v$v"
    echo "Tagged v$v. CI builds the release in about 8 minutes (gh run watch); Winston installs then see it on Status."
