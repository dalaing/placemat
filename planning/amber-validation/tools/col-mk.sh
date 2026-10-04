#!/bin/bash
# mk.sh NAME POLICY [DEFINES...]: a copy of src (main d596e57a) with the colouring patch, built.
S=/private/tmp/claude-501/-Users-dave-work-ngn-k-k/eb2f95d5-4068-4d95-8225-d12e510d750c/scratchpad
cd $S/col || exit 1
d=$1; shift
rm -rf "$d" && cp -R src "$d" && python3 tools/patch.py "$d" "$@" || exit 1
cd "$d" && sh /Users/dave/work/ngn-k/amber/pbt/qbuild.sh ./build.sh > build.log 2>&1; grep -A5 error build.log; ls -la amber
