#!/bin/sh
# Installs Winston on Linux, then hands over to `winston setup` (docs/setup.md):
#
#   curl --proto '=https' -fsSL https://github.com/aldinsmajlovic/winston/releases/latest/download/install.sh | sh
#
# uv into ~/.local/bin if it is missing, the release checked against its SHA256SUMS and unpacked into
# ~/.local/share/winston-app/<version>, its packages, `winston` in ~/.local/bin, then `winston setup` with
# this script's arguments (`sh -s -- --yes --profile hosted`). WINSTON_VERSION=0.2.0 installs that release
# instead of the latest, and WINSTON_RELEASES gets it from somewhere other than GitHub.
#
# Everything is in main, called on the last line, so a download cut short runs nothing.

main() {
    set -eu
    uv_version=0.10.6
    releases=${WINSTON_RELEASES:-https://github.com/aldinsmajlovic/winston/releases}
    app=$HOME/.local/share/winston-app
    bin=$HOME/.local/bin

    for tool in curl tar sha256sum; do
        command -v "$tool" >/dev/null || { echo "Installing Winston needs $tool." >&2; exit 1; }
    done
    if [ -n "${WINSTON_VERSION:-}" ]; then url=$releases/download/v$WINSTON_VERSION; else url=$releases/latest/download; fi
    tmp=$(mktemp -d)
    trap 'rm -rf "$tmp"' EXIT
    get() { curl --proto "=${releases%%://*}" -fsSL "$url/$1" -o "$tmp/$1"; }

    get SHA256SUMS || { echo "No Winston release at $url." >&2; exit 1; }
    name=$(sed -n 's/^[0-9a-f]\{64\}  \(winston-.*\.tar\.gz\)$/\1/p' "$tmp/SHA256SUMS")
    [ -n "$name" ] || { echo "No Winston release in $url/SHA256SUMS." >&2; exit 1; }
    version=${name#winston-}
    version=${version%.tar.gz}

    if [ -d "$app/$version" ]; then
        echo "Winston $version is already here."
    else
        echo "Downloading Winston $version"
        get "$name"
        if ! (cd "$tmp" && grep "  $name\$" SHA256SUMS | sha256sum -c --status); then
            echo "$name does not match its SHA256SUMS, so nothing was installed. Try again, and if it happens" \
                 "again, say so at https://github.com/aldinsmajlovic/winston/issues" >&2
            exit 1
        fi
        rm -rf "$app/.$version"
        mkdir -p "$app/.$version"
        tar -xzf "$tmp/$name" -C "$app/.$version" --strip-components=1
        mv "$app/.$version" "$app/$version"
    fi

    if command -v uv >/dev/null; then
        uv=$(command -v uv)
    elif [ -x "$bin/uv" ]; then
        uv=$bin/uv
    else
        echo "Installing uv $uv_version"
        curl --proto '=https' -fsSL "https://astral.sh/uv/$uv_version/install.sh" -o "$tmp/uv.sh"
        UV_INSTALL_DIR=$bin sh "$tmp/uv.sh" --quiet
        uv=$bin/uv
    fi
    echo "Installing Winston's packages (a minute or two the first time)"
    (cd "$app/$version" && "$uv" sync --frozen --quiet)

    ln -sfn "$version" "$app/current"
    mkdir -p "$bin"
    ln -sf "$app/current/.venv/bin/winston" "$bin/winston"

    echo
    if (: </dev/tty) 2>/dev/null; then
        PATH="$bin:$PATH" "$bin/winston" setup "$@" </dev/tty
    else
        PATH="$bin:$PATH" "$bin/winston" setup "$@"
    fi
    case ":$PATH:" in
        *":$bin:"*) ;;
        *) echo "To run winston (status, update, uninstall), open a new terminal, or add $bin to your PATH." ;;
    esac
}

main "$@"
