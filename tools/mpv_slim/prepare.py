"""Apply a wallpaper-only build profile to an independent toolchain copy."""

import argparse
from pathlib import Path
import shutil

PACKAGES = (
    "zlib", "libpng", "libjpeg", "freetype2", "harfbuzz", "fribidi", "libiconv",
    "libunibreak", "libass", "lcms2", "xxhash", "dav1d", "luajit",
    "spirv-headers", "spirv-tools", "glslang", "shaderc", "spirv-cross",
    "vulkan-header", "glad", "fast_float", "libplacebo", "ffmpeg", "mpv",
)

FFMPEG = r'''ExternalProject_Add(ffmpeg
    DEPENDS zlib dav1d
    GIT_REPOSITORY https://github.com/FFmpeg/FFmpeg.git
    GIT_TAG release/8.0
    GIT_SHALLOW TRUE
    SOURCE_DIR ${SOURCE_LOCATION}
    UPDATE_COMMAND ""
    CONFIGURE_COMMAND ${EXEC} CONF=1 <SOURCE_DIR>/configure
        --cross-prefix=${TARGET_ARCH}-
        --prefix=${MINGW_INSTALL_PREFIX}
        --arch=${TARGET_CPU}
        --target-os=mingw32
        --pkg-config-flags=--static
        --enable-cross-compile
        --enable-runtime-cpudetect
        --enable-static
        --disable-shared
        --disable-autodetect
        --enable-gpl
        --enable-version3
        --enable-small
        --disable-debug
        --disable-doc
        --disable-programs
        --disable-network
        --disable-protocols
        --enable-protocol=file
        --disable-devices
        --disable-avdevice
        --disable-encoders
        --enable-encoder=png
        --disable-muxers
        --enable-muxer=image2,image2pipe
        --enable-zlib
        --enable-libdav1d
        --enable-d3d11va
        --enable-dxva2
        --optflags=-Os
        --extra-cflags=-Wno-error=int-conversion
    BUILD_COMMAND ${MAKE}
    INSTALL_COMMAND ${MAKE} install
    LOG_DOWNLOAD 1 LOG_UPDATE 1 LOG_CONFIGURE 1 LOG_BUILD 1 LOG_INSTALL 1
)
force_rebuild_git(ffmpeg)
'''

MPV = r'''ExternalProject_Add(mpv
    DEPENDS ffmpeg libass libplacebo libiconv libjpeg zlib luajit lcms2 shaderc spirv-cross
    GIT_REPOSITORY https://github.com/mpv-player/mpv.git
    GIT_TAG v0.42.0
    GIT_SHALLOW TRUE
    SOURCE_DIR ${SOURCE_LOCATION}
    UPDATE_COMMAND ""
    CONFIGURE_COMMAND ${EXEC} CONF=1 meson setup <BINARY_DIR> <SOURCE_DIR>
        --prefix=${MINGW_INSTALL_PREFIX}
        --libdir=${MINGW_INSTALL_PREFIX}/lib
        --cross-file=${MESON_CROSS}
        --default-library=shared
        --prefer-static
        --buildtype=release
        -Doptimization=s
        -Db_ndebug=true
        -Db_lto=true
        -Dauto_features=disabled
        -Dcplayer=false
        -Dlibmpv=true
        -Dbuild-date=false
        -Dtests=false
        -Dfuzzers=false
        -Dlua=luajit
        -Diconv=enabled
        -Djpeg=enabled
        -Dlcms2=enabled
        -Dzlib=enabled
        -Dvector=enabled
        -Dwin32-threads=enabled
        -Dwasapi=enabled
        -Dshaderc=enabled
        -Dspirv-cross=enabled
        -Dd3d11=enabled
        -Dd3d-hwaccel=enabled
        -Dd3d9-hwaccel=enabled
        -Dgl=disabled
        -Dvulkan=disabled
        -Dwin32-smtc=disabled
        -Dc_link_args=-Wl,--gc-sections,-lstdc++
    BUILD_COMMAND ${EXEC} LTO_JOB=1 ninja -C <BINARY_DIR> libmpv-2.dll
    INSTALL_COMMAND ${EXEC} ${TARGET_ARCH}-strip --strip-unneeded <BINARY_DIR>/libmpv-2.dll
    LOG_DOWNLOAD 1 LOG_UPDATE 1 LOG_CONFIGURE 1 LOG_BUILD 1 LOG_INSTALL 1
)
force_rebuild_git(mpv)
'''


