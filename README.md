# 实验室混样批次追溯 API

基于 **FastAPI + SQLite** 的实验室混样（pooling）批次追溯服务：登记原始样本、按配方创建混样批次，并对任一批次递归追溯其中各原始样本的精确占比（约分后的分子/分母，无浮点误差）。

## 功能特性

- 登记**原始样本**、按配方创建**混样批次**（配方 = 已有批次 + 各自的正整数投入份数）
- **原始样本禁配规则**：登记一对不同的原始样本（无序对，重复登记幂等）；创建混样时沿配方图递归展开**嵌套混样与多路径汇合**后的全部可达原始样本，命中任一禁配对即返回 **409**（不创建批次或配方）；规则只约束**随后创建**的批次，已有批次及其占比接口不受影响
- 创建校验：拒绝**空配方**（422）、**未知批次**（400）、**非正整数份数**（422，含 0、负数、小数、字符串）、**命中禁配规则**（409）
- 新批次只能引用**已创建**的批次，引用关系天然构成有向无环图，不可能成环
- 占比追溯：按每层“投入份数 / 该层总份数”递归分摊；同一原始样本经多条路径到达时**自动合并**；基于 `fractions.Fraction` 精确有理数运算，返回**约分后**的分子与分母
- **目标占比规划**：给定两个不同的已有批次、一个原始样本与目标占比，反算使新混样精确达标的**正整数投入份数**，只返回总份数最小的整数解（两原料占比都等于目标时取 1:1），配方可直接提交 `/pools`；**只规划、不创建批次**
- **配方变更影响预演**：提交完整替代配方，在内存中演算目标批次及全部下游批次各原始样本的原占比、预演占比与有符号差值，**不修改数据库**
- **份数诊断**：假设混样配方仅一项投入份数记错，按实测原始样本占比用有理数方程反推该项的正整数份数，并用全部样本占比复核；份数变化不改变组成且与实测吻合的项列为**无法判定**；**不修改数据库**
- 依赖安装与数据库建表全部纳入启动流程，无需手工初始化

## 快速开始（Docker）

唯一需要的一条命令：

```bash
docker compose up --build
```

启动后访问：

| 地址 | 说明 |
| --- | --- |
| <http://localhost:8000> | API 服务 |
| <http://localhost:8000/docs> | Swagger 交互式文档（可直接试调用） |
| <http://localhost:8000/redoc> | ReDoc 文档 |

数据保存在 Compose 命名卷 `lab-data`（容器内 `/data/lab.db`）中，删除重建容器数据不丢失；`docker compose down -v` 可连同数据一起清除。

## 配置

通过环境变量配置（`docker-compose.yml` 中已设置，可按需修改）：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DATABASE_PATH` | 容器内 `/data/lab.db`，本地运行默认 `data/lab.db` | SQLite 数据库文件路径，启动时自动建目录、建表 |

端口映射在 `docker-compose.yml` 的 `ports` 中调整（默认 `8000:8000`）。

## API 一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/samples` | 登记原始样本 |
| `POST` | `/exclusion-rules` | 登记原始样本禁配对（无序对，重复登记幂等） |
| `GET` | `/exclusion-rules` | 列出全部禁配规则（按样本对字典序） |
| `POST` | `/pools` | 创建混样批次（命中禁配返回 409） |
| `POST` | `/pool-plans` | 按目标占比反算整数份数配方（只规划，不写库） |
| `GET` | `/batches` | 列出全部批次 |
| `GET` | `/batches/{id}` | 批次详情（混样含配方） |
| `GET` | `/batches/{id}/composition` | 递归追溯原始样本占比 |
| `POST` | `/batches/{id}/recipe-preview` | 配方变更影响预演（不写库） |
| `POST` | `/batches/{id}/portion-diagnosis` | 份数诊断：假设仅一项份数记错，反推正整数份数（不写库） |

## 示例请求

### 1. 登记原始样本

```bash
curl -X POST http://localhost:8000/samples \
  -H 'Content-Type: application/json' \
  -d '{"name": "S1"}'
# {"id":1,"name":"S1","kind":"sample","created_at":"...","recipe":null}

curl -X POST http://localhost:8000/samples \
  -H 'Content-Type: application/json' \
  -d '{"name": "S2"}'
# {"id":2,"name":"S2","kind":"sample",...}
```

### 2. 创建混样批次 P1 = 1×S1 + 1×S2

