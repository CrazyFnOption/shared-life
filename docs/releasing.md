# 参数化兼容更新

`python3 scripts/release.py` 固化既有单服务 Docker 更新流程：固定源码、构建、校验镜像、保护备份、切换程序，并核对健康状态与实际资源。它适用于已建立的 schema9／format2 运行环境，不是通用安装器或数据库迁移工具。新部署先使用 README 的容器模板。

实际主机、SSH、路径、镜像基线、数据卷及允许来源放在被 Git 忽略且权限为 `0600` 的本地配置中。参考 `scripts/release/config.example.json`，不要提交真实配置、密钥、备份或运行日志。推送配置按现有运行环境核对，发布不能代替每台设备的通知授权。

## 固定候选与准备

先提交并完成对应验收，再填入已核实的运行基线。源码和资源从指定 Git 提交产生，不使用工作区里未提交的文件或现有 dist。输出使用全新目录：

```sh
python3 scripts/release.py prepare --commit CANDIDATE --baseline LIVE_BASELINE \
  --config .local/release/config.json --output .local/release/NEW_OUTPUT
python3 scripts/release.py plan --config .local/release/config.json \
  --output .local/release/NEW_OUTPUT
```

`prepare` 只在本地执行，生成源码包、构建上下文、SHA 清单和日志；资源数量自动统计，包括异步编辑器的 JS／CSS。npm 使用已有离线缓存；Python 离线 wheelhouse 需由部署者事先准备并核对。当前构建驱动复用既有 Docker 依赖缓存，缓存不足时可能需要正常的仓库网络访问；工具不会自动调整主机网络或升级依赖。

## 兼容边界

默认只接受 Vue／CSS、静态资源与前端测试变化，项目自身版本号另做严格语义比较。数据库结构、存储协议、后端依赖、构建脚本等变化会阻断，不能用此入口执行迁移。

配置中的 `reviewed_runtime_changes` 可承载经过独立审查的精确兼容修复，目前限定备忘服务、计划服务及旧版兼容守卫三个文件；必须绑定完整基线／候选提交、路径、旧新 SHA 和不变模式。守卫例外仅用于当前 schema9 的兼容程序更新，不授予旧库升级权限。

`reviewed_frontend_dependency_additions` 绑定两个 web 包文件及其精确提交、SHA 和模式；脚本还会证明仅新增精确版本，旧依赖声明、旧锁记录、构建脚本和其他元数据保持。它不允许升级现有依赖或使用本地链接包。

这些输入是可核验的审查记录，不能自行提供操作授权。提交、文件或配置变化后需重新核对；工具没有跳过检查的选项。

## 执行与续做

核对目标和有效操作权限后，显式执行：

```sh
python3 scripts/release.py execute --config .local/release/config.json \
  --output .local/release/NEW_OUTPUT --approve FULL_PREPARED_COMMIT
python3 scripts/release.py status --config .local/release/config.json \
  --output .local/release/NEW_OUTPUT
```

程序保持原 Compose 项目、数据卷、环境、通知配置、用户、端口、自启和默认网络，先保护一致性备份再切换。网络校验接受默认空 IPAM，仍拒绝自定义／外部网络及运行契约漂移。

状态保存在本地和目标主机。重复执行先查询同一运行，完成的上传、构建、备份和切换不会重做；结果未知时先查 `status` 与同次日志。上传或备份的未知结果需人工核对，不能靠改 journal、删除锁或覆盖备份继续。工具不会自动恢复数据库、清理备份或回退不兼容程序。

发布后根据机器摘要记录新基线与备份位置。真实设备、系统通知和业务体验仍须单独验证；静态资源匹配不能代替它们。
