# LoveApp

LoveApp 是一个面向单用户的恋爱沟通与约会决策 Agent。当前版本可在终端运行，核心逻辑不依赖具体 UI。

## 当前能力

- 基于 LangGraph 的恋爱咨询工作流
- 基于规则置信度与 DeepSeek 校正的分层混合路由
- 普通对话、关系建议、约会规划和高风险响应的顶层 LangGraph 分支
- 关系建议的主次 AdviceGoal、主次 AdviceScenario 与多标签 RAG 检索
- 六类关系场景的 ScenarioPolicy、主次检索配额和生成后硬约束校验
- 一个问答一个 chunk 的 Markdown 知识解析器
- 本地中文 Embedding 与 Qdrant 持久化检索
- 竞态安全的后台 Embedding 预热、单轮单次向量化与分阶段 RAG Trace
- DeepSeek 结构化建议生成与知识引用
- 高风险内容的独立安全分支
- 高德真实 POI、营业信息和路线规划
- 按区域、偏好、距离、评分和预算组合约会行程
- 持久化的约会任务状态：城市、区域、日期时间、预算、偏好、交通和限制条件可跨轮补充
- 可选天气 provider；天气只作为室内/户外排序的软约束，查询失败不会阻断规划
- SQLite 持久化的关系记忆、结构化抽取与关系隔离
- 本地记忆 Gate、后台抽取、尝试级耗时/重试/token Trace
- 咨询和约会规划自动读取相关偏好、事件、趋势与建议结果
- 可替换的知识、模型、用户记忆和地图适配器
- Pydantic 结构化输入与输出
- Markdown、JSON、JSONL 知识校验和入库

`LOVEAPP_LLM_PROVIDER=demo` 时使用确定性模板生成器，配置 DeepSeek 后使用真实模型。`LOVEAPP_MAP_PROVIDER=amap` 使用真实高德数据，设置为 `demo` 可离线测试约会工作流。

## 环境

- Python 3.12
- 推荐使用 uv

```powershell
uv sync --extra dev
```

从 `.env.example` 创建 `.env`，填入 DeepSeek 与高德 Web 服务 Key。不要把真实 Key 写入 `.env.example` 或提交到 Git。

关系记忆默认保存在 `.data/loveapp.db`。该 SQLite 文件会在第一次使用时自动创建，程序退出后数据仍然存在；`.data/` 已被 Git 忽略。

启动本地 Qdrant：

```powershell
docker compose up -d
```

首次入库会通过 ModelScope 下载约 200MB 的中文 BGE Embedding 文件，并缓存在项目的 `.cache/` 中：

```powershell
uv run loveapp knowledge validate loveapp_rag_knowledge_base_formal_v1.md
uv run loveapp knowledge ingest loveapp_rag_knowledge_base_formal_v1.md --recreate
uv run loveapp knowledge search "和对象吵架后怎么沟通？"
```

入库时会先把内置 Seed 和正式文档统一为同一个数据模型，再按 ID 与规范化问题去重。当前数据会得到 56 条文档。`chat` 启动后会在等待用户输入时后台预热 Embedding；如果首个请求更早到达，它会等待同一个预热任务，不会重复加载模型。

Qdrant Dashboard：`http://localhost:6333/dashboard`

如果咨询时报错 `Unexpected Response: 502 (Bad Gateway)` 且失败阶段为 `RAG 检索`，该异常来自 Qdrant HTTP 客户端。先确认 Docker Desktop 已启动，再执行：

```powershell
docker compose up -d
uv run loveapp knowledge search "测试检索"
```

## 终端运行

恋爱咨询：

```powershell
uv run loveapp advice "和对象吵架后应该怎么开口道歉？" --stage dating
```

多轮咨询建议使用交互模式。固定的 `conversation_id` 保存短期消息历史，`relationship_id` 负责隔离长期关系记忆：

```powershell
uv run loveapp chat --user-id local-user --relationship-id current-partner --debug-memory
```

交互中输入 `/quit` 退出，输入 `/new` 开始一个新的短期会话；新会话仍会读取同一关系的长期记忆。`chat` 会根据每轮输入自动进入普通对话、关系建议或约会规划。约会规划会持久化一个独立的短期任务状态：首次缺少关键参数时只追问一次，后续仍未补充就生成通用草案并忽略无法确认的地点、路线或天气；之后用户再输入“上海”“预算 300”“周六下午”等内容，会被识别为同一任务的参数补充。