```bash
curl -X POST http://localhost:8000/pools \
  -H 'Content-Type: application/json' \
  -d '{"name": "P1", "recipe": [{"batch_id": 1, "portions": 1}, {"batch_id": 2, "portions": 1}]}'
```

```json
{
  "id": 3,
  "name": "P1",
  "kind": "pool",
  "created_at": "2026-09-24T12:00:00.000Z",
  "recipe": [
    {"batch_id": 1, "batch_name": "S1", "portions": 1},
    {"batch_id": 2, "batch_name": "S2", "portions": 1}
  ]
}
```

### 3. 创建二级混样 P2 = 1×P1 + 1×S1

```bash
curl -X POST http://localhost:8000/pools \
  -H 'Content-Type: application/json' \
  -d '{"name": "P2", "recipe": [{"batch_id": 3, "portions": 1}, {"batch_id": 1, "portions": 1}]}'
```

### 4. 追溯 P2 的原始样本占比

```bash
curl http://localhost:8000/batches/4/composition
```

```json
{
  "batch_id": 4,
  "batch_name": "P2",
  "kind": "pool",
  "components": [
    {"sample_id": 1, "sample_name": "S1", "numerator": 3, "denominator": 4},
    {"sample_id": 2, "sample_name": "S2", "numerator": 1, "denominator": 4}
  ]
}
```

S1 经两条路径进入 P2（直接投入 1/2，加上经 P1 的 1/2×1/2），合并为 3/4；各占比之和恒为 1。

### 5. 预演 P1 配方变更的影响

提交完整替代配方（沿用创建时的校验：严格正整数份数、重复原料合并、未知批次拒绝），在内存中把 P1 的配方替换为 `3×S1 + 1×S2`，返回 P1 及其全部下游批次（此处为 P2）各原始样本的原占比、预演占比与有符号差值（`delta = preview − original`），均为约分后的分子/分母：

```bash
curl -X POST http://localhost:8000/batches/3/recipe-preview \
  -H 'Content-Type: application/json' \
  -d '{"recipe": [{"batch_id": 1, "portions": 3}, {"batch_id": 2, "portions": 1}]}'
```

```json
{
  "batch_id": 3,
  "batch_name": "P1",
  "recipe": [
    {"batch_id": 1, "batch_name": "S1", "portions": 3},
    {"batch_id": 2, "batch_name": "S2", "portions": 1}
  ],
  "affected_batches": [
    {
      "batch_id": 3,
      "batch_name": "P1",
      "kind": "pool",
      "components": [
        {"sample_id": 1, "sample_name": "S1", "original": {"numerator": 1, "denominator": 2}, "preview": {"numerator": 3, "denominator": 4}, "delta": {"numerator": 1, "denominator": 4}},
        {"sample_id": 2, "sample_name": "S2", "original": {"numerator": 1, "denominator": 2}, "preview": {"numerator": 1, "denominator": 4}, "delta": {"numerator": -1, "denominator": 4}}
      ]
    },
    {
      "batch_id": 4,
      "batch_name": "P2",
      "kind": "pool",
      "components": [
        {"sample_id": 1, "sample_name": "S1", "original": {"numerator": 3, "denominator": 4}, "preview": {"numerator": 7, "denominator": 8}, "delta": {"numerator": 1, "denominator": 8}},
        {"sample_id": 2, "sample_name": "S2", "original": {"numerator": 1, "denominator": 4}, "preview": {"numerator": 1, "denominator": 8}, "delta": {"numerator": -1, "denominator": 8}}
      ]
    }
  ]
}
```

说明：

- 预演原料只能引用**比目标批次更早创建**的批次（含目标自身在内的引用都会被 400 拒绝），预演不会成环；
- 多路径汇合的下游批次精确合并、只返回一次；未受影响的批次不出现在结果中；
- 某样本在新配方下占比降为 0 时，以 `0/1` 返回，差值为负；
- 预演**不修改数据库**：调用前后 `/batches/{id}` 与 `/batches/{id}/composition` 的结果保持原样。

### 6. 按目标占比规划混样配方

`POST /pool-plans` 接收两个**不同的已有批次 id**（`ingredient_a_batch_id`、`ingredient_b_batch_id`）、一个已有**原始样本** id（`sample_id`）、目标占比（`numerator` / `denominator`，要求 `0 ≤ 分子 ≤ 分母` 且分母 > 0）以及正整数总份数上限 `max_total_portions`。

