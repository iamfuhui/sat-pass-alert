# SPA — Sat Pass Alert

自托管的业余卫星过境预报与提醒工具。设置观测站和卫星订阅，即可查看未来 24–72 小时的过境和天空轨迹，并按每颗卫星的仰角门槛与提前时间接收通知。

- 支持简体中文、English 和日本語。
- 支持经纬度、Maidenhead 网格定位、TLE/OMM 导入和轨道自动刷新。
- 推送渠道包括 Bark、Server 酱、PushPlus、Telegram、ntfy、群机器人和自定义 Webhook。

## Docker Compose 快速开始

需要 Docker Compose v2、`curl` 和 `openssl`。默认使用 SQLite，只运行一个应用容器，数据库自动迁移并保存在独立数据卷。

```sh
mkdir -p sat-pass-alert
cd sat-pass-alert
curl -fsSLO https://raw.githubusercontent.com/iamfuhui/sat-pass-alert/v0.1.0-alpha.1/compose.yaml

(
  set -euC
  umask 077
  if [ ! -e .env ]; then
    secret=$(openssl rand -hex 32)
    password=$(openssl rand -hex 16)
    printf 'SPA_IMAGE_TAG=0.1.0-alpha.1\nAPP_SECRET_KEY=%s\nADMIN_PASSWORD=%s\n' "$secret" "$password" > .env
  fi
)

docker compose pull && docker compose up -d --no-build --wait
curl -fsS http://127.0.0.1:18473/healthz
```

首次启动的用户名为 `admin`，密码见 `.env` 中的 `ADMIN_PASSWORD`。应用默认绑定 `127.0.0.1:18473` 并启用 Secure Cookie。公开访问请通过 HTTPS 反向代理转发到该端口。已有 `.env` 会被保留，升级时请先备份并手动更新 `SPA_IMAGE_TAG`。

**镜像**支持 `linux/amd64` 和 `linux/arm64`。也可以直接拉取：

```sh
docker pull ghcr.io/iamfuhui/sat-pass-alert:0.1.0-alpha.1
```

登录后，设置观测站、添加并订阅卫星，再保存推送渠道并发送测试通知。ISS 的 NORAD 编号为 `25544`。

## SQLite 备份与恢复

在线备份（同时另行安全备份 `.env` 中的 `APP_SECRET_KEY`）：

```sh
umask 077
backup="spa-$(date +%Y%m%d-%H%M%S).sqlite3"
docker compose exec -T spa python -m app.sqlite_backup backup > "$backup.partial" &&
  mv "$backup.partial" "$backup"
```

使用同一个 `APP_SECRET_KEY` 恢复到新项目和新数据卷。恢复工具会拒绝覆盖已有数据库。验收时关闭 Worker，避免重复发送提醒：

```sh
docker compose -p spa-restore run --rm -T --no-deps spa python -m app.sqlite_backup restore < "$backup"
WORKER_ENABLED=false SPA_PORT=18474 docker compose -p spa-restore up -d --no-build --wait
curl -fsS http://127.0.0.1:18474/healthz
```

可在 `.env` 设置 `SPA_MEMORY_LIMIT=256m` 作为小规模部署的可选上限，并根据实际负载调整。SPA 目前支持单应用实例。

## 可选：MariaDB

**已有 MariaDB 用户升级时，保留原项目名、数据库和 `APP_SECRET_KEY`。** 切换到 SQLite 需要单独迁移数据。

- **内置 MariaDB**：下载 [compose.mariadb.yaml](https://raw.githubusercontent.com/iamfuhui/sat-pass-alert/v0.1.0-alpha.1/compose.mariadb.yaml)，在 `.env` 设置 `DB_PASSWORD` 和 `DB_ROOT_PASSWORD`。
- **外置 MariaDB**：下载 [compose.external-db.yaml](https://raw.githubusercontent.com/iamfuhui/sat-pass-alert/v0.1.0-alpha.1/compose.external-db.yaml)，设置指向专用数据库的 `DATABASE_URL`。连接串密码需做 URL 编码，应用账户需具备迁移权限。

两种方式均设置 `SPA_BIND_IP=127.0.0.1` 与 `COOKIE_SECURE=true`。启动时用 `docker compose -f <文件名> pull` 和 `docker compose -f <文件名> up -d --no-build`。

## 本地开发

Python 3.12+、Node.js 22+。在 Python 虚拟环境中运行：

```sh
pip install -r requirements-dev.txt
python -m pytest -q
(cd frontend && npm ci && npm test && npm run build)
```

MariaDB 集成测试通过 `TEST_DATABASE_URL` 指定专用测试库。测试会删除并重建该库中的应用表。

[发行说明](https://github.com/iamfuhui/sat-pass-alert/releases) · [MIT License](LICENSE)
