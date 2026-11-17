# libmpv 运行库

将可信来源的 **Windows x64 libmpv** 放到此目录，例如 `mpv-2.dll` 或 `libmpv-2.dll`。首版建议使用 mpv 0.38 或更新版本，并确保构建包含 FFmpeg/H.264、`vo=gpu` 与 D3D11 支持。

如果该构建带有其他依赖 DLL，应一起放到这个目录。`mpv.exe` 不能替代 libmpv DLL。程序不会自动下载、执行安装器或搜索整个系统的 DLL。

也可以通过 `--mpv-library D:\\path\\mpv-2.dll` 或环境变量 `DYNAMIC_WALLPAPER_MPV_DLL` 指定运行库。显式路径无效时不会悄悄回退到其他库。

参考：

- https://mpv.io/installation/
- https://github.com/mpv-player/mpv

分发前请审查所选构建的许可证。mpv 构建可能是 GPL 或 LGPL，FFmpeg 的编译选项也会影响分发义务。请随构建保留版权、许可证及相应源码/提供方式，不能把任意构建直接宣称为 LGPL。

本仓库不附带第三方二进制。