接口沿用上面的精确占比计算：设两原料中该样本的占比分别为 `a`、`b`，新混样按正整数份数 `x:y` 混合后占比为 `(a·x + b·y)/(x + y)`。接口反算使该占比**恰好等于**目标的正整数份数，只返回**总份数最小**的整数解；当两原料占比都等于目标时任意份数都满足，按约定取 `1:1`。返回的 `recipe` 可**直接作为 `/pools` 的配方提交**，`components` 给出新混样的全部原始样本精确占比。接口只做规划，**不创建任何批次**。

以第 4 节的 P2（S1 占 3/4）和 S2（S1 占 0）为原料，规划 S1 恰好占 1/2、总份数上限 10：

```bash
curl -X POST http://localhost:8000/pool-plans \
  -H 'Content-Type: application/json' \
  -d '{"ingredient_a_batch_id": 4, "ingredient_b_batch_id": 2, "sample_id": 1, "numerator": 1, "denominator": 2, "max_total_portions": 10}'
```

```json
{
  "sample_id": 1,
  "target": {"numerator": 1, "denominator": 2},
  "total_portions": 3,
  "recipe": [
    {"batch_id": 4, "portions": 2},
    {"batch_id": 2, "portions": 1}
  ],
  "components": [
    {"sample_id": 1, "sample_name": "S1", "numerator": 1, "denominator": 2},
    {"sample_id": 2, "sample_name": "S2", "numerator": 1, "denominator": 2}
  ]
}
```

校验：`2×P2 + 1×S2` 中 S1 = 2/3×3/4 = 1/2。把 `recipe` 原样提交即可创建该混样：

```bash
curl -X POST http://localhost:8000/pools \
  -H 'Content-Type: application/json' \
  -d '{"name": "P3", "recipe": [{"batch_id": 4, "portions": 2}, {"batch_id": 2, "portions": 1}]}'
```

说明：

- 目标占比**无法由正整数份数达到**时返回 **422**：包括目标落在两原料占比区间之外，或目标只等于其中一端（会迫使另一原料投入 0 份）；
- 最小整数解的**总份数超过上限**时返回 **422**，错误信息中给出最小总份数与上限；恰好等于上限时正常返回；
- 两原料 id 相同、分子 > 分母、分母 ≤ 0、份数上限非正整数等请求格式问题返回 **422**；引用不存在的批次返回 **400**；`sample_id` 指向混样批次（非原始样本）返回 **400**。

### 7. 原始样本禁配规则

`POST /exclusion-rules` 登记一对**不同且已存在的原始样本** id（`sample_a_id`、`sample_b_id`）。禁配对是**无序对**：服务端按 id 归一为 `(low_sample_id, high_sample_id)` 存储并返回；请求中两个 id 的先后顺序无关。重复登记同一无序对（含交换顺序）**不会生成第二条规则**：首次返回 **201**，重复登记幂等返回 **200** 且 `created_at` 保持首次登记时间。两个 id 相同、引用不存在的批次或引用混样批次（非原始样本）返回 **400**。

```bash
curl -X POST http://localhost:8000/exclusion-rules \
  -H 'Content-Type: application/json' \
  -d '{"sample_a_id": 2, "sample_b_id": 1}'
# 201（参数顺序无关，响应按 id 升序归一）
# {"low_sample_id":1,"high_sample_id":2,"created_at":"2026-09-25T09:00:00.000Z"}

curl -X POST http://localhost:8000/exclusion-rules \
  -H 'Content-Type: application/json' \
  -d '{"sample_a_id": 1, "sample_b_id": 2}'
# 200，重复登记幂等，不生成第二条
```

`GET /exclusion-rules` 列出全部规则，按样本对字典序（先 `low_sample_id` 后 `high_sample_id`）升序：

```bash
curl http://localhost:8000/exclusion-rules
```

```json
[
  {"low_sample_id": 1, "high_sample_id": 2, "created_at": "2026-09-25T09:00:00.000Z"}
]
```

**创建混样时的禁配检查**：`POST /pools` 会沿配方图递归求新混样可达的**全部原始样本**——无论样本直接出现在配方中，还是经嵌套混样、多路径汇合间接可达——只要其中任意两个命中已登记的禁配对，即返回 **409**，且**不创建批次或配方**（失败的批次名称不会被占用）。响应 `detail.pair` 给出按 id 升序的冲突样本对；同时命中多条规则时，按样本对字典序返回**第一对**：

