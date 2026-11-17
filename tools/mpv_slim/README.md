# 精简 libmpv 的 Docker 构建

用于构建当前壁纸程序的 Windows x64 候选 DLL，**不会替换正在使用的播放器运行库**。配置在独立的工具链副本上应用，原始 [mpv-winbuild-cmake](../../vendor/mpv-winbuild-cmake/) 仓库以只读方式挂载。

## 保留与裁剪

- 保留 libmpv API、窗口嵌入、本地文件、FFmpeg 内置解码器和 demuxer、dav1d AV1 解码。
- 保留 D3D11、D3D11VA/DXVA2、WASAPI 音频输出、GPU shader、色彩处理。
- 保留 Lua：当前程序初始化时设置 `osc=no`、`ytdl=no`、`load-scripts=no`，这些选项依赖 Lua 编译支持。程序运行时仍禁用脚本。
- 保留 libass 和 libplacebo：mpv 0.42 将其列为强制依赖，不能通过一个禁用选项删除。
- 删除视频编码器、网络协议、光盘/压缩包播放、其他图形后端、外部滤镜和 Opus DNN 等附加依赖。仅保留 Vulkan 头文件，用于 libplacebo 在禁用 Vulkan 后端时仍需编译的公共兼容接口；不构建 Vulkan loader 或启用 Vulkan 后端。
- PNG 编码器仅保留用于原生像素回归测试；不对用户视频重新编码或降低分辨率。

mpv 固定为 `v0.42.0`，FFmpeg 使用 `release/8.0` 分支；其他依赖采用工具链当前配置。构建后记录各仓库实际提交，后续发布应依据提交散列固定来源，并审查安全更新。

## 启动

在项目根目录运行，Docker Desktop 必须使用 Linux 容器：

```powershell
docker pull ghcr.io/shinchiro/archlinux:latest
python tools/mpv_slim/run.py start
```

容器名称为 `dynamic-wallpaper-mpv-slim-builder`，工作卷为 `dynamic-wallpaper-mpv-slim-work`。源码缓存、MinGW 工具链和编译中间文件都位于这个 Linux 卷，避免在 Windows 挂载盘上大量读写及处理符号链接。容器限制为 3 CPU、5 GiB 内存，内外层编译并行数默认为 2。

首次编译会生成 MinGW-w64 工具链并下载依赖，需要数 GB 空间和较长时间；失败后缓存保留，不会影响其他容器或现有 Ubuntu 环境。新建构建容器时会继承 Docker 引擎配置的 HTTP/HTTPS 代理，避免出现引擎能拉镜像、容器却无法克隆源码的路由差异；已有容器保留创建时的环境设置。代理地址不会打印到构建启动日志。

部分网络环境对 Git 的后续对象请求不稳定；遇到 `CONNECT tunnel failed` 或 `could not fetch ... from promisor remote` 时，应先检查代理与直连是否可用，并补齐中断的源码检出，再继续构建，不必删除整个工具链缓存。

此配置保留已完成的下载和补丁时间戳，不运行上游工具链的自动 Git 更新/补丁回放逻辑，避免重启时对已修改的源码再次执行同一补丁。需要更新依赖版本时，应显式调整对应版本并清理受影响包的步骤，而不是对整个缓存执行自动更新。

```powershell
docker logs --tail 60 dynamic-wallpaper-mpv-slim-builder
docker wait dynamic-wallpaper-mpv-slim-builder
```

`docker wait` 打印的是容器退出码；打印 `0` 才表示构建脚本完成。失败时应检查相应 `*-err.log` 和 `*-out.log`，修复配置后执行 `python tools/mpv_slim/run.py start` 重新启动同一容器。

## 导出与验证

成功后：

```powershell
python tools/mpv_slim/run.py collect
python -m dynamic_wallpaper --diagnose --mpv-library vendor/mpv-slim-candidate/libmpv-2.dll
```

候选目录包含 DLL、实际源代码提交、构建配置和许可证文件。只有原生测试、常见编码测试及打包独立启动校验通过后，才应考虑替换 [vendor/mpv/](../../vendor/mpv/) 中的原库。

用现有完整运行库生成静音的 H.264/AAC、HEVC/AAC、VP9/Opus、AV1/Opus 测试片，然后验证候选库及整个原生回归测试集：

```powershell
python tools/mpv_slim/fixtures.py .integration-test/mpv-slim-validation
python tools/mpv_slim/validate.py vendor/mpv-slim-candidate/libmpv-2.dll
```

测试片是临时合成数据，不读取或压缩用户视频。验证只加载候选库，不替换部署目录；结果写入候选目录的 `windows-validation.json`。

```powershell
$env:DYNAMIC_WALLPAPER_MPV_DLL = (Resolve-Path vendor/mpv-slim-candidate/libmpv-2.dll).Path
$env:DYNAMIC_WALLPAPER_NATIVE_TESTS = "1"
python -m unittest discover -s tests -v
Remove-Item Env:DYNAMIC_WALLPAPER_MPV_DLL
Remove-Item Env:DYNAMIC_WALLPAPER_NATIVE_TESTS
```

候选库仍为 GPL 构建。保留许可证文件不等于已完成全部分发义务；对外分发前须审查各静态链接依赖的许可证及对应源码提供要求。

目前这里只优化媒体运行库。即使 DLL 缩小，完整程序目录能否低于 100 MB 仍取决于后续 Qt 裁剪，不能只凭 DLL 体积保证。
