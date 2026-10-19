#!/bin/sh
set -eu
src=$(cd "$(dirname "$0")" && pwd)
dest="$HOME/.local/opt/blendex"
apps="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
if [ "${1:-}" = "--uninstall" ]; then
    rm -rf "$dest" "$apps/blendex.desktop" "${XDG_DATA_HOME:-$HOME/.local/share}/Blendex" "${XDG_CACHE_HOME:-$HOME/.cache}/Blendex"
    echo "Blendex removed. The library index in ~/.local/share/dam is kept; delete it to forget your libraries."
    exit 0
fi
if [ "$src" != "$dest" ]; then
    rm -rf "$dest"
    mkdir -p "$dest"
    cp -a "$src/." "$dest/"
fi
mkdir -p "$apps"
sed "s|@APP@|$dest|g" "$src/blendex.desktop" > "$apps/blendex.desktop"
update-desktop-database "$apps" 2>/dev/null || true
echo "Blendex installed in $dest. Start it from your app menu or run $dest/Blendex"
echo "Uninstall with: $dest/install.sh --uninstall"
