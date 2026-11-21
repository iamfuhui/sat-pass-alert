# SPA — Sat Pass Alert

自托管卫星过境预测与 Bark 提醒，面向业余无线电用户。FastAPI + Vue 3 + MariaDB + Skyfield/SGP4。

## v0.1 功能

- Maidenhead 网格定位支持 4、6、8 位，忽略大小写并自动换算网格中心经纬度；输入与精度保存在用户配置中，直接修改经纬度会清除原定位。
- 独立用户观测位置、显示时区、卫星订阅、最高仰角门槛和提前提醒分钟数。
- CelesTrak OMM 获取、TLE/OMM 手动导入；轨道每 6 小时刷新，默认拒绝偏离历元超过 14 天的预测窗口。
- 地平线 AOS/LOS、方位角、最高仰角；支持未来 24–72 小时预报。
- Bark JSON 推送、加密保存设备 Key、发送测试、持久化队列、有限重试及发送记录。
- AMSAT 状态只读，每小时抓取，按精确名称匹配，例如 `ISS_[FM]`。无有效近期报告时显示未知，不阻止推送。
- 初始管理员、默认关闭注册、一次性邀请码（7 天）或管理员审核；无需 SMTP。
- `/healthz` 检查数据库与迁移，`/api/v1/` API，`/docs` OpenAPI。

## Docker 快速部署

需要 Docker Engine 和 Compose v2。可选代理覆盖文件使用 Compose 2.24.4+ 的 `!override`。

1. 克隆仓库，复制 `.env.example` 为 `.env`。
2. 配置 `APP_SECRET_KEY`（至少 32 字符）、`ADMIN_PASSWORD`（至少 12 字符）、`DB_PASSWORD`、`DB_ROOT_PASSWORD`。推荐生成各不相同的 URL-safe 随机值：
   `python -c "import secrets; print(secrets.token_urlsafe(48))"`。
3. 运行 `docker compose up -d --build`。
4. 打开 `http://服务器地址:18473`，使用初始管理员账号登录。
5. 保存实际观测位置；在系统管理中按 NORAD 添加卫星；在卫星订阅中启用规则；保存 Bark Key 并发送测试。

当前应从源码构建。只有发布版本标签并成功执行 release workflow 后才会有对应 GHCR 镜像，不能假设 latest 已存在。容器内部端口 8000，默认宿主端口 18473，Bridge 网络。Dockerfile 非 root 运行，启动先执行 Alembic 迁移。数据库不对外映射端口。

连接已有 MariaDB：

```sh
docker compose -f compose.external-db.yaml up -d --build
```

在 `.env` 配置 `DATABASE_URL=mysql+pymysql://user:password@host:3306/spa?charset=utf8mb4`；数据库应预先创建，并给应用用户迁移权限。密码中的特殊字符须 URL 编码。独立 Compose 的 DB_PASSWORD 建议只用 URL-safe 字符。

主机反向代理可加 `-f compose.proxy.yaml` 将入口绑定到 127.0.0.1；容器代理按部署环境接入网络。应用不依赖 Cloudflare 或特定域名。HTTPS 终止后设置 `COOKIE_SECURE=true`，仅在 `TRUSTED_PROXY_IPS` 指定真实可信代理地址，默认不信任转发头。直接 HTTP 可用于可信局域网。

## 配置与运维

| 变量 | 默认值 / 用途 |
| --- | --- |
| APP_SECRET_KEY | 必填；会话签名及 Bark Key 加密。备份时与数据库一起妥善保管 |
| ADMIN_USERNAME / ADMIN_PASSWORD | admin / 必填初始密码；仅没有管理员时创建，不覆盖现有账号 |
| DATABASE_URL | 开发默认 SQLite；生产推荐 MariaDB 11.4 |
| SPA_BIND_IP / SPA_PORT | 0.0.0.0 / 18473 |
| BARK_SERVER_URL | https://api.day.app，可由部署管理员指定自托管服务 |
| COOKIE_SECURE | false；HTTPS 部署设 true |
| TRUSTED_PROXY_IPS | 空，仅信任明确配置的代理 |
| WORKER_ENABLED | true；测试可关闭 |
| TLE_MAX_AGE_DAYS | 14，按整个预测窗口检查 |
| STATIC_DIR | frontend/dist |

MVP **只支持一个应用实例、一个 Uvicorn worker**，不要扩容副本或使用多个 worker。内置调度器每 30 秒运行，预测未来一天并建立队列。通过唯一约束与同一次低轨过境 ±10 分钟匹配避免 TLE 小幅变化造成重复提醒。更新位置或规则会重建尚未发送的记录。停止订阅或停用用户会取消尚未发送的提醒。

失败最多尝试 3 次，按 2/4 分钟退避；AOS 后不再发送。进程在发送中中断的记录标记 unknown，不自动重发。Bark 没有本应用可用的幂等确认协议：网络超时后的重试仍可能重复，不能保证恰好一次投递。Bark 接受请求不代表 iPhone 已收到。记录保留 30 天。

轨道源失败会保留最后有效轨道，超过时效即停止预测；AMSAT 网页结构变化会退化为未知，不作为运行许可或卫星开机保证。此版面向有升落事件的低轨卫星，持续可见的地球同步目标不生成“过境”。首轮刷新或大量订阅可能延迟提醒，小规模自托管使用优先。

备份 MariaDB 数据卷与 APP_SECRET_KEY；升级前备份，再重建容器。不得公开提交 .env、数据库文件或 Bark Key。更换 APP_SECRET_KEY 会使会话及已有 Bark Key 失效，需要重新配置。管理员初始化不是密码重置机制。

## 本地开发与测试

Python 3.12+、Node 22+：

```sh
python -m venv .venv
# 激活虚拟环境后：
pip install -r requirements-dev.txt
# 配置 APP_SECRET_KEY、ADMIN_PASSWORD，开发可 WORKER_ENABLED=false
alembic upgrade head
uvicorn app.main:app --reload
# 另一个终端
cd frontend
npm ci
npm run dev
```

测试：

```sh
python -m pytest -q
cd frontend
npm test
npm run build
```

测试默认创建独立临时 SQLite 数据库；`TEST_DATABASE_URL` 可指向**专用、可清空的** MariaDB 测试库。测试会删除这个库中的应用表，绝不能指向生产数据库。覆盖注册/审核、CSRF、用户隔离、密钥加密、迁移升降级、轨道几何、过期拒绝、调度去重/重试/取消、Bark MockTransport 和 AMSAT 解析。不会向真实设备推送。

CI 配置 Python 3.12/3.14、MariaDB 集成测试、前端测试/构建、AMD64/ARM64 镜像构建与 Compose 启动检查。版本标签 v* 触发 GHCR 多架构镜像发布，默认分支不会在开发过程中被直接改写。

## 上游接口

- [Skyfield 地球卫星计算](https://rhodesmill.org/skyfield/earth-satellites.html)
- [Bark 服务 API](https://github.com/Finb/bark-server/blob/master/docs/API_V2.md)
- [AMSAT 公众状态报告](https://status.amsat.org/)

MIT License。
