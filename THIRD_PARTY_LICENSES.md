# 第三方软件声明（Third-Party Licenses）

本应用包含的第三方组件及许可信息。本应用主体以 Apache License 2.0 许可发布（见仓库 LICENSE 文件）。

## FFmpeg（含 ffprobe）

- **用途**：视频识别的分段采样与抽帧（设置 → 视觉 → 视频识别）。
- **许可**：GNU General Public License v3（GPL-3.0）。本应用随包分发的 ffmpeg/ffprobe 为 GPL 构建版本（启用了 GPL 组件编译）。
- **Windows 发布包**：静态构建，取自 [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds)（GPL 构建）。
- **macOS 发布包**：经 Homebrew 安装的 GPL 构建（编译参数含 `--enable-gpl`）。
- **源码获取**：依照 GPL-3.0 第 6(6) 条，完整对应源码可通过以下途径免费获取：
  - FFmpeg 官方源码仓库：<https://git.ffmpeg.org/ffmpeg.git>（或镜像 <https://github.com/FFmpeg/FFmpeg>）
  - Windows 分发所用构建仓库：<https://github.com/BtbN/FFmpeg-Builds>
  - 应用随附版本号可经 `ffmpeg -version` 查看；对应 tag 的源码包亦可在上述仓库按同版本 tag 获取。
- **许可文本**：GPL-3.0 全文见 <https://www.gnu.org/licenses/gpl-3.0.html>；源码分发时本文件即 fulfillment of the written offer。

> FFmpeg 本体可在 LGPL-2.1 下获得（不含 GPL 组件）；本发布包选用 GPL 构建以获得完整编码器支持。此分发不影响 Apache License 2.0 对应用其余部分的许可。

本声明随应用一同打包（设置 → 关于 → 第三方许可），任何修改后的再分发需保留本文件并维持相同的源码提供义务。
