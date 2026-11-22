# SPA — Sat Pass Alert

SPA 是面向业余无线电爱好者的自托管卫星过境预报与提醒工具。设置观测站和卫星订阅后，可查看下一次过境的升起时间、最高仰角、落下时间和天空轨迹，并在卫星升起前接收提醒。

Self-hosted satellite pass forecasts and notifications for amateur radio operators.

アマチュア無線向けのセルフホスト型衛星パス予報・通知ツールです。

## 当前功能

- **观测站**支持经纬度、海拔、显示时区以及 4、6、8 位 Maidenhead 网格定位。
- **过境预报**支持未来 24、48 或 72 小时，展示 AOS、LOS、最高仰角和天空轨迹。可按卫星与最高仰角筛选。
- **卫星目录**支持从 CelesTrak 或 AMSAT 获取轨道，也可手动导入 TLE 或 OMM。后台每 6 小时检查轨道更新。
- **提醒渠道**包括 Bark、Server 酱 Turbo、PushPlus、Telegram、ntfy、群机器人 Webhook 和自定义 Webhook。每个用户独立配置渠道、订阅和提前提醒时间。
- **AMSAT 状态**来自公开报告，每小时刷新，供操作参考。
- **账号管理**默认关闭注册。管理员可启用邀请码或审批注册。推送凭据加密存储。

### 语言支持

支持**简体中文、English 和日本語**。从页面右上角切换语言。登录后，语言偏好保存在账户中，测试通知和后续卫星过境提醒会使用该语言。部分服务端错误与第三方渠道返回的原始信息仍可能显示中文或上游原文。

## Docker Compose 快速开始

默认部署只运行一个 SPA 容器。SQLite 数据库位于 `/app/data/spa.db`，由 `spa_data` 数据卷持久化，启动时自动执行迁移。需要 Docker Engine 和 Docker Compose v2。

```sh
mkdir -p sat-pass-alert
cd sat-pass-alert
curl -fsSLO https://raw.githubusercontent.com/iamfuhui/sat-pass-alert/v0.1.0-alpha.1/compose.yaml
```

在 `.env` 中保存以下配置，为应用密钥和管理员密码分别生成随机值（例如各运行一次 `openssl rand -hex 32`）。应用密钥至少 32 个字符，初始管理员密码至少 12 个字符。

```dotenv
SPA_IMAGE_TAG=0.1.0-alpha.1
ADMIN_USERNAME=admin
ADMIN_PASSWORD=
APP_SECRET_KEY=
```

```sh
chmod 600 .env
docker compose pull
docker compose up -d --no-build
curl -fsS http://127.0.0.1:18473/healthz
```

SPA 默认绑定 `127.0.0.1:18473` 并启用 Secure Cookie。配置 HTTPS 反向代理到该端口后，从 HTTPS 站点登录。按需设置 `SPA_PORT` 和 `TRUSTED_PROXY_IPS`。

镜像支持 `linux/amd64` 和 `linux/arm64`。单独拉取镜像可用：

```sh
docker pull ghcr.io/iamfuhui/sat-pass-alert:0.1.0-alpha.1
```

### 可选：MariaDB

**已有 MariaDB 部署升级时，请继续使用原数据库和 Compose 项目名。** SQLite 默认配置会启动一个新的数据库，切换数据库类型需要单独迁移数据。

