# 蚂蚁找房助手 · 前后端接口契约 v1

> 给前端同事：接口已可用，直接接。所有字段一次说清，不需要再问。
> 有问题改这里，不要各自猜。

## 0. 连接方式

| 项 | 值 |
|---|---|
| 本机（后端自己调试） | `http://127.0.0.1:8000` |
| **局域网（前端请用这个）** | `http://192.168.1.4:8000` |
| 交互式文档（可直接点着试） | `http://192.168.1.4:8000/docs` |
| CORS | 已放开 `*`，浏览器直连无需代理 |

启动命令（后端方执行）：`python -m uvicorn api.main:app --host 0.0.0.0 --port 8000`

---

## 1. 核心：POST /chat —— 一句话找房 + 页面联动

### 请求

```json
{
  "message": "2500以下、整租、能养猫",
  "session_id": "用户或会话的唯一ID",
  "stream": false,
  "mode": "llm"
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| message | 是 | 用户这句话，1-500 字 |
| session_id | 否 | 多轮上下文用，同一个人保持不变，默认 `default` |
| stream | 否 | `true` 返回 SSE 流，默认 `false` |
| mode | 否 | `llm`（默认）走大模型；`rules` 不调模型、纯规则解析，**演示兜底用** |

### 响应（非流式）

```json
{
  "session_id": "abc",
  "answer": "按「预算上限2500元、租赁方式：整租」共找到 75 套房源，已为你更新列表。",
  "mode": "rules",
  "filters": { "max_price": 2500, "rent_type": "整租" },
  "actions": [
    { "type": "update_filter", "payload": { "max_price": 2500, "rent_type": "整租" } },
    { "type": "render_listings",
      "payload": { "total": 75, "ids": ["CD2225...", "CD2225..."], "listings": [ /* 见 §4 */ ] } }
  ],
  "listings": [ /* 同上，方便直接渲染 */ ]
}
```

### 关键：`actions` 是前端要执行的页面动作

**前端只需要一个 `switch(action.type)`，不用自己解析自然语言。**

| type | 触发场景 | payload | 前端要做什么 |
|---|---|---|---|
| `update_filter` | 用户说了新的筛选条件 | 条件对象（见下表） | 把筛选栏同步成这个状态 |
| `render_listings` | 有搜索结果 | `total` / `ids` / `listings` | 刷新房源卡片列表 |
| `open_detail` | 用户说「打开第二套」「看第三个」 | `id` / `index` | 跳详情页，`id` 直接拿去调 §3 |
| `locate_map` | 本轮结果里存在已回填坐标的房源 | `points`：`[{id, lng, lat}]` | 在地图上打点并缩放到可视范围 |

**`locate_map` 说明**：只在有坐标时下发，可能缺失（当前部分房源坐标待回填）。`payload.points` 里的 `lng`/`lat` 已经是 WGS84 坐标；如果前端用的是高德/腾讯地图（GCJ02），需要先做一次 `WGS84 → GCJ02` 转换，否则会有几百米偏移。

`update_filter.payload` 可用字段（与后端筛选参数同名）：

| 字段 | 类型 | 说明 |
|---|---|---|
| district | string | 行政区，如 `"高新区"` |
| max_price / min_price | int | 月租上下限，元，含本数 |
| bedrooms | int | 卧室数 1-4 |
| rent_type | string | `"整租"` / `"合租"` |
| pets_allowed | bool | 是否可养宠 |
| south_facing | bool | 朝南 |
| has_elevator | bool | 有电梯 |
| min_area / max_area | float | 面积区间 |
| community | string | 小区名精确匹配 |

**注意 `open_detail` 的 `id` 为 `null` 时**，`payload.error` 有中文原因（如「当前列表只有 3 套，没有第 5 套」），前端弹提示即可。

---

## 2. 流式版（打字机效果）

请求带 `"stream": true`，返回 `text/event-stream`，逐行：

```
data: {"delta": "在天府新区找到 "}
data: {"delta": "4套一居室"}
data: {"done": true, "filters": {...}, "actions": [...]}
data: [DONE]
```

- 收到 `delta` 就追加到气泡里
- 收到 `done` 再执行 `actions`（**关键顺序：先出文字，再动页面**）
- 出错时收到 `{"error": "..."}`
- **不要用 EventSource**（它只支持 GET），用 `fetch` + ReadableStream 或 `@microsoft/fetch-event-source`

---

## 3. GET /listings/{id} —— 详情页

拿 `open_detail` 的 id 直接请求，返回单条房源完整字段（§4 全部字段 + 以下）：

| 字段 | 说明 |
|---|---|
| amenities | 家电/配套数组，如 `["洗衣机","空调","冰箱","暖气","宽带","天然气"]` |
| tags | 标签数组，如 `["权属核验","近地铁","精装","随时看房"]` |
| quality_flags | 正向标签，如 `["政府权属核验","可随时入住","有电梯","近地铁"]` |
| risk_flags | 风险提示，如 `["无电梯且位于高楼层","图片均带水印，不宜直接对外展示"]` |
| photo_total | 图片数量（**图片素材未提供，前端请用占位图**） |

不存在或已下架 → `404`。

---

## 4. 房源卡片字段（`listings[]` 每项）

```json
{
  "id": "CD2225564269229899776",
  "district": "成华区",
  "community": "新鸿路五冶住宅区",
  "headline": "新鸿路五冶住宅区 三室一厅一卫 68.38㎡ 朝东南 高楼层/6层 精装 850元/月",
  "rent_cny_per_month": 850,
  "rent_cny_per_sqm_month": 12.43,
  "area_sqm": 68.38,
  "bedrooms": 3, "living_rooms": 1, "bathrooms": 1, "layout_label": "三居",
  "orientation_primary": "东南", "is_south_facing": true,
  "floor_position": "高", "total_floors": 6, "elevator": false,
  "decoration_tier": "精装", "rent_type": "整租", "move_in_type": "immediate",
  "ownership_checked": true,
  "pets_allowed": null,
  "pets_status": "待与房东确认",
  "price_position_label": "明显低于同类中位",
  "vs_cohort_pct": -57.5,
  "risk_flags": ["无电梯且位于高楼层"]
}
```

字段渲染建议：

| 字段 | 推荐展示 |
|---|---|
| `headline` | 卡片主标题，已拼好，直接用最省事 |
| `rent_cny_per_month` | 大字 `850 元/月` |
| `layout_label` + `area_sqm` | `三居 · 68.38㎡` |
| `elevator` 为 `false` | 灰字 `无电梯`；为 `null` 显示 `—` |
| `pets_allowed` 为 `null` | **直接展示 `pets_status`**：`待与房东确认`（灰字）。**不要渲染成「不可养」** |
| `price_position_label` + `vs_cohort_pct` | `低于同类中位 65%` 绿色标签；正数用红色 |
| `ownership_checked` | 打勾徽标 `权属已核验` |
| `risk_flags` | 橙色小字提示，别忽略 |

---

## 4.5 POST /tool/search —— 给 Dify Chatflow 用

`/chat` 是给 app/小程序直连的；Dify 的 HTTP 节点走这个更省事，一次拿到结论、卡片和可直接塞给模型的文本。

```
POST /tool/search
{"max_price": 2500, "rent_type": "整租", "pets_allowed": true, "limit": 10}
```

```json
{"ok": true, "total": 47, "filters": {"max_price": 2500, "rent_type": "整租", "pets_allowed": true},
 "items": [{"index": 1, "id": "CD...", "community": "新鸿路五冶住宅区", "rent": 850,
            "layout": "三居", "area_sqm": 68.38, "pets_status": "可养宠", "headline": "..."}],
 "context": "1. 850元/月 | 新鸿路五冶住宅区 | 成华区 | 三居 68.38㎡ | 整租 | 宠物:可养宠\n..."}
