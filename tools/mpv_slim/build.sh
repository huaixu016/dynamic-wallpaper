#!/usr/bin/env bash
set -Eeuo pipefail

cd /work
printf '\n=== Preparing independent source profile ===\n'
python /profile/prepare.py /toolchain /work/toolchain-src

git config --global user.name "Local mpv build"
git config --global user.email "local-mpv-build@localhost"
git config --global http.version HTTP/1.1

# Limit nested Ninja invocations as well as the outer dependency graph.
real_ninja=$(command -v ninja)
mkdir -p /work/bin
printf '#!/bin/sh\nexec %s -j%s "$@"\n' "$real_ninja" "${BUILD_JOBS:-2}" > /work/bin/ninja
chmod +x /work/bin/ninja
export PATH="/work/bin:$PATH"
export NINJA_STATUS='[%f/%t %e sec] '

printf '\n=== Configuring x64 UCRT toolchain ===\n'
cmake -G Ninja -S /work/toolchain-src -B /work/build \
    -DTARGET_ARCH=x86_64-w64-mingw32 \
    -DGCC_ARCH=x86-64 \
    -DMAKEJOBS="${BUILD_JOBS:-2}" \
    -DSINGLE_SOURCE_LOCATION=/work/sources \
    -DRUSTUP_LOCATION=/work/unused-rust \
    -DENABLE_CCACHE=OFF \
    '-DGCC_FLAGS=-ffunction-sections -fdata-sections -static-libgcc -static-libstdc++' \
    -DBFD_FLAGS=--gc-sections

printf '\n=== Building MinGW-w64 compiler (cached after first build) ===\n'
ninja -C /work/build gcc
printf '\n=== Building wallpaper-only libmpv ===\n'
ninja -C /work/build mpv
printf '\n=== Exporting candidate DLL and notices ===\n'
python /profile/export.py /work/build /work/output
printf '\n=== Build finished; active Windows runtime was not modified ===\n'