恋爱咨询和约会规划共用同一个关系记忆侧路。约会规划的当前城市、日期和预算属于短期任务状态；用户/对方的饮食禁忌、活动偏好和已发生事件进入长期记忆，并在下一轮约会搜索时分别作为排除条件或排序依据。明确的菜系和地点类型会进入高德精确检索，例如：

```powershell
uv run loveapp plan-date --city 上海 --area 静安区 `
  --dining-keywords 西餐 --activity-keywords 博物馆
```

高德搜索会同时传递区域和关键词，并在本地校验行政区、POI 类型和必需关键词；没有精确匹配时不会静默降级为其他菜系。

开发版本默认开启结构化流式预览、路由详情和模块耗时。流式预览只展示已经完成并经过场景策略过滤的 JSON 字段，不展示模型推理内容；最终答案仍会执行完整硬约束。可以分别使用 `--no-stream`、`--no-debug-route` 和 `--no-timings` 关闭。耗时表会继续显示尚未完成的后台记忆任务，并展示 Embedding 是否已就绪、候选数、Gate 决策和模型 token 等 Trace 详情；并行模块不能直接相加。

`--debug-route` 会显示规则得分、主次目标、主次场景、置信度以及是否使用 LLM 校正。耗时表会区分路由、记忆抽取、上下文、RAG、模型生成、策略执行、消息持久化和地图检索，失败时也会标出实际失败阶段。

路由模型可通过 `LOVEAPP_ROUTER_TIMEOUT_SECONDS`、`LOVEAPP_ROUTER_MAX_RETRIES` 和 `LOVEAPP_ROUTER_THINKING` 单独控制；默认关闭推理以控制延迟，超时会回退到规则结果。

约会调试信息还会显示 `date_intent`（新建、补充、继续、切换或取消）、缺少字段和当前任务状态。真实高德查询会把已知城市名先转换为行政区划编码，并丢弃返回结果中明确属于其他城市的 POI；空路线会降级为计划说明，不会让整轮对话失败。

路由按以下顺序执行：保留原文的文本标准化、安全规则扫描、TaskType 打分、AdviceGoal 与 AdviceScenario 多标签打分、低置信度或复合意图 LLM 校正、Python 结构校验与 LangGraph 条件路由。约会候选短语只负责触发语义复核，最终是否属于约会规划由 LLM 根据当前对话和任务状态判断；明确的城市、预算、日期补充也会结合已有状态判断是 `supplement` 还是任务切换。安全规则拥有最高优先级；高置信度的一级 `TaskType` 也会被保护，LLM 在这类请求中只校正 Goal/Scenario，不能把关系建议或约会规划降级成 `general_chat`。路由模型单独使用 `LOVEAPP_ROUTER_TIMEOUT_SECONDS` 和 `LOVEAPP_ROUTER_MAX_RETRIES`，超时后回退到规则结果，不拖住最终回答链路。调试路由会同时显示 `rule_task`、`llm_task` 和 `task_guard`，便于区分规则结果与模型建议。

关系建议进入二级路由后，会解析 `ScenarioPolicy`。每个场景分别定义生成规则、硬约束、允许的回答区块和检索权重；原来的 `3 + 2` 或 `3 + 1 + 1` 配额会转换成主次场景软权重。Qdrant 全库召回 15 个候选后，再结合标题、标签、Goal、Scenario 和关系阶段重排，因此错误路由不会把其他场景文档直接过滤掉。合并后的规则会传给 DeepSeek，返回结果还会经过 Python 后处理，过滤操控、纠缠和越界建议。

约会规划：

```powershell
uv run loveapp plan-date --city 杭州 --area 西湖 --budget 500 --preferences "安静,咖啡,展览"
# 已配置天气 provider 时，可按日期调整室内/户外排序
uv run loveapp plan-date --city 杭州 --date 2026-07-25 --budget 500 --preferences "展览,咖啡"
# 单城市多日约会旅行；预算按天计算
uv run loveapp plan-date --city 上海 --date 2026-08-07 --end-date 2026-08-09 `
  --budget 500 --budget-scope per_day
```

多日模式当前支持单城市 1～5 天。聊天中可以直接输入“三天两夜”“周五到周日”或“每天预算 500 元”；生成结果按天保存地点、天气、费用和路线，跨夜后会重新计算路线。已有计划支持“第二天下午换成博物馆”一类定向修改，未受影响的日期会保留。住宿要求会进入任务状态和逐日备注，但当前版本不自动搜索酒店，也不处理跨城市交通。

