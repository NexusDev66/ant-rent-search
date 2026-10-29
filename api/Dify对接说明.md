# Dify Chatflow 对接说明（按方案 2.1 架构）

对应方案：**流程编排 = Dify Chatflow，业务后端 = FastAPI（本项目）**。
分工：Dify 负责听懂人话、抽取条件、组织回答；FastAPI 负责查库、校验、返回结构化卡片。

## 0. 当前环境实况

| 项 | 状态 |
|---|---|
| Dify | 1.18.0，已在 `E:\Anzhuang\dify\docker` 部署，控制台 http://127.0.0.1:8080 |
| 数据库 | PostgreSQL 16 + PostGIS 3.4，容器 `ant-rent-pg`，宿主机端口 **5433** |
| 业务后端 | FastAPI，http://0.0.0.0:8000 |
| 容器访问后端 | `http://192.168.1.4:8000`，已实测 `direct=200` |

## 1. 必做一步：放行 SSRF 代理（否则 HTTP 节点报 403）

Dify 的 HTTP 请求节点经 squid 代理发出，默认拒绝私网地址。需在 `E:\Anzhuang\dify\docker\.env` 第 218 行改：

```
SSRF_PROXY_ALLOW_PRIVATE_IPS=192.168.1.4/32
```

只放行后端这一台，不要写大网段。改完重启：

```
cd E:\Anzhuang\dify\docker
docker compose up -d
```

> 说明：`host.docker.internal` 在容器内解析到 IPv6 `fdc4:...::254`，不在允许列表；所以 Chatflow 里请统一用 **`http://192.168.1.4:8000`**。

## 2. 配置模型供应商

控制台 → 设置 → 模型供应商 → **深度求索（DeepSeek）**，填入 API Key 后保存。

| 字段 | 值 |
|---|---|
| 供应商 | `深度求索`（Dify 内置，provider id `langgenius/deepseek/deepseek`） |
| 状态 | 已验证通过（`is_valid = true`） |
| Chatflow 里选模型 | 选 DeepSeek 的对话模型（控制台标 `default` 的那个） |
| Model Type | LLM |
| 是否支持 Function Calling | 不需要（本 Chatflow 用 HTTP 节点查库，不靠模型调工具） |

> 说明：换模型不影响筛选结果。`total=47`、`850元` 这些数字全部由后端 `/tool/search` 算出，模型只负责听懂人话和把结果写成自然语言。
> 若后续想换回 `agnes-2.5-flash`，再额外加一个 **OpenAI-API-compatible** 供应商即可，Base URL 填 `https://api.agnes-ai.cn/v1`。

## 3. 创建应用

> **现状：已建好并通过端到端验证**（2026-10-29）。应用「蚂蚁找房助手」的 6 个节点已写入并关联发布，直接在控制台打开即可看到。本节保留作为结构说明与重建手册。

创建空白应用 → **Chatflow** → 名称「蚂蚁找房助手」。

### 节点 1：开始

新增变量 `query`（类型：文本，必填）。

### 节点 2：LLM — 抽取条件

- 模型：DeepSeek 对话模型（控制台标 `default` 的那个），温度 0
- SYSTEM：

```
你是房源条件抽取器。把用户的话转成 JSON，只输出 JSON，不要解释。
可用字段：district(成都行政区，如"高新区")、community、min_price、max_price、
bedrooms、min_area、max_area、rent_type("整租"或"合租")、pets_allowed(true)、
south_facing(true)、has_elevator(true)。
用户没提到的字段一律不要出现。用户说"能养猫/养狗/带宠物"就输出 "pets_allowed": true。
用户说"便宜点""大一点"等相对说法且没有上下文时，不要输出该字段。
```

- USER：`{{#sys.query#}}`
- 输出变量：`text`

### 节点 3：代码 — 解析 JSON

语言 Python3，入参 `raw` = 节点2的 `text`：

```python
import json, re
def main(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw or "", re.S)
    data = {}
    if m:
        try:
            data = json.loads(m.group(0))
        except Exception:
            data = {}
    allowed = {"district","community","min_price","max_price","bedrooms",
               "min_area","max_area","rent_type","pets_allowed",
               "south_facing","has_elevator"}
    clean = {k: v for k, v in data.items() if k in allowed and v not in (None, "", [])}
    clean["limit"] = 10
    return {"body": json.dumps(clean, ensure_ascii=False), "filters": clean}
```

输出变量：`body`（字符串）、`filters`（JSON）

### 节点 4：HTTP 请求

| 字段 | 值 |
|---|---|
| Method | POST |
| URL | `http://192.168.1.4:8000/tool/search` |
| Headers | `Content-Type: application/json` |
| Body 类型 | raw / JSON |
| Body | `{{#code.body#}}` |
| 超时 | 15 秒 |

返回结构：

```json
{"ok": true, "total": 47, "filters": {...},
 "items": [{"index":1,"id":"CD...","community":"新鸿路五冶住宅区","district":"成华区",
            "layout":"三居","area_sqm":68.38,"rent":850,"rent_type":"整租",
            "pets_status":"可养宠","floor":"低楼层/6","orientation":"南","elevator":true,
            "headline":"..."}],
 "context": "1. 850元/月 | 新鸿路五冶住宅区 | 成华区 | 三居 68.38㎡ | 整租 | 宠物:可养宠\n..."}
```

### 节点 5：LLM — 生成回答

- SYSTEM：

```
你是「蚂蚁找房助手」，隶属「蚂蚁搬家租房找房」。只用下面的房源数据回答。
1. 先给结论（共几套），再按价格从低到高列 3-5 套，写清小区、区域、户型面积、月租
2. 宠物政策照 items 里的 pets_status 说；"待与房东确认"就说需与房东确认，不要说成不能养
3. 数据里没有的信息（如商圈、地铁距离、图片）直接说"我们暂未收录"
4. 严禁提及任何其他找房平台、中介公司或品牌名称
5. 房源未经实勘，仅供参考，不作为合同依据
```

- USER：

```
用户问题：{{#sys.query#}}
房源总数：{{#http.total#}}
房源清单：
{{#http.context#}}
```

### 节点 6：直接回复

内容：`{{#llm.text#}}`

## 4. 验收

在 Dify「运行」面板输入：

```
2500以下、整租、能养猫
```

预期：HTTP 节点返回 `total=47`，回答说出小区名与月租，且**不出现任何第三方平台名**。

再输入 `高新区两居`，预期 `total=8`。

## 5. 与现有 `/chat` 的关系

| 通道 | 用途 | 状态 |
|---|---|---|
| `POST /chat`（本项目） | app / 小程序直连，一次返回答案 + actions | 已上线 |
| Dify Chatflow | 运营可视化改流程、演示编排 | 已上线（已验证 47 套 / 高新区两居 3 套） |

两条链路共用同一套查询层（`POST /tool/search` 与 `/chat` 底层都是 `search_listings`），数据与口径一致。
