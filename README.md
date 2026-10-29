# 蚂蚁搬家租房找房 · 房源检索与对话服务

用自然语言对话找房的后端服务。用户说「2500以下、整租、能养猫」，服务把这句话转成结构化筛选条件，从房源库中精确检索，再用自然语言返回结果。

## 整体链路

```
用户 → Dify Chatflow（6 节点）─┐
                              ├→ FastAPI → PostgreSQL + PostGIS
用户 → 前端直接调 /chat（SSE）─┘
```

- **检索不走向量相似度**：房源是结构化数据（价格、户型、面积、行政区都已字段化），SQL 精确筛选比向量检索准，所以采用「模型抽条件 → 查库 → 模型写回答」的工具增强方式。
- **后端与模型解耦**：模型只负责理解自然语言和措辞，筛选数字全部由后端计算，换模型不影响结果。

## 目录结构

```
rag_server/
├─ api/
│  ├─ main.py            FastAPI 服务（/chat /tool/search /listings /health）
│  ├─ API契约.md          前端对接文档
│  └─ Dify对接说明.md     Chatflow 搭建步骤
├─ rag/
│  ├─ db.py              连接层（PostgreSQL / SQLite 可切换）
│  ├─ retrieve.py        查询层 search_listings
│  ├─ llm.py             LLM 客户端 + 工具 schema + 系统提示词
│  └─ rules.py           确定性规则解析（模型不可用时兜底）
├─ db/
│  ├─ schema_pg.sql      PostgreSQL 表结构（含 geometry + GiST 索引）
│  ├─ schema.sql         SQLite 表结构
│  ├─ import_data.py     导入数据（幂等）
│  ├─ migrate_to_pg.py   同步到 PostgreSQL（幂等）
│  ├─ backfill_geom.py   按小区名回填坐标（高德/百度双数据源）
│  ├─ export_seed.py     从库导出脱敏 CSV
│  └─ load_seed.py       把 CSV 导入 PostgreSQL（幂等）
├─ data/
│  └─ listings_seed.csv  126 条脱敏样例数据
├─ chat.py               命令行对话
├─ 验收标准.md            A-F 六组验收项 + 测试集
└─ requirements.txt
```

## 快速开始

### 1. 起数据库

```bash
docker run -d --name ant-rent-pg -p 5433:5432 \
  -e POSTGRES_DB=antrent -e POSTGRES_USER=antrent -e POSTGRES_PASSWORD=ant_rent_dev \
  postgis/postgis:16-3.4
```

### 2. 装依赖

```bash
pip install -r requirements.txt
```

### 3. 建表并导入数据

```bash
# 建表（含 PostGIS 字段与索引）
docker exec -i ant-rent-pg psql -U antrent -d antrent < db/schema_pg.sql

# 导入脱敏样例数据（126 条，幂等，可重复执行）
python db/load_seed.py
```

如果不想用 Docker，把 `PG_DSN` 指向任意 PostgreSQL 15+ 并装好 PostGIS 扩展即可。

### 4. 启动服务

```bash
python -m uvicorn api.main:app --host 0.0.0.0 --port 8000
```

访问 `http://127.0.0.1:8000/health` 应返回 `{"status":"ok","active_listings":126}`。

## 环境变量

| 变量 | 必填 | 说明 |
|---|---|---|
| `RAG_DB` | 否 | `postgres`（默认）/ `sqlite` |
| `PG_DSN` | 否 | 默认 `host=127.0.0.1 port=5433 dbname=antrent user=antrent password=ant_rent_dev` |
| `RAG_API_KEY` | 是 | 对话模型密钥 |
| `RAG_API_BASE` | 否 | 默认 `https://api.agnes-ai.cn/v1` |
| `RAG_MODEL` | 否 | 默认 `agnes-2.5-flash` |
| `AMAP_KEY` | 否 | 高德 Web 服务 Key（坐标回填） |
| `BAIDU_AK` | 否 | 百度服务端 AK（坐标回填，二选一） |

密钥一律走环境变量，**不要写进代码或提交到仓库**。

## 接口

| 接口 | 用途 |
|---|---|
| `POST /chat` | 对话，`stream:true` 返回 SSE；`mode:"rules"` 走确定性规则兜底 |
| `POST /tool/search` | 供 Dify HTTP 节点调用，返回结论 + 卡片 + 上下文文本 |
| `GET /listings` | 纯结构化筛选 |
| `GET /listings/{id}` | 房源详情 |
| `GET /health` | 健康检查 |
| `POST /session/reset` | 清空会话 |

