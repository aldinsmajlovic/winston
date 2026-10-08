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
    uvx ruff check src tests scripts
    ! grep -rn "—" --include='*.py' --include='*.md' --include='*.yaml' src docs examples README.md

# the checks that need no hardware
test:
    uv run winston check unit