- **内置 MariaDB**：下载 [compose.mariadb.yaml](https://raw.githubusercontent.com/iamfuhui/sat-pass-alert/v0.1.0-alpha.1/compose.mariadb.yaml)，保留或填写 `DB_PASSWORD` 与 `DB_ROOT_PASSWORD`，并显式设置 `SPA_BIND_IP=127.0.0.1` 和 `COOKIE_SECURE=true`。更新 `SPA_IMAGE_TAG` 后，在原项目目录运行：

  ```sh
  docker compose -f compose.mariadb.yaml pull
  docker compose -f compose.mariadb.yaml up -d --no-build
  ```

- **外置 MariaDB**：下载 [compose.external-db.yaml](https://raw.githubusercontent.com/iamfuhui/sat-pass-alert/v0.1.0-alpha.1/compose.external-db.yaml)，设置指向专用数据库的 `DATABASE_URL`、`SPA_BIND_IP=127.0.0.1` 和 `COOKIE_SECURE=true`。使用 `docker compose -f compose.external-db.yaml pull` 和 `docker compose -f compose.external-db.yaml up -d --no-build`。数据库账户需要迁移权限，连接串中密码的特殊字符需要 URL 编码。

### 内存上限

SQLite 方案支持在 `.env` 设置 `SPA_MEMORY_LIMIT`（例如 `256m`）。默认值 `0` 由宿主机管理内存。设置上限后重新创建应用容器，并以实际订阅数量、预报请求量和后台刷新负载验证。

Mac mini（ARM64）上的 OrbStack 实测使用 `256m` 上限。12 颗订阅、4 路并发，共 8 个 72 小时预报请求时，容器内存工作集峰值约 92 MiB，包含文件缓存的峰值约 104 MiB。此结果仅对应所测负载。

```sh
docker compose up -d
docker stats --no-stream "$(docker compose ps -q spa)"
```

### 运行中备份与恢复到新卷

在应用运行时使用 SQLite 在线备份生成一致快照，包含已提交的数据。备份包含账户和推送配置，请限制访问权限，并单独安全备份 `APP_SECRET_KEY` 或包含它的 `.env`。

```sh
umask 077
backup="spa-$(date +%Y%m%d-%H%M%S).sqlite3"
docker compose exec -T spa python -m app.sqlite_backup backup > "$backup.partial" &&
  mv "$backup.partial" "$backup"
```

恢复时使用备份对应的 `APP_SECRET_KEY`。以下命令将备份恢复到独立项目的新数据卷，并通过另一个本机端口验收。恢复命令会校验备份完整性，遇到已有目标数据库时会退出。

```sh
docker compose -p spa-restore run --rm -T --no-deps spa python -m app.sqlite_backup restore < "$backup"
WORKER_ENABLED=false SPA_PORT=18474 docker compose -p spa-restore up -d --wait
curl -fsS http://127.0.0.1:18474/healthz
```

临时验收时，建议在启动恢复实例前设置 `WORKER_ENABLED=false`，避免它使用恢复的凭据重复发送真实提醒。可通过与恢复端口对应的 HTTPS 入口登录核对订阅与推送配置。确认恢复结果后，再按实际切换计划启用 Worker。

## 第一次使用

1. 用初始管理员账号登录，在系统管理中添加卫星。可用 ISS 的 NORAD 编号 `25544` 开始。
2. 设置实际观测位置、海拔和时区。
3. 启用卫星订阅，设定最低峰值仰角和提前提醒时间。
4. 配置并启用至少一个推送渠道，保存后发送测试通知，在设备上确认到达。
5. 查看过境预报和通知记录，确认下一次过境与提醒计划。

## 运行与维护

- 当前运行方式为单个应用实例和单个 Uvicorn worker。调度器会定期计算过境并安排提醒。
- 轨道会随时间失准。默认预测窗口不得偏离轨道历元超过 14 天。
- 上游接受推送和手机实际收到是两个验收步骤。网络超时后的重试可能产生重复通知。
- 升级前备份 SQLite 数据库或 MariaDB 数据卷，并安全保存 `APP_SECRET_KEY`。更换密钥会使现有会话和加密保存的推送凭据失效。
- 健康检查位于 `/healthz`，OpenAPI 文档位于 `/docs`。

## 本地测试

需要 Python 3.12+ 和 Node.js 22+。在 Python 虚拟环境中执行后端测试：

```sh
pip install -r requirements-dev.txt
python -m pytest -q
```

前端测试和构建：

```sh
cd frontend
npm ci
npm test
npm run build
```

后端测试默认使用临时 SQLite 数据库。MariaDB 集成测试通过 `TEST_DATABASE_URL` 指定专用测试库。测试会删除并重建该库中的应用表，请使用可清空的测试数据库。

项目使用 FastAPI、Vue 3、SQLAlchemy、Alembic、MariaDB 和 Skyfield/SGP4。以 [MIT License](LICENSE) 发布。

[Skyfield 卫星计算](https://rhodesmill.org/skyfield/earth-satellites.html) · [CelesTrak](https://celestrak.org/) · [AMSAT 公众状态](https://status.amsat.org/)
