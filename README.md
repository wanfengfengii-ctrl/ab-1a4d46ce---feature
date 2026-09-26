# 化工园区事故导排网络 · 检修前审计 + 薄弱管段复核

安全工程师在检修事故导排总管前，录入：

- **一个泄压源**、**一个安全焚烧端**、若干**汇合节点**；
- 若干带**方向**、**最大流量（容量上限）**、**可检修标记**的管段；
- 事故时必须持续排出的流量。

系统在**正常网络**与**每一条可检修管段单独临时失效后的残余网络**上，
分别独立运行最大流（Dinic），给出每个情景的**最大可导排量**。
所有情景均不低于事故要求流量才放行；失败时按管段**录入顺序**
返回首条不达标管段，并依据最大流 / 最小割定理给出可复核的
**源侧割集节点、焚烧端侧节点、割集管段与割集容量**。

审计通过后，可发起**薄弱管段复核**：服务端从完整草稿**重新执行既有审计**，
并对每个达标情形判定每条管段在**全部**容量等于该情形最大可导排量的
源侧最小割中的归属——**从不跨割 / 仅部分最小割跨越 / 全部最小割均跨越**——
同时给出**最小割容量**与**相对事故要求流量的裕量**。这样能把一次最大流
运行恰好返回的割集中的**偶然管段**，与所有同容量瓶颈中**均不可绕开**的
管段区分开；任一情形本已不达标时，复核保留既有首条失败证据，
不生成任何薄弱分级。

> 结论以**流量**为准而非路径条数：存在多条路径不代表总排量达标，
> 共享瓶颈会限制总流量（见 `tests/test_flow.py::test_shared_bottleneck_not_path_count`）。

## 快速开始（Docker Compose）

```bash
# 构建并启动常驻 Web 服务（默认宿主机端口 8080，可配置）
docker compose up -d --build web
# 浏览器打开 http://localhost:8080

# 自定义宿主机端口
WEB_HOST_PORT=9090 docker compose up -d web
# 或复制 .env.example 为 .env 后修改 WEB_HOST_PORT
```

健康检查：

```bash
curl http://localhost:8080/health
# {"status":"ok","service":"flare-audit", ...}
```

## 一次性交付校验服务 verify

`verify` 是一次性服务：依次运行 **pytest 代码测试 → 构建检查
（字节码编译 + 应用导入）→ 真实拉起 uvicorn 的导排 API 冒烟**，
随后自行退出，**退出码即结论**（0 全部通过，非 0 存在失败项）：

```bash
docker compose build verify
docker compose run --rm verify
echo "exit code = $?"
```

冒烟覆盖：健康检查、达标网络放行、失效网络返回首条失效管段且
割集容量 == 最大流、薄弱管段复核的全最小割归属分级与裕量、
复核不达标草稿保留失败证据且不生成薄弱分级、非法节点引用返回 400。

## 本地开发（不使用 Docker）

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