def replace(path: Path, before: str, after: str) -> None:
    text = path.read_text(encoding="utf-8")
    if before not in text:
        raise RuntimeError(f"Toolchain layout changed: cannot locate expected text in {path}")
    path.write_text(text.replace(before, after), encoding="utf-8", newline="\n")


def prepare(source: Path, destination: Path) -> None:
    if source.resolve() == destination.resolve():
        raise ValueError("Refusing to modify the original toolchain checkout")
    shutil.copytree(source, destination, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns(".git", "__pycache__"))
    for path in destination.rglob("*"):
        if path.is_file() and (path.suffix in {".cmake", ".in", ".patch", ".sh"}
                               or path.name == "CMakeLists.txt"):
            data = path.read_bytes()
            if b"\r\n" in data:
                path.write_bytes(data.replace(b"\r\n", b"\n"))
    packages = destination / "packages"
    # Each recipe expects SOURCE_LOCATION to point at a package-specific source cache.
    includes = "\n".join(
        f'set(SOURCE_LOCATION "${{SINGLE_SOURCE_LOCATION}}/{name}")\n'
        f'include(${{CMAKE_CURRENT_SOURCE_DIR}}/{name}.cmake)' for name in PACKAGES
    )
    source_manifest = "\n".join(
        f'get_property(src TARGET {name} PROPERTY _EP_SOURCE_DIR)\n'
        f'file(APPEND "${{CMAKE_BINARY_DIR}}/slim-sources.tsv" "{name}\\t${{src}}\\n")'
        for name in (*PACKAGES, "gcc", "gcc-binutils", "mingw-w64", "winpthreads")
    )
    (packages / "CMakeLists.txt").write_text(
        'set_property(DIRECTORY PROPERTY EP_STEP_TARGETS download)\n'
        'include(${PROJECT_SOURCE_DIR}/cmake/packages_check.cmake)\n'
        + includes + '\nfile(WRITE "${CMAKE_BINARY_DIR}/slim-sources.tsv" "")\n'
        + source_manifest + "\n", encoding="utf-8", newline="\n")
    (packages / "ffmpeg.cmake").write_text(FFMPEG, encoding="utf-8", newline="\n")
    (packages / "mpv.cmake").write_text(MPV, encoding="utf-8", newline="\n")
    for name in ("spirv-cross", "luajit"):
        replace(packages / f"{name}.cmake",
                f"PATCH_COMMAND ${{EXEC}} git am --3way ${{CMAKE_CURRENT_SOURCE_DIR}}/{name}-*.patch",
                f"PATCH_COMMAND ${{EXEC}} python /profile/patches.py {name} <SOURCE_DIR> ${{CMAKE_CURRENT_SOURCE_DIR}}")
    replace(packages / "freetype2.cmake", "        brotli\n", "")
    replace(packages / "freetype2.cmake", "-Dbrotli=enabled", "-Dbrotli=disabled")
    replace(packages / "libass.cmake", "        fontconfig\n", "")
    replace(packages / "libass.cmake", "-Dfontconfig=enabled", "-Dfontconfig=disabled")
    replace(packages / "libplacebo.cmake", "        vulkan\n", "        vulkan-header\n")
    replace(packages / "libplacebo.cmake",
            "        -Dvulkan-registry='${MINGW_INSTALL_PREFIX}/share/vulkan/registry/vk.xml'\n", "")
    replace(packages / "libplacebo.cmake", "        -Ddemos=false",
            "        -Dvulkan=disabled\n        -Dopengl=disabled\n"
            "        -Dshaderc=enabled\n        -Dglslang=disabled\n"
            "        -Dlcms=enabled\n        -Dlibdovi=disabled\n"
            "        -Dunwind=disabled\n        -Ddemos=false\n        -Dtests=false")
    replace(destination / "toolchain/gcc/gcc.cmake", "        rustup\n", "")
    replace(destination / "toolchain/gcc/gcc.cmake", "        cppwinrt\n", "")
    # Keep intermediate objects and source revisions so a failed build can resume safely.
    steps = destination / "cmake/custom_steps.cmake"
    text = steps.read_text(encoding="utf-8")
    end = text.index("endfunction()") + len("endfunction()")
    text = 'function(cleanup _name _last_step)\nendfunction()\n' + text[end:]
    start = text.index("function(force_rebuild_git ")
    end = text.index("endfunction()", start) + len("endfunction()")
    # Pinned local builds must not touch download stamps and replay git-am patches on every resume.
    text = text[:start] + 'function(force_rebuild_git _name)\nendfunction()' + text[end:]
    steps.write_text(text, encoding="utf-8", newline="\n")
    print(f"Prepared {len(PACKAGES)} dependency recipes in {destination}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    prepare(args.source, args.destination)