高德模式会返回真实地址、评分、营业时间、人均消费、路线和地图跳转链接。API 未返回价格时会明确标记为估算；营业状态、价格和预约情况仍需在出发前确认。适配器内置串行限速和 QPS 重试。城市名会归一化为 Amap adcode（例如 `上海 -> 310000`）。天气默认关闭；需要时在 `.env` 设置 `LOVEAPP_WEATHER_PROVIDER=amap`，只有日期和城市都已知才会请求天气接口。

输出原始 JSON：

```powershell
uv run loveapp advice "她最近回复很冷淡，我应该怎么办？" --json
uv run loveapp plan-date --city 杭州 --area 西湖 --budget 500 --preferences "展览,咖啡" --json
```

## 关系记忆

咨询命令会从用户陈述中抽取八类记忆：`stable_fact`、`preference`、`interaction_event`、`interaction_pattern`、`advice_outcome`、`planned_event`、`action_intent` 和 `relationship_state`。其中 `interaction_event` 只表示已经发生的互动，`planned_event` 表示有明确时间的未来安排，`action_intent` 表示尚未确定日期的具体行动，`relationship_state` 表示熟悉度、接触机会、联系可用性、冲突状态或互动互惠性等可变化状态。每条 `planned_event` 还会映射到独立的 `RelationshipPlan`，按 `proposed -> confirmed -> completed/cancelled/expired` 管理生命周期；完成事件优先按计划 ID 关联，历史数据再按活动、参与人和时间回退匹配。正向、负向、混合或中性是记忆属性，不会把事件和模式混成一种类型。抽取前的本地 Gate 会跳过纯寒暄、普通知识问题、格式指令、Agent 元问题、纯假设和没有事实陈述的纯咨询；包含事实、当前状态、一般回顾事件或具体未来安排的混合提问仍会进入模型。

默认关系 ID 是 `primary`。同一用户涉及不同对象时必须传入不同 ID，避免串用上下文：

```powershell
uv run loveapp advice "她最近回复变少了，我应该怎么沟通？" --relationship-id current-partner
uv run loveapp plan-date --city 杭州 --budget 500 --relationship-id current-partner
```

手动抽取并检查记忆：

```powershell
uv run loveapp memory remember "我们最近两周每晚都会通话。" --relationship-id current-partner
uv run loveapp memory list --relationship-id current-partner
uv run loveapp memory plans --relationship-id current-partner
uv run loveapp memory context --relationship-id current-partner
uv run loveapp memory show <memory-id>
```

调试多轮对话时，可以在第二个终端持续观察 SQLite 中已经提交的记忆：

```powershell
uv run loveapp memory watch --user-id local-user --relationship-id current-partner
```

交互式 `chat` 不等待正样例的记忆模型完成才返回答案，`--debug-memory` 会显示“后台处理中”，完成后可由 `memory watch` 看到；程序退出时最多等待 `LOVEAPP_MEMORY_SHUTDOWN_GRACE_SECONDS`。单次 `loveapp advice` 默认等待记忆完成，避免命令退出导致本轮记忆丢失。

记忆抽取遵循“一条可独立确认、更新或删除的信息对应一条记忆”。熟悉度、接触机会、实际联系频率、话题范围、互动渠道和主动性是不同维度；同一条输入中的独立状态或指标必须分别输出。关系状态使用注册的 `state_dimension/state_value`，同一维度的新值会将旧值标记为 `superseded`，不同维度可以同时存在。一个主事实可以携带必要的渠道、共同场景或社会关系限定，例如 `contact_frequency + channel=online` 仍是一条频率模式；本地校验只拒绝把多个不兼容的主维度合成一条 claim。证据唯一明确指向另一个注册互动维度时，错误的 `metric/predicate` 会在本地一致性修复后再校验。

模型自动抽取的记忆状态为 `proposed`，明确从约会参数写入的偏好为 `confirmed`。计划事件会额外保留 `period_start/period_end` 和 `expires_at`，过期后不会进入 Agent 的有效关系上下文。两者都会进入上下文，但保留状态供模型和用户区分；可以确认、拒绝或永久删除：

```powershell
uv run loveapp memory confirm <memory-id>
uv run loveapp memory reject <memory-id>
uv run loveapp memory delete <memory-id> --yes
uv run loveapp memory clear --relationship-id current-partner --yes
```

