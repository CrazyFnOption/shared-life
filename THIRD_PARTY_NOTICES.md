# 第三方依赖

本项目 MIT 许可仅覆盖项目原创内容，不替代依赖各自的许可。

前端主要依赖 Vue、markdown-it、CodeMirror 6 与 Lezer 解析器（MIT）及 DOMPurify（MPL-2.0 OR Apache-2.0）。Python 主要依赖 FastAPI、argon2-cffi、lunar-python（MIT），Uvicorn（BSD-3-Clause），Pillow（MIT-CMU）与 pywebpush（MPL-2.0）。锁文件固定具体版本与传递依赖。

已收集的安装包许可文本保存在 `third-party-licenses/`，原文中的作者和版权声明保留。DOMPurify 保留其双许可选择，未将第三方文件改标为项目 MIT。pywebpush、py-vapid、certifi 和构建工具 lightningcss 含 MPL-2.0 许可，相关依赖仍各自保持原许可。

首发提供项目源码，不附依赖二进制或容器镜像。自行分发包含第三方依赖的构建、wheel、容器镜像或其他二进制时，应同时保留适用的版权、许可和 NOTICE，满足有关源代码提供义务；不能只附本项目 LICENSE。仓库锁文件中的包、安装后的具体平台组件及最终分发产物可能不同，发行者应按实际内容复核。

许可要求参考：[Mozilla MPL 2.0 FAQ](https://www.mozilla.org/en-US/MPL/2.0/FAQ/) 说明文件级许可边界及分发可执行程序时的源码告知；[Apache 2.0 第4条](https://www.apache.org/licenses/LICENSE-2.0) 说明许可副本、归属与适用 NOTICE 的保留。