```

参数与 `/listings` 一致；`index` 从 1 开始，可直接用于「打开第 N 套」。

## 5. GET /listings —— 直接筛选（不经过模型）

做筛选栏/列表页时用这个，比 /chat 快且稳定：

```
GET /listings?district=高新区&bedrooms=2&max_price=3000&pets_allowed=true&sort=price_asc&limit=20
```

可传参数：`district` `min_price` `max_price` `bedrooms` `min_area` `max_area` `rent_type` `community` `pets_allowed` `south_facing` `has_elevator` `include_non_residential` `sort(price_asc|price_desc|area_desc)` `limit(1-100)`

- **`include_non_residential` 默认 `false`**：默认已排除 `tags` 含「非居住房屋」的商办公寓，演示列表更干净。要看全量传 `true`。
- `pets_allowed=true` 是**严格筛选**：只返回标为「可养宠」的房源，`null` 的不会混进来。

返回 `{ "total": 3, "listings": [...] }`；未知行政区 → `400`。

---

## 6. 两个必须知道的数据现状（影响演示）

1. **宠物政策是「三态」**：`pets_allowed` 为 `1`=可养、`0`=不可、`null`=未核实。
   一律以 `pets_status` 文案展示：`可养宠` / `不养宠` / `待与房东确认`。
   **当前库内已灌入演示用示例数据**（`房源数据/pets.csv`，规则见文件「备注」列），
   演示阶段可直接筛选。正式上线前需用真实房东确认结果覆盖该文件，再跑
   `python db/import_data.py`（无需重启服务）。
2. **无图片**：只有 `photo_total` 数量，没有图片文件。卡片请用占位图或纯色块。

---

## 7. 演示兜底（周五现场一定要留这一手）

模型偶发慢或抽风时，前端只需把 `mode` 改成 `"rules"`：

```json
{ "message": "2500以下、整租、能养猫", "session_id": "demo", "mode": "rules" }
```

同一句话，返回结构完全一致，但走确定性规则、不做 LLM 调用，毫秒级返回。
**建议：默认 `llm`，控制台留个开关随时切 `rules`。**

---

## 8. 其它

| 接口 | 用途 |
|---|---|
| `GET /health` | 存活探针，返回在架房源数 |
| `POST /session/reset` | 清空某个 session 的对话记忆 |
| `GET /docs` | Swagger，可直接试调 |
