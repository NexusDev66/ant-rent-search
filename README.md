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

## 协作约定

1. 不要直接改 `main`，从 `dev` 切 `feature/xxx` 分支，改完提 PR。
2. 改接口字段必须同步更新 `api/API契约.md`。
3. 提交前确认没把密钥、数据库文件（`*.db`）带上。
4. 修改模型配置后，用 `验收标准.md` 的 12 题测试集回归一次。
