# 夜班样本交接台

一个真正以 PostgreSQL 为裁决者的样本交接台。FastAPI/SQLAlchemy 保存批次、容器、位置、一次性交接和不可变责任链；React/Vite 页面提供待办、六位短码接收、异常处置及详情。浏览器不会推测写入成功，也不会用本机时间决定交接是否过期。

## 启动

需要 Docker 及 Docker Compose v2。

```bash
cp .env.example .env
# 修改 .env 中的 POSTGRES_PASSWORD、DATABASE_URL 密码和 HANDOFF_SIGNING_KEY
docker compose up --build -d
docker compose ps
```

打开 <http://localhost:4173>；API 文档位于 <http://localhost:8000/docs>。三个服务均有健康检查，且 API、前端以非 root 用户运行。API 会等待数据库可连接，然后执行 `alembic upgrade head`；迁移失败时容器以代码 71 退出并输出明确恢复提示，而不会带着旧 schema 接收写入。

`DATABASE_URL` 中的密码必须与 `POSTGRES_PASSWORD` 相同。例如把密码改为 `a-long-password` 时，同时使用：

```dotenv
POSTGRES_PASSWORD=a-long-password
DATABASE_URL=postgresql+psycopg://handoff:a-long-password@db:5432/handoff
```

仓库不会提交 `.env`、本地密钥、数据库目录或 Docker 生成物。

## 数据库迁移与重置

API 正常启动会自动迁移。手动查看及执行迁移：

```bash
docker compose run --rm --entrypoint alembic api current
docker compose run --rm --entrypoint alembic api upgrade head
```

若迁移失败，先运行 `docker compose logs api db`，根据 Alembic revision/SQLSTATE 修复配置或 schema，再重启 `docker compose up -d api`。不要绕过迁移启动 Uvicorn。

完全重置开发数据（会删除命名数据库卷，不能恢复）：

```bash
docker compose down --volumes
docker compose up --build -d
```

生产备份应在迁移前使用 `pg_dump`；恢复时先恢复同版本数据，再运行 `alembic upgrade head`。

## 业务与并发语义

- 批次记录温区和最长离柜分钟数。位置明确标记是否为冷藏；从冷藏发起到非冷藏位置的交接即开始计时，因此接收人迟到也计入暴露。撤销视为返回来源并结算本次时长；回到冷藏时累计，达到上限（包括恰好等于边界）即拒绝接收并形成异常。
- 六位码由密码学随机源产生，只保存以 `HANDOFF_SIGNING_KEY` 计算的 HMAC-SHA256 摘要。明码只在发起/重开响应展示一次；已经用过的摘要不会重新分配。
- 确认在一个事务中以 `SELECT … FOR UPDATE` 依次锁定交接、容器和批次，再用数据库可见的当前服务端时间校验状态、截止时间、来源位置和暴露上限。只有校验全部通过，才同时移动容器并追加责任链。
- 两人同时提交一个有效码时，第二个事务在行锁后看到 `received`，返回与首次相同的接收事实并带 `replayed=true`；不会二次移动或追加事件。SQLite 只用于快速单测，生产并发保证来自 PostgreSQL 行锁。
- 确认、撤销和超时相遇时，先获得交接行锁的事务决定结果；刷新后位置与时间线来自同一提交。发起人只能在服务端尚未判定过期时撤销。
- 到期的 pending 记录在读取时以有效状态 `anomaly` 展示为红色；确认时或重开时会持久化异常。未解决异常（包括尚未物化的过期记录）阻止整个批次发起新流转。
- 重开先把原异常解析为 `REOPENED`，保留旧记录和责任链并建立 `successor_id`，再生成新码。也可记录隔离、复核或放行决定；只有放行或重开会恢复 active。
- 网络中断时前端保留接收码与接收人，且不做乐观位置变更。API 错误统一返回 `code/message/retryable/trace_id/details`；数据库中断返回可重试的 503，事务死锁/序列化冲突返回可重试的 409。

## 验收操作

1. 在“批次档案”登记批次，输入 `2–8°C`、离柜上限、容器和初始位置。
2. 回到“交接待办”发起交接，记下只显示一次的六位码；待办显示根据响应内 `server_time` 建立的单调倒计时。
3. 在“短码接收”输入代码和接收人；成功后刷新批次详情，当前位置和 `handoff_received` 责任链同时出现。
4. 再提交同一码，应显示早前接收结果；批次时间线仍只有一次接收事件。
5. 发起有效期 1 分钟的交接，等待到期：列表变红，接收返回 `HANDOFF_EXPIRED`。重开后新旧记录通过 successor 关联，旧码不再有效。
6. 在浏览器开发工具中切换 Offline，提交接收：输入不会清空，页面不会移动容器；恢复联网后原输入可直接重试。
7. 并发验收可将 `CODE` 和接收人替换后同时执行；两个响应都为接收事实，其中一个 `replayed` 为 true：

   ```bash
   for who in receiver-a receiver-b; do
     curl -sS -X POST http://localhost:8000/api/handoffs/confirm \
       -H 'Content-Type: application/json' \
       -d "{\"code\":\"$CODE\",\"received_by\":\"$who\"}" &
   done
   wait
   ```

8. 用 `docker compose stop db` 后请求健康检查和写 API，确认得到含追踪号的 503；`docker compose start db` 后可重试原输入。

## 测试

```bash
make test                 # 容器内运行 pytest、Vitest 和前端生产构建
docker compose up -d      # 端到端测试前先启动完整栈
cd frontend
npm install
npx playwright install chromium
npm run e2e               # 浏览器创建记录，再由真实 API/数据库读回
```

后端测试覆盖状态转换、撤销权限、错误结构、幂等重放、双线程同码竞争、超时/重开历史及 59/60 秒暴露边界。前端 Vitest 覆盖基于服务器快照和单调时钟的倒计时，以及领域错误、断网反馈映射。Playwright 测试贯通页面、API 与 PostgreSQL。

## 密钥轮换提示

当前版本的未完成交接依赖单个 `HANDOFF_SIGNING_KEY`。轮换前应先撤销或处理全部 pending 交接，再替换环境变量并重启 API；否则旧码无法查找。这是有意的安全失败模式，历史记录和责任链本身不受影响。
