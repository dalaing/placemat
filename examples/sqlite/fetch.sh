#!/bin/sh
# Fetch SQLite amalgamations (public domain) into $1/<version>/, outside this repository.
#   sh fetch.sh ../placemat-work/sqlite 3.52.0:2026 3.53.0:2026
# Each argument is version:year (the year in sqlite.org's download path). speedtest1.c (SQLite's
# own benchmark, used only for the Cachegrind cross-check) comes from the GitHub mirror's tag.
set -e
dest=$1; shift
mkdir -p "$dest"
for vy in "$@"; do
    v=${vy%%:*}; y=${vy##*:}
    n=$(echo "$v" | awk -F. '{printf "%d%02d%02d00", $1, $2, $3}')
    [ -f "$dest/$v/sqlite3.c" ] && continue
    curl -sSf -o "$dest/sqlite-amalgamation-$n.zip" "https://sqlite.org/$y/sqlite-amalgamation-$n.zip"
    mkdir -p "$dest/$v"
    unzip -q -o -j "$dest/sqlite-amalgamation-$n.zip" -d "$dest/$v"
    curl -sSf -o "$dest/$v/speedtest1.c" "https://raw.githubusercontent.com/sqlite/sqlite/version-$v/test/speedtest1.c"
done
