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

需要 Docker Engine 和 Docker Compose v2。已发布 `linux/amd64` 和 `linux/arm64` 镜像，Docker 会自动选择当前机器的架构。

```sh
docker pull ghcr.io/iamfuhui/sat-pass-alert:0.1.0-alpha
```

### 使用内置 MariaDB

在一个新的部署目录中下载与镜像版本匹配的 Compose 文件，再创建 `.env`：

```sh
mkdir -p sat-pass-alert
cd sat-pass-alert
curl -fsSLO https://raw.githubusercontent.com/iamfuhui/sat-pass-alert/v0.1.0-alpha/compose.yaml
```

将以下内容保存为 `.env`，为四个空值各生成一个不同的随机值。可以运行 `openssl rand -hex 32` 生成随机值。应用密钥至少 32 个字符，初始管理员密码至少 12 个字符。

```dotenv
SPA_IMAGE_TAG=0.1.0-alpha
SPA_BIND_IP=127.0.0.1
SPA_PORT=18473
COOKIE_SECURE=true
ADMIN_USERNAME=admin
APP_SECRET_KEY=
ADMIN_PASSWORD=
DB_PASSWORD=
DB_ROOT_PASSWORD=
```

```sh
chmod 600 .env
docker compose pull
docker compose up -d --no-build
docker compose ps
curl -fsS http://127.0.0.1:18473/healthz
```

Compose 会启动 SPA 和独立的 MariaDB 数据卷。应用启动时会自动运行 Alembic 迁移。配置 HTTPS 反向代理，将请求转发到宿主机的 `http://127.0.0.1:18473`，然后使用 `.env` 中的管理员凭据从 HTTPS 入口登录。仅在需要解析代理转发头时设置 `TRUSTED_PROXY_IPS`，并填写实际可信的代理地址。

### 使用现有 MariaDB

如果已有专用 MariaDB 数据库，在 `.env` 中保留镜像、入口和应用配置，另设 `DATABASE_URL=mysql+pymysql://用户:密码@数据库主机:3306/数据库名?charset=utf8mb4`。提前创建数据库、授予应用账户迁移权限，并对连接串中的密码特殊字符做 URL 编码。

```sh
curl -fsSLO https://raw.githubusercontent.com/iamfuhui/sat-pass-alert/v0.1.0-alpha/compose.external-db.yaml
docker compose -f compose.external-db.yaml pull
docker compose -f compose.external-db.yaml up -d --no-build
```

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
- 升级前备份 MariaDB 卷及 `APP_SECRET_KEY`。更换密钥会使现有会话和加密保存的推送凭据失效。
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
