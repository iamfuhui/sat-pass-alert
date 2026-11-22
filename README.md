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

需要 Docker Engine 和 Docker Compose v2。建议先从源码构建。

```sh
git clone https://github.com/iamfuhui/sat-pass-alert.git
cd sat-pass-alert
cp .env.example .env
```

在 `.env` 中填写以下设置。模板中的示例密码需要替换。

```dotenv
APP_SECRET_KEY=
ADMIN_USERNAME=admin
ADMIN_PASSWORD=
DB_PASSWORD=
DB_ROOT_PASSWORD=
SPA_BIND_IP=127.0.0.1
SPA_PORT=18473
COOKIE_SECURE=true
```

为应用和数据库生成不同的随机值。应用密钥至少 32 个字符，初始管理员密码至少 12 个字符。内置 MariaDB 的密码建议使用 URL 安全字符。

```sh
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
docker compose up -d --build
docker compose ps
curl -fsS http://127.0.0.1:18473/healthz
```

默认 Compose 启动应用和独立的 MariaDB 数据卷。应用启动时会运行 Alembic 迁移。通过 HTTPS 反向代理将站点转发到宿主机的 `http://127.0.0.1:18473`，再从 HTTPS 站点登录。按需配置 `TRUSTED_PROXY_IPS`，只列出实际可信的代理地址。

如果已有专用 MariaDB 数据库，可使用 `compose.external-db.yaml`。将 `DATABASE_URL` 指向已创建的数据库，给应用账户迁移权限，并对连接串中的密码特殊字符做 URL 编码。

```sh
docker compose -f compose.external-db.yaml up -d --build
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