响应里的 `actions` 是给前端执行的页面动作：`update_filter` / `render_listings` / `open_detail` / `locate_map`。详见 [API契约.md](api/API契约.md)。

## 数据说明

- 仓库内 `data/listings_seed.csv` 为**脱敏样例**：已移除来源地址、内部核验码等字段，仅用于联调。
- 坐标字段 `geom` 为 PostGIS `geometry(Point,4326)`，存 **WGS84**。前端若用高德/腾讯地图（GCJ02）需先转换。
- 部分房源坐标待回填，`locate_map` 动作可能只包含部分点位。

## 5 分钟上手（新同事）

前提：本机有 Docker 和 Python 3.10+，且仓库管理员已把你加为 collaborator（私有库看不到仓库要先找何安加人）。

```bash
# 1. 拉代码
git clone https://github.com/NexusDev66/ant-rent-search.git
cd ant-rent-search

# 2. 装依赖
pip install -r requirements.txt

# 3. 起数据库（首次约 1 分钟）
docker run -d --name ant-rent-pg -p 5433:5432 \
  -e POSTGRES_DB=antrent -e POSTGRES_USER=antrent -e POSTGRES_PASSWORD=ant_rent_dev \
  postgis/postgis:16-3.4

# 4. 建表 + 导入 126 条样例（幂等，可重复跑）
docker exec -i ant-rent-pg psql -U antrent -d antrent < db/schema_pg.sql
python db/load_seed.py

# 5. 配模型密钥（PowerShell）
$env:RAG_API_KEY = "向何安索取"

# 6. 起服务
python -m uvicorn api.main:app --host 0.0.0.0 --port 8000
```

自检（三个都通就算上手完成）：

```bash
curl http://127.0.0.1:8000/health
# 期望 {"status":"ok","active_listings":126}

curl "http://127.0.0.1:8000/listings?limit=1"

curl -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -d "{\"message\":\"2500以下 整租 能养猫\",\"mode\":\"rules\"}"
# mode=rules 走确定性规则，不消耗模型额度，用来验证「检索链路」是否通
```

Windows 用 PowerShell 时把 `curl` 换成 `curl.exe`，或直接用浏览器打开 `http://127.0.0.1:8000/docs` 调接口。

常见卡点：

- `/health` 里 `active_listings` 是 0 → 第 4 步没导入成功，重跑 `python db/load_seed.py`。
- 连不上库 → 端口被占，换 `-p 5434:5432` 并同步改 `PG_DSN`。
- `/chat` 报模型错误 → `RAG_API_KEY` 没配，先用 `mode:"rules"` 调检索。

## 协作规范

**分支模型**

1. `main` 只保留可运行版本，任何人不得直接推送，一律走 PR。
2. 从 `main` 切功能分支：`git checkout -b feature/简短说明`（修 bug 用 `fix/简短说明`）。
3. 完成后 `git push -u origin feature/xxx`，在网页发起 Pull Request，至少一人 review 后合并。
4. 合并方式统一用 **Squash and merge**，保持 `main` 线性、好回滚。
5. 分支合并当天删除，避免堆积。

**PR 检查项（发起前自查）**

- 本地能起服务，`/health`、`/listings` 至少各通一次。
- 改了接口字段/参数 → 同步更新 `api/API契约.md`，否则前端会踩空。
- 改了模型配置、检索条件、排序逻辑 → 用 `验收标准.md` 的测试集回归一遍。
- 没有把密钥、`.env`、`*.db`、`data/` 原始数据提交上来（`.gitignore` 已拦，提交前看一眼 `git status`）。

**代码约定**

- 密钥只走环境变量，不写死、不进代码注释。
- 数据库结构变更只加不改：新增字段给默认值，`schema_pg.sql` 与 `schema.sql` 同步更新，导入脚本保持幂等（可重复执行）。
- 检索逻辑与模型调用分离：新筛选条件加在 `rag/retrieve.py`，提示词改在 `rag/llm.py`，不要在接口层写业务判断。

**联调约定**

- 后端接口以 `main` 上的 [API契约.md](api/API契约.md) 为准，前端/Dify 侧按此对接；字段有调整提前在 PR 里说明。
- Dify Chatflow 的搭建与节点参数见 [Dify对接说明.md](api/Dify对接说明.md)，改节点后请把变更点写进该文档。
- 联调环境共用一套库，不要在里面做删表、批量改数据的实验；需要实验请本地起 Docker。

**沟通**

- 需求/口径问题在 PR 或群里说清楚，不要口头改完不留记录。
- 发现数据问题（租金异常、字段缺失）提 Issue 并附一条示例 `id`。
