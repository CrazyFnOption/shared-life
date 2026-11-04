# 同在 · Shared Life

一款可自建的共同生活 PWA，把共同记账、生活备忘、愿望与计划放在同一个入口。适合个人和受邀家庭成员使用。

<img src="docs/images/home.png" alt="同在首页" width="240"> <img src="docs/images/memos.png" alt="生活备忘与日程" width="240"> <img src="docs/images/editor.png" alt="单区 Markdown 编辑" width="240">

截图使用专属演示数据。

## 第一版可以做什么

- 私人和家庭内容分开；支持多个家庭、邀请、退出、移交管理员和解散家庭。
- 共同记账：支出记录、分类、图片、退款、更正请求及统计。金额用整数分计算，统计使用已同步净额，退款归原消费月份；当前只支持人民币。
- 生活备忘：记录、附件、日程、提醒、分类与关注。
- 愿望与计划：手动整理计划与任务，将任务加入生活备忘关注并共用完成状态。
- 本机优先保存并显示同步状态；首次联网准备好应用资源后，可使用已支持的离线记录能力。

愿望 AI 规划和月度 AI 财务分析留给第二版；第一版无需模型密钥。详见 [路线图](ROADMAP.md)。

## 快速开始

需要 Python 3.13、uv，以及 Node.js 24.12+（或满足依赖要求的 Node.js 22.18+）。在项目根目录执行：

```sh
uv sync --locked
./scripts/web.sh ci
./scripts/web.sh run build
uv run python -m scripts.seed_demo --database .local/dev.sqlite3
uv run python -m server.main
```

打开 <http://127.0.0.1:8765>。演示账号为 `demo-a`、`demo-b`，密码均为 `demo-only-2026`，只用于本机虚构数据；演示数据库已存在时种子脚本会拒绝覆盖。再次启动只需执行最后一条命令。独立注册的账号默认只有私人内容，主动建家或接受邀请才加入家庭。

部署给真实成员前，应创建全新数据库和自己的账号，不开放演示账本。使用 HTTPS 反向代理并配置准确的允许来源及安全 Cookie；PWA 安装、Service Worker 和系统通知在远程访问时需要安全上下文。通用容器模板默认仅绑定本机端口：

```sh
docker compose up --build -d
```

容器数据保存在命名卷中；首次运行没有预置账号。模板默认关闭真实模型调用、推送与自动备份。`.env.example` 仅为进程环境参考，应用不会自动加载该文件。容器模式应在 `compose.yaml` 中显式配置需要的环境变量。

## 使用与数据边界

私人内容仅本人可见，家庭内容按具体家庭隔离。共同账目由原付款人维护，其他成员可提交更正请求；共同备忘与计划支持家庭成员编辑，删除由创建者决定。家庭历史的可见性随成员关系变化。失权后重新同步会撤回共同访问，已经看过或离线保存的信息不能立即远程抹除。

“已保存到本机”和“已同步到服务端”是不同状态。请保留未同步内容；清除网站数据会造成未同步记录丢失。关闭应用后的后台同步和提醒送达取决于设备与系统，不能保证。系统通知需要服务器配置和每台设备主动授权，真实手机送达、冷启动与网络切换应由部署者验证。

## 开发检查

```sh
uv run pytest server/tests server/plans/test_service.py tests/integration/test_family_migration.py tests/integration/test_family_admin_migration.py -q
python3 -m unittest discover -s tests/release -v
./scripts/web.sh test
./scripts/web.sh run build
```

这组命令覆盖后端模块、真实旧版本源码构造的虚构数据库迁移／恢复、计划模块、前端状态及生产构建。历史测试源码夹具随仓库提供并逐文件校验，不需要维护者的私人 Git 历史。它不代替完整浏览器、真机和生产恢复验收，也不承诺旧程序可以回退读取当前数据库。

页面开发可为后端允许来源增加 `http://127.0.0.1:5173`，再另开终端执行 `./scripts/web.sh run dev`；离线验收应使用生产构建。

兼容更新的参数化打包、保护备份、切换与资源核验见[发布说明](docs/releasing.md)。真实环境配置保存在仓库之外，不提交到公开项目。

## 设计取舍与限制

Vue / TypeScript 前端（Markdown 编辑器使用 CodeMirror 6）、FastAPI 后端、SQLite 数据库，采用单进程服务。依赖版本以 `web/package-lock.json` 与 `uv.lock` 为准，前述安装和 CI 命令会安装完整编辑器依赖。保存、同步、金额计算与权限边界优先于功能数量。项目仍在早期维护阶段，不提供托管服务或响应时效承诺，也不是支付、AA 结算或银行账单导入工具。

生产部署自行承担域名、TLS、访问控制、备份和恢复验证。数据库升级前应保留受保护备份；先在隔离副本验证，不能用旧程序直接写新结构，也不能用旧备份覆盖日常新增记录。

## 参与与许可

欢迎提供可复现的问题和小范围改进，见 [贡献说明](CONTRIBUTING.md)。请勿在 Issue 中上传真实账本、账号会话或私人附件；漏洞报告见 [安全政策](SECURITY.md)。

项目原创代码和原创图标采用 [MIT](LICENSE) 许可；第三方依赖保留各自许可，见 [第三方说明](THIRD_PARTY_NOTICES.md)。