```bash
# P1 = S1 + S3（创建于规则登记之前的批次不受影响）
# 登记禁配 (S1, S2) 后，尝试创建 P2 = 2×P1 + 3×S2：
# P2 经 P1 嵌套可达 S1，与直接投入的 S2 构成禁配对
curl -X POST http://localhost:8000/pools \
  -H 'Content-Type: application/json' \
  -d '{"name": "P2", "recipe": [{"batch_id": 3, "portions": 2}, {"batch_id": 2, "portions": 3}]}'
```

```json
{
  "detail": {
    "message": "混样命中原始样本禁配规则",
    "pair": [
      {"sample_id": 1, "sample_name": "S1"},
      {"sample_id": 2, "sample_name": "S2"}
    ]
  }
}
```

说明：

- 规则**只约束随后创建的批次**：登记之前已经存在的批次（即使本身同时含有这两个样本）不会被改动或删除，其详情与 `/batches/{id}/composition` 占比接口照常返回；随后创建的新批次若引用旧批次、经其可达样本构成禁配对，仍会被 409 拒绝；
- `/pool-plans` 只做配方规划、不创建批次，`/batches/{id}/recipe-preview` 只做内存预演，二者均不做禁配检查；
- 规则登记与混样创建使用同一个 SQLite 写事务模型（`BEGIN IMMEDIATE` 立即获取写锁），两类写操作相互串行：并发登记的规则一旦提交，随后的混样创建必然能读到该规则，不会漏检。

### 8. 份数诊断（反推记错的投入份数）

`POST /batches/{id}/portion-diagnosis`（服务地址 `http://localhost:8000`）接收目标混样批次的**实测原始样本占比列表**与**最大份数** `max_portions`：

- `measurements`：`sample_id` + `numerator`/`denominator`（分子 ≥ 0、分母 > 0 的严格整数）。样本 id **不得重复**且必须都是**原始样本**（400）；各占比**之和必须为 1**（422）；**未列出的样本占比视为 0**；
- 目标批次必须存在（404）且为**混样批次**（400）。

假设现有配方中**仅一项投入份数记错、其余不变**：接口沿嵌套配方精确展开各原料的原始样本占比，设被记错项为 `j`（记录份数 `p_j`，实际份数 `x`），其余项份数之和 `R` 与其贡献 `B_s` 固定，则实际组成满足 `(B_s + x·A_s)/(R + x) = m_s`。接口对每个样本建立有理数方程解出唯一候选 `x`，再用**全部样本占比**逐一复核，只返回**与原值不同且不超过 `max_portions`** 的正整数解，按原料 id 升序。若某项的份数变化**不改变组成且与实测吻合**（任意正整数份数都成立），列入 `undetermined`、**不枚举份数**；无解时 `solutions` 为**空列表**。接口只做内存演算，**不修改数据库**。

沿用前面的例子：P2（id=4）记录为 `1×P1 + 1×S1`（S1 应占 3/4），实测却是 S1=5/8、S2=3/8——恰好对应 `3×P1 + 1×S1`：

```bash
curl -X POST http://localhost:8000/batches/4/portion-diagnosis \
  -H 'Content-Type: application/json' \
  -d '{"measurements": [{"sample_id": 1, "numerator": 5, "denominator": 8}, {"sample_id": 2, "numerator": 3, "denominator": 8}], "max_portions": 10}'
```

```json
{
  "batch_id": 4,
  "batch_name": "P2",
  "solutions": [
    {"ingredient_batch_id": 3, "ingredient_name": "P1", "original_portions": 1, "corrected_portions": 3}
  ],
  "undetermined": []
}
```

校验：`3×P1 + 1×S1` 中 S1 = 3/4×1/2 + 1/4×1 = 5/8，S2 = 3/4×1/2 = 3/8，与实测一致；把 S1 的份数改成任何值都无法得到该组成，故 P1 是唯一可疑项。

### 错误示例