pytest -q                      # 代码测试
python scripts/verify          # 与容器内一致的一次性校验
uvicorn app.main:app --reload  # 开发服务
```

## 业务 API

### `POST /api/audit`

请求体：

```json
{
  "source": "泄压源V-101",
  "sink": "焚烧炉F-1",
  "required_flow": 95,
  "nodes": ["汇合点A", "汇合点B"],
  "edges": [
    {"id": "E1", "from": "泄压源V-101", "to": "汇合点A", "capacity": 100, "maintainable": true}
  ]
}
```

- `nodes` 仅列汇合节点；泄压源与安全焚烧端自动并入节点集合。
- `capacity` 为管段容量上限，必须为正数；方向为 `from → to`，不可逆向。
- 仅 `maintainable: true` 的管段参与“单管段临时失效”模拟。

响应（失败时节选）：

```json
{
  "passed": false,
  "required_flow": 95,
  "normal": {"max_flow": 100, "meets": true, "cut": { ... }},
  "scenarios": [
    {"position": 1, "edge_id": "E1", "from": "...", "to": "...",
     "capacity": 100, "max_flow": 90, "meets": false}
  ],
  "failure": {
    "stage": "single_failure",
    "position": 1,
    "edge_id": "E1",
    "max_flow": 90,
    "required_flow": 95,
    "cut": {
      "capacity": 90,
      "source_side_nodes": ["泄压源V-101", "汇合点B"],
      "sink_side_nodes": ["汇合点A", "焚烧炉F-1"],
      "cut_edges": [ {"position": 3, "id": "E3", "from": "...", "to": "...", "capacity": 90} ]
    }
  }
}
```

割集可独立复核：把节点按 `source_side_nodes / sink_side_nodes` 两分组，
所有从源侧指向焚烧端侧的管段容量之和应恰为 `capacity`，且依据
最大流 / 最小割定理等于该情景最大可导排量。

输入无效（方向自环、容量非正数、节点引用不存在、源汇相同、必填为空等）
返回 `HTTP 400`：

```json
{"error": "第 1 条管段终点“X”未在节点中定义", "field": "edges[0].to"}
```

### `POST /api/review`

薄弱管段复核。请求体与 `POST /api/audit` 相同（完整草稿）；服务端
**从草稿重新执行既有审计**，再对每个**达标**情形在残余网络上判定
每条管段在全部源侧最小割中的归属（等价于枚举全部容量等于该情形
最大可导排量的最小割后的归属统计，判定本身在残余网络上多项式完成）：

- `all`：全部最小割均跨越——任何同容量瓶颈都不可绕开该管段；
- `some`：仅部分最小割跨越——可替代瓶颈，存在不经过它的同容量最小割；
- `never`：从不跨割——非瓶颈（未满载管段必然落入此档）。

响应（达标时节选）：

```json
{
  "passed": true,
  "required_flow": 50,
  "scenarios": [
    {
      "stage": "normal",
      "removed": null,
      "max_flow": 100,
      "meets": true,
      "min_cut_capacity": 100,
      "margin": 50,
      "classified": true,
      "edges": [
        {"position": 3, "edge_id": "E3", "from": "B", "to": "T", "capacity": 100,
         "crosses": "all", "in_returned_cut": true},
        {"position": 1, "edge_id": "E1", "from": "S", "to": "A", "capacity": 100,
         "crosses": "never", "in_returned_cut": false}
      ],
      "summary": {"all": 1, "some": 0, "never": 3}
    },
    {
      "stage": "single_failure",
      "removed": {"position": 1, "edge_id": "E1", "from": "S", "to": "A", "capacity": 100},
      "max_flow": 60, "meets": true,
      "min_cut_capacity": 60, "margin": 10,
      "classified": true, "edges": [ ... ], "summary": { ... }
    }
  ],
  "failure": null
}
```

- `scenarios` 第一项固定为正常网络（`stage: "normal"`），其后按管段
  录入顺序列出每条可检修管段的单点失效情形；被移除的管段不参与
  该情形的分级列表。
- `min_cut_capacity` ＝ 该情形最大可导排量（最大流 / 最小割定理）；
  `margin` ＝ `min_cut_capacity − required_flow`，即相对事故必须持续
  排出量的裕量。
- `in_returned_cut` 标记该管段是否恰好落在本次最大流运行返回的
  单个割集中：`in_returned_cut=true` 而 `crosses≠"all"` 的管段即
  “单次割集中的偶然管段”，并非所有同容量瓶颈都经过它。

若某情形本已不达标（审计不通过），复核**保留既有首条失败证据**
（`failure` 与 `POST /api/audit` 完全一致），且**不生成任何薄弱分级**
（各情形 `classified=false`、`edges=null`）：

```json
{
  "passed": false,
  "scenarios": [{"stage": "normal", "max_flow": 100, "meets": true,
                 "classified": false, "edges": null, "margin": null, ...}],
  "failure": {"stage": "single_failure", "position": 1, "edge_id": "E1",
              "max_flow": 90, "cut": {"capacity": 90, ...}}
}
```

输入校验与 `POST /api/audit` 相同，非法输入返回 `HTTP 400`。

### `GET /health`

容器健康检查端点，返回 `{"status":"ok",...}`。

## 前端交互约定

- 页面分区：**当前输入（草稿）**、**输入被拒绝（400）**、**审计结论**、
  **薄弱管段复核结论**。
- 提交审计后通过真实业务 API 渲染正常网络与逐条失效情形的最大可导排量。
- 通过时显示放行结论；失败时高亮首条失效管段并展示最小割证据。
- 点击“薄弱管段复核”后，页面经 `POST /api/review` 展示逐情形的
  **导排裕量**、**全部最小割均跨越（不可绕开）**、
  **仅部分最小割跨越（可替代瓶颈）**与**从不跨割（非瓶颈）**三档分级；
  恰好落在单次返回割集中却非必经的管段会被标记为“单次割集偶然包含”。
  复核未放行时展示既有首条失败证据，不渲染任何分级。
- 草稿在上次审计 / 复核之后被任何修改时，对应旧结论区顶部出现过期警示，
  旧结论不会被当作新草稿的结果；重新提交后才刷新。

## 项目结构

```
app/
  flow.py            # Dinic 最大流 + 残余网络最小割 + 审计编排、业务校验
                     #   + 薄弱管段复核（全部最小割归属分级与裕量）
  main.py            # FastAPI：/api/audit、/api/review、/health、静态页面
  static/            # 原生前端（无构建步骤）
tests/               # pytest：引擎/审计/复核逻辑 + API
scripts/verify       # 一次性校验：测试 + 构建 + API 冒烟（退出码报告）
Dockerfile
docker-compose.yml   # web（常驻，健康检查，端口可配）+ verify（一次性）
```
