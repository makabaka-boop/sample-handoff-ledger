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
- 夜班接收人发现封签破损、标签不符或包装污染时，可凭仍有效的六位码直接拒收（`POST /api/handoffs/reject`，原因限 `seal_broken`、`label_mismatch`、`package_contaminated`、`other`，备注可空）。拒收与确认共用同一事务裁决：同样依次锁定交接、容器、批次并校验待接收、未过期、仍位于来源；通过后交接置为 `anomaly`/`RECEIVER_REJECTED`、批次置为 `review`，容器不移动。若交接从冷藏指向非冷藏，按退回来源结算本次离柜时长并清除 `out_since`，且只追加一次 `handoff_rejected` 责任链事件。重复提交同一拒收返回首次结果并带 `replayed=true`。
- 日常盘点发现容器损坏且样本已转装时，在批次详情对 `active` 容器执行“转装替换”（`POST /api/containers/{id}/replace`，提交新标签、操作人、原因和可空备注）。服务在同一事务以 `SELECT … FOR UPDATE` 依次锁定原容器与批次，校验批次仍可流转且该容器没有待接收交接，再检查新标签在批次内不重名；任一校验失败整体回滚。通过后在原位置创建新容器并继承累计离柜秒数与 `out_since`（计时连续，不重置暴露预算），随后把原容器置为 `replaced`，记录替代容器、替换人、时间与原因，并追加一条 `container_replaced` 责任链事件（事件指向原容器）。原容器已替换返回 `CONTAINER_ALREADY_REPLACED`，存在待接收交接返回 `HANDOFF_ALREADY_PENDING`，新标签重名返回 `CONTAINER_LABEL_EXISTS`。封存容器不能再发起交接（返回 `CONTAINER_ALREADY_REPLACED`），历史交接仍指向原容器且旧接口可正常读取；迁移把全部旧容器置为 `active`。
- 两人同时提交一个有效码时，第二个事务在行锁后看到 `received`，返回与首次相同的接收事实并带 `replayed=true`；不会二次移动或追加事件。两次拒收同理，重复拒收只返回首次拒收事实。确认与拒收并发时仅先获得交接行锁的操作生效：确认胜出后拒收返回 `HANDOFF_ALREADY_RECEIVED`，拒收胜出后确认返回 `HANDOFF_REJECTED`。SQLite 只用于快速单测，生产并发保证来自 PostgreSQL 行锁。
- 确认、撤销、拒收和超时相遇时，先获得交接行锁的事务决定结果；刷新后位置、批次处置与时间线来自同一提交。发起人只能在服务端尚未判定过期时撤销。
- 到期的 pending 记录在读取时以有效状态 `anomaly` 展示为红色；确认时或重开时会持久化异常。未解决异常（包括尚未物化的过期记录）阻止整个批次发起新流转。
- 重开先把原异常解析为 `REOPENED`，保留旧记录和责任链并建立 `successor_id`，再生成新码。也可记录隔离、复核或放行决定；只有放行或重开会恢复 active。
- 网络中断时前端保留接收码与接收人，且不做乐观位置变更。API 错误统一返回 `code/message/retryable/trace_id/details`；数据库中断返回可重试的 503，事务死锁/序列化冲突返回可重试的 409。

## 验收操作

1. 在“批次档案”登记批次，输入 `2–8°C`、离柜上限、容器和初始位置。
2. 回到“交接待办”发起交接，记下只显示一次的六位码；待办显示根据响应内 `server_time` 建立的单调倒计时。
3. 在“短码接收”输入代码和接收人；成功后刷新批次详情，当前位置和 `handoff_received` 责任链同时出现。
4. 再提交同一码，应显示早前接收结果；批次时间线仍只有一次接收事件。
5. 在“短码接收”切换到“拒绝接收”，选择原因并填写备注后提交：容器仍留在来源位置，批次进入 `review`，时间线仅追加一条 `handoff_rejected`；再提交同一拒收码返回首次结果且 `replayed=true`。详情抽屉展示拒收人、原因与备注，可经隔离/复核/放行或“重开交接”恢复，重开会生成新的一次性码。
6. 发起有效期 1 分钟的交接，等待到期：列表变红，接收返回 `HANDOFF_EXPIRED`。重开后新旧记录通过 successor 关联，旧码不再有效。
7. 盘点发现容器损坏且样本已转装：打开批次详情，在 `active` 容器上点“转装替换”，填写新标签、操作人、原因并提交。原容器显示“已封存”且不再有操作按钮，新容器在同一位置显示“可流转”，离柜计时与 `out_since` 连续；时间线只增加一条 `container_replaced`，历史交接仍可打开且仍指向原容器。待接收交接存在时提交得到 `HANDOFF_ALREADY_PENDING`，标签重名得到 `CONTAINER_LABEL_EXISTS`，失败后页面保留全部输入。
8. 在浏览器开发工具中切换 Offline，提交接收或拒收：全部输入（动作、代码、人员、原因、备注）不会清空，页面不会移动容器；恢复联网后原输入可直接重试。
9. 并发验收可将 `CODE` 和接收人替换后同时执行；两个响应都为接收事实，其中一个 `replayed` 为 true。确认与拒收并发则一方得到事实、另一方得到 `HANDOFF_ALREADY_RECEIVED` 或 `HANDOFF_REJECTED`：

   ```bash
   for who in receiver-a receiver-b; do
     curl -sS -X POST http://localhost:8000/api/handoffs/confirm \
       -H 'Content-Type: application/json' \
       -d "{\"code\":\"$CODE\",\"received_by\":\"$who\"}" &
   done
   wait
   ```

   拒收请求体为 `{"code":"$CODE","rejected_by":"night-lead","reason":"seal_broken","note":"…"}`。

10. 用 `docker compose stop db` 后请求健康检查和写 API，确认得到含追踪号的 503；`docker compose start db` 后可重试原输入。

## 测试

```bash
make test                 # 容器内运行 pytest、Vitest 和前端生产构建
docker compose up -d      # 端到端测试前先启动完整栈
cd frontend
npm install
npx playwright install chromium
npm run e2e               # 浏览器创建记录，再由真实 API/数据库读回
```

后端测试覆盖状态转换、撤销权限、错误结构、幂等重放（确认与拒收）、双线程同码竞争、确认/拒收互斥竞争、拒收状态边界（过期、撤销、未知码、非法原因、冷柜离柜结算）、超时/重开历史及 59/60 秒暴露边界。转装替换另测离柜计时跨容器连续、待接收交接/重名标签/非 active 批次的冲突回滚、二次转装替代链、历史交接仍指向原容器以及双线程替换仅一个赢家。前端 Vitest 覆盖基于服务器快照和单调时钟的倒计时，以及领域错误、断网反馈映射、拒收表单保留，并验证新容器可发起交接、封存容器不可操作、替换失败保留输入且成功后时间线仅一条替换记录。Playwright 测试贯通页面、API 与 PostgreSQL，并验证拒收后位置不变、批次进入复核、时间线仅一条拒收事件且重开可生成新码；转装流程验证原容器封存、新容器继续交接及唯一替换事件。

## 密钥轮换提示

当前版本的未完成交接依赖单个 `HANDOFF_SIGNING_KEY`。轮换前应先撤销或处理全部 pending 交接，再替换环境变量并重启 API；否则旧码无法查找。这是有意的安全失败模式，历史记录和责任链本身不受影响。