```bash
# 空配方 → 422
curl -X POST http://localhost:8000/pools -H 'Content-Type: application/json' \
  -d '{"name": "P", "recipe": []}'

# 未知批次 → 400 {"detail":"未知批次 id: [999]"}
curl -X POST http://localhost:8000/pools -H 'Content-Type: application/json' \
  -d '{"name": "P", "recipe": [{"batch_id": 999, "portions": 1}]}'

# 非正份数（0 / 负数 / 小数 / 字符串）→ 422
curl -X POST http://localhost:8000/pools -H 'Content-Type: application/json' \
  -d '{"name": "P", "recipe": [{"batch_id": 1, "portions": 0}]}'

# 查询不存在的批次 → 404
curl http://localhost:8000/batches/999/composition

# 预演原料引用了比目标批次更晚创建的批次（或目标自身）→ 400
curl -X POST http://localhost:8000/batches/3/recipe-preview \
  -H 'Content-Type: application/json' \
  -d '{"recipe": [{"batch_id": 4, "portions": 1}]}'

# 规划目标无法由正整数份数达到（P2 与 S1 中 S1 占比为 3/4、1，目标 1/4 在区间之外）→ 422
curl -X POST http://localhost:8000/pool-plans -H 'Content-Type: application/json' \
  -d '{"ingredient_a_batch_id": 4, "ingredient_b_batch_id": 1, "sample_id": 1, "numerator": 1, "denominator": 4, "max_total_portions": 10}'

# 规划的最小整数解超过总份数上限（2/3 需要 2+1=3 份）→ 422
curl -X POST http://localhost:8000/pool-plans -H 'Content-Type: application/json' \
  -d '{"ingredient_a_batch_id": 1, "ingredient_b_batch_id": 2, "sample_id": 1, "numerator": 2, "denominator": 3, "max_total_portions": 2}'

# 登记禁配对时两个样本 id 相同 → 400
curl -X POST http://localhost:8000/exclusion-rules -H 'Content-Type: application/json' \
  -d '{"sample_a_id": 1, "sample_b_id": 1}'

# 登记禁配对引用不存在的批次 → 400 {"detail":"未知批次 id: [999]"}
curl -X POST http://localhost:8000/exclusion-rules -H 'Content-Type: application/json' \
  -d '{"sample_a_id": 1, "sample_b_id": 999}'

# 已登记禁配对 (1,2)，创建混样可达样本同时含 1 和 2（含经嵌套混样间接可达）→ 409
curl -X POST http://localhost:8000/pools -H 'Content-Type: application/json' \
  -d '{"name": "PBAD", "recipe": [{"batch_id": 1, "portions": 1}, {"batch_id": 2, "portions": 1}]}'
# {"detail":{"message":"混样命中原始样本禁配规则","pair":[{"sample_id":1,"sample_name":"S1"},{"sample_id":2,"sample_name":"S2"}]}}

# 份数诊断：实测占比之和不为 1（未列样本视为 0）→ 422
curl -X POST http://localhost:8000/batches/3/portion-diagnosis -H 'Content-Type: application/json' \
  -d '{"measurements": [{"sample_id": 1, "numerator": 1, "denominator": 3}], "max_portions": 10}'

# 份数诊断：目标不是混样批次 → 400
curl -X POST http://localhost:8000/batches/1/portion-diagnosis -H 'Content-Type: application/json' \
  -d '{"measurements": [{"sample_id": 1, "numerator": 1, "denominator": 1}], "max_portions": 10}'
```

## 占比计算说明

- 每个原始样本对自身的占比为 `1/1`；
- 混样批次中，某成分的占比 = 该成分投入份数 ÷ 本层总份数，再乘以下游展开结果，逐层递归；
- 同一原始样本经多条路径到达时，各路径占比相加合并；
- 全程使用 `fractions.Fraction` 精确有理数运算，结果自动约分，直接返回分子/分母，无浮点误差；
- 由于新批次只能引用已创建的批次，配方图无环，递归必然终止。

## 本地开发（不用 Docker）

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload          # http://localhost:8000
pytest                                 # 运行测试（使用临时数据库，互不影响）
```

## 项目结构

```
app/
  main.py       # FastAPI 路由与请求处理（含禁配规则登记/查询、目标占比规划、配方变更预演、份数诊断端点）
  db.py         # SQLite 连接与建表（启动时自动执行，含禁配规则表与写事务设置）
  schemas.py    # Pydantic 请求/响应模型（含严格正整数校验）
  service.py    # 递归占比计算、配方图可达原始样本、目标占比反算、配方预演与份数诊断（Fraction 精确分数）
tests/
  test_api.py   # 端到端测试（含多级合并、约分、占比规划、配方预演、份数诊断、禁配规则、并发与各类非法输入）
Dockerfile
docker-compose.yml
requirements.txt
requirements-dev.txt
```