`memory list --json` 可以查看全部结构化字段，`memory plans --json` 可以查看计划 ID、活动、参与人、计划时间、状态及源记忆。需要直接检查数据库时，可使用 SQLite 客户端或 DB Browser for SQLite 打开 `.data/loveapp.db`；核心表为 `relationships`、`conversations`、`messages`、`memory_items`、`relationship_plans` 和 `date_planning_tasks`。最后一张表是短期约会工作流状态，不会混入长期关系记忆。

`memory watch` 默认按 `user_id + relationship_id` 观察该关系的活动记忆和活动计划（都只显示 `proposed/confirmed`），同时显示最近的 Gate 与抽取运行记录。增加 `--include-inactive` 后可看到计划的 `completed/cancelled/expired` 历史。它不是单个会话的视图；需要定位某次 `chat` 时传入会话 ID：

```powershell
uv run loveapp memory watch --user-id local-user --relationship-id current-partner --conversation-id <conversation-id>
uv run loveapp memory watch --user-id local-user --relationship-id current-partner --include-inactive
uv run loveapp memory runs --user-id local-user --relationship-id current-partner --conversation-id <conversation-id> --json
```

运行记录会区分 `skipped`、`running`、`completed`、`failed` 和 `cancelled`，并保存 Gate 原因、模型尝试耗时/token、错误信息、局部无效 claim 的校验原因、实际写入的记忆 ID 以及未写入片段的原文和原因。claim 证据与 discarded span 不允许重叠。功能上线前已经产生的历史抽取不会自动补写 discarded 明细，因此旧运行记录只保留当时的计数。

记忆模型采用 Flash 优先的两级链路：Flash 请求明确关闭 DeepSeek thinking，先在本地清理代码围栏、尾逗号并补齐安全的根数组字段；无法安全修复的格式/结构错误直接丢弃。覆盖缺口会记录为诊断信号，但普通漏抽不会单独触发强模型；只有重要信息出现语义校验失败、低置信度、复杂指代、潜在冲突或高价值事实缺失时才升级。Flash 与强模型分别使用 `LOVEAPP_MEMORY_EXTRACTION_TIMEOUT_SECONDS`、`LOVEAPP_MEMORY_EXTRACTION_MAX_RETRIES`、`LOVEAPP_MEMORY_EXTRACTION_MAX_TOKENS` 和对应的 `STRONG_*` 配置；默认值分别为 `30s/0/1536` 与 `60s/1/4096`。

普通记忆默认要求置信度不低于 `0.65`；`source_type=hearsay` 的暂定记忆允许降到 `LOVEAPP_MEMORY_TENTATIVE_MIN_CONFIDENCE`（默认 `0.5`），`user_belief` 允许降到 `LOVEAPP_MEMORY_BELIEF_MIN_CONFIDENCE`（默认 `0.4`）。它们仍以 `proposed` 状态保存，不会被当作已确认事实。

只测记忆抽取耗时时，使用隔离的内存后端 smoke：

```powershell
uv run python scripts/benchmark_memory_extraction.py
```

脚本不会输出 API Key，也不会写入 `.data/loveapp.db`；输出包含 Flash 直成功率、本地修复次数、强模型升级次数和每轮耗时。固定的多轮记忆评测集仍保存在 `evals/memory/conversations_v1.jsonl`，不与单元测试复用语料。

如果历史数据中已有重复记忆，先预览再做可逆的状态归档；命令不会物理删除记录：

```powershell
uv run loveapp memory compact --user-id local-user --relationship-id current-partner --json
uv run loveapp memory compact --user-id local-user --relationship-id current-partner --apply
```

## 测试与检查

```powershell
uv run pytest
uv run ruff check .
```

运行固定 baseline：

```powershell
uv run loveapp eval baseline --output evals/baselines/current.json
uv run loveapp eval baseline --no-live-memory --output evals/baselines/rag-only.json
```

报告包含路由准确率、RAG Recall@3/5 与 MRR、高风险召回率/精确率/特异度、记忆污染率、Gate 召回率/特异度和尝试级 Trace。指标只描述仓库中的固定小样例，扩充数据集后才适合设定更可靠的上线阈值。

## 结构

```text
src/loveapp/
├── adapters/       # Qdrant、DeepSeek、高德等基础设施实现
├── agents/         # LangGraph 工作流
├── core/           # 配置
├── domain/         # 领域模型
├── ports/          # RAG、地图、记忆接口
├── resources/      # 种子知识
├── safety/         # 安全策略
├── bootstrap.py    # 依赖装配
└── cli.py          # 终端入口
```

下一阶段可在现有端口边界上加入 FastAPI，并扩充人工标注的路由、RAG、安全和记忆评测集。
