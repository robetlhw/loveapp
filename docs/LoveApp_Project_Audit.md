# LoveApp 面试项目事实审计

审计日期：2026-08-03  
审计范围：源代码、运行配置、测试、评测集与历史报告、SQLite 数据结构与只读数据快照、Qdrant 本地存储配置、LangGraph 工作流、依赖清单、调试脚本和 Git 元数据。  
审计原则：以当前代码和可复现检查为准；README 与复盘文档只作为辅助证据；未读取或输出 API Key。

> 2026-08-06 更新：本文保留为改造前事实快照。长期记忆的事务边界、Predicate、准入、Strong Verifier、审计表、测试基线和已知限制已由 [Memory_System_V2.md](Memory_System_V2.md) 更新；涉及这些主题时以后者为准。

## 0. 状态标记与结论边界

- [已实现]：当前源代码中存在可执行实现，并且有测试、运行数据或明确调用路径支撑。
- [部分实现]：核心路径存在，但覆盖面、容错、评测或产品化程度不完整。
- [设计中]：仓库只保留下一步方向，当前没有可执行实现。
- [已发现缺陷]：本次审计可以从代码、测试、数据或运行检查中复现的缺陷。
- [无法从仓库确认]：缺少 Git 历史、运行中的外部服务、日志或有效评测，不能把推测写成事实。

本次审计没有修改业务代码或数据库，只新增本文档。

### 0.1 本次可复现检查

| 检查 | 当前结果 | 证据 |
|---|---:|---|
| pytest 收集 | 295 tests | pyproject.toml:40；本次 uv run pytest --collect-only -q |
| pytest 全量执行 | 294 passed，1 failed，66.89s | tests/test_memory_lifecycle.py:28、92；本次 uv run pytest -q |
| Ruff | All checks passed | pyproject.toml:44-50；本次 uv run ruff check . |
| compileall | 通过 | 本次 uv run python -m compileall -q src tests evals |
| 测试函数定义数 | 244 | tests/ 下 31 个测试文件；参数化后收集 295 项 |
| 源码文件数 | 78 | src/loveapp |
| 评测与 baseline 文件数 | 19 | evals |
| 调试脚本数 | 3 | scripts |
| 项目日志文件 | 0 个 .log 文件 | 本次文件扫描 |
| Git 历史 | main 分支，无 HEAD 提交；所有可跟踪文件均为 untracked | git status --short；git rev-parse --verify HEAD |

[已发现缺陷] 当前唯一失败测试要求“对方道歉后，旧的 ignoring_user 状态变为 superseded”，实际变成 expired。MemoryService 使用测试注入时钟生成 expires_at，但 InMemoryMemoryStore.save_memory/list_memories 又直接使用 utc_now() 做过期判断，导致测试时钟与存储时钟不一致。证据：src/loveapp/application/memory.py:83、168；src/loveapp/adapters/memory/in_memory.py:83-84、177-186、319-328；tests/test_memory_lifecycle.py:28-104。

[无法从仓库确认] 因仓库没有任何 Git commit，无法从 Git 还原功能演进、作者提交边界、回归首次引入提交或真实开发时间线。evals/baselines 与 INTERVIEW_PROJECT_NOTES.md 可以说明保存过的历史结果，但不能替代 Git 历史。

## 1. 项目定位

### 1.1 业务问题

[已实现] LoveApp 是终端形态的恋爱沟通与约会决策 Agent，当前一级业务由普通对话、关系建议和约会规划组成；高风险不是第四个业务任务，而是独立安全覆盖分支。证据：pyproject.toml:6-24；src/loveapp/domain/enums.py:34-37、91-94；src/loveapp/agents/conversation.py:108-132。

[已实现] 关系建议覆盖追求、冲突、聊天分析、关系维护、边界和分手六个二级场景，并用七种 AdviceGoal 表达用户真正想完成的动作。证据：src/loveapp/domain/enums.py:4-10、24-31。

[已实现] 约会规划不是只生成一段文案。它收集城市、区域、日期、多日窗口、预算、餐饮与活动关键词、餐次、顺序、替换对象、排除项、交通和住宿备注，调用地图/天气适配器后生成结构化 DatePlan。证据：src/loveapp/domain/routing.py:24-46；src/loveapp/domain/date_plan.py:18-89、92-182。

### 1.2 为什么单轮 ChatBot 或简单 RAG 不够

[已实现] 请求需要先区分“评价一个约会想法”与“让系统执行地点搜索/行程规划”。例如“我想约她吃饭，你看怎么样”应进入关系建议，而“帮我找静安区西餐厅并安排路线”才进入约会任务。这个差异由 DateRequestMode、LLM 校正和 Python merge guard 共同表达，不是单纯关键词分类。证据：src/loveapp/domain/enums.py:40-45；src/loveapp/adapters/routing/openai_compatible.py:165-204；src/loveapp/application/routing.py:1185-1208。

[已实现] 多轮中需要持续保存两类不同状态：跨会话的关系记忆，以及仅服务当前约会工作流的 DatePlanningTaskState。简单单轮 ChatBot 无法可靠处理“上海”“预算 1000”“第二天换博物馆”这类省略式补充。证据：src/loveapp/domain/date_task.py:18-75；src/loveapp/adapters/date_tasks.py:42-139。

[已实现] 关系事实有生命周期：一次事件、长期互动模式、当前关系状态、未来计划和主观判断不能混为同一段 history。系统因此引入原子 claim、状态迁移、计划状态机、关系证据投影和按角色配额的上下文装配。证据：src/loveapp/domain/memory.py:15-74、287-407；src/loveapp/domain/memory_lifecycle.py:24-58；src/loveapp/domain/relationship_evidence.py:21-134。

[已实现] 恋爱领域包含暴力、跟踪、强迫、自伤和未经同意的亲密行为等风险；这些必须在开放式模型路由前由确定性规则覆盖。证据：src/loveapp/safety/policy.py:15-70；src/loveapp/application/routing.py:41-57。

[部分实现] RAG 为咨询提供领域问答依据，但当前知识库主要是 synthetic_draft，规模小，不能替代成熟的人工审核知识体系。证据：src/loveapp/adapters/knowledge/markdown.py:101-115；loveapp_rag_knowledge_base_formal_v1.md 的 50 个二级标题问答块。

### 1.3 当前能力边界

[已实现] 当前可用入口是 Typer/Rich CLI；支持交互式多轮 chat、单次 advice、plan-date、knowledge、memory 和 eval 子命令。证据：pyproject.toml:34-35；src/loveapp/cli.py:61-66、150-224、224-470、505-551。

[设计中] 仓库没有 FastAPI、WebSocket、网页前端或移动端实现。README 把 FastAPI 放在“下一阶段”，依赖清单也没有 Web 框架。证据：pyproject.toml:11-25；README.md:229。

[部分实现] 代码可以按 user_id 与 relationship_id 隔离数据，但产品仍是本地单用户开发形态，没有认证、授权、租户治理、隐私导出或生产部署方案。证据：src/loveapp/domain/advice.py:17-45；src/loveapp/adapters/memory/sqlite.py:1406-1548。

[设计中] 没有订餐、订票、酒店搜索/预订、跨城交通购买、提醒通知或日历写入。住宿当前只是 lodging_notes，代码明确不会自动搜索酒店。证据：src/loveapp/domain/date_plan.py:41-43；src/loveapp/agents/date_planner.py:835-847。

## 2. 整体架构

### 2.1 架构图

~~~mermaid
flowchart TD
    U[用户 / Typer CLI] --> C[ConversationAgent StateGraph]
    C --> H[load_history<br/>SQLite messages + date task]
    H --> R[HybridRouter]
    R --> S[SafetyPolicy deterministic scan]
    S -->|high| HR[high_risk_response]
    R -->|general_chat| GC[casual_chat]
    R -->|relationship_advice| A[AdviceAgent StateGraph]
    R -->|date_planning| D[DatePlanningAgent StateGraph]

    A --> AP[ScenarioPolicy<br/>prompt rules + hard constraints + sections + quota]
    A --> K[QdrantKnowledgeStore]
    K --> E[Local BGE embedding]
    A --> MCTX[Relationship context projection]
    AP --> LLM[DeepSeek final answer]
    K --> LLM
    MCTX --> LLM
    LLM --> PE[Python policy enforcement]

    D --> DM[Unified relationship memory]
    D --> W[WeatherProvider]
    D --> MAP[AmapMapProvider]
    DM --> DP[Deterministic itinerary builder]
    W --> DP
    MAP --> DP
    DP --> DTS[SQLite date_planning_tasks]

    A -. user message sidecar .-> MG[Memory Gate]
    D -. user message sidecar .-> MG
    MG --> F[DeepSeek Flash extraction]
    F --> V[Local repair + per-claim validation]
    V -->|important semantic uncertainty| STRONG[DeepSeek Pro fallback]
    V --> ML[dedupe + lifecycle + plan matching]
    STRONG --> ML
    ML --> DB[(SQLite)]

    GC --> DB
    HR --> DB
    PE --> DB
    DTS --> DB
~~~

### 2.2 一次用户请求的真实数据流

1. [已实现] ConversationAgent 为无 conversation_id 的请求生成 UUID，建立 ExecutionTrace，然后调用顶层 StateGraph。证据：src/loveapp/agents/conversation.py:67-105。
2. [已实现] load_history 最多等待同一关系的后台记忆任务 2 秒，再加载近期消息、关系上下文和当前约会任务状态。证据：src/loveapp/agents/conversation.py:134-156；src/loveapp/core/config.py:70-73。
3. [已实现] HybridRouter 先标准化文本、执行 SafetyPolicy，再运行 Python 规则；只有符合语义校正条件时才调用 Router LLM。失败时返回规则结果并记录 llm_error。证据：src/loveapp/application/routing.py:41-74、181-187。
4. [已实现] 顶层图按 risk/task 进入 high_risk_response、relationship_advice、date_planning 或 casual_chat。证据：src/loveapp/agents/conversation.py:108-132、887。
5. [已实现] 关系建议图并行装配关系上下文和执行 RAG，二者汇合后调用最终模型，再由 Python 强制执行场景策略并保存回复。证据：src/loveapp/agents/advice.py:97-127、225-286。
6. [已实现] 约会分支先合并当前 slots 和持久化任务状态；必要时只追问一次，否则调用日期图并保存 current_plan、plan_version 与 last_mutation。证据：src/loveapp/agents/conversation.py:293-445、511-615、845-880。
7. [已实现] 用户消息持久化后会启动记忆侧路。交互式 chat 不等待该侧路才返回；下一轮最多等待 2 秒，进程关闭最多等待 10 秒。证据：src/loveapp/agents/advice.py:129-145、272-301；src/loveapp/application/memory.py:494-632；src/loveapp/core/config.py:72-73。

### 2.3 LangGraph、LangChain 与普通 Python 的职责

[已实现] LangGraph 只负责三张有界状态图的节点编排与条件边，不负责持久化、检索算法或业务判断。仓库中只有三个 StateGraph：ConversationAgent、AdviceAgent、DatePlanningAgent。证据：src/loveapp/agents/conversation.py:6、108-132；src/loveapp/agents/advice.py:4、97-127；src/loveapp/agents/date_planner.py:6、83-94。

[已实现] 技术选型事实可以确认：仓库没有 langchain 依赖或 import，因此不能在面试中称为“基于 LangChain Chain/Retriever/Memory”。准确说法是“LangGraph 编排 + 自定义 ports/adapters + OpenAI SDK/Qdrant client”。证据：pyproject.toml:11-25；全仓库 langchain 搜索无结果。

[已实现] 普通 Python/Pydantic 承担了主要可解释业务逻辑：Router 打分和 merge guard、Memory Gate/修复/迁移、RAG soft rerank、ScenarioPolicy、安全硬约束、日期 slots 合并、POI 排序和行程增量编辑。证据：src/loveapp/application/routing.py；src/loveapp/application/memory_repair.py；src/loveapp/adapters/knowledge/scoring.py；src/loveapp/application/scenario_policy.py；src/loveapp/agents/date_planner.py。

[已实现] 当前没有 LangGraph checkpointer。图内 TypedDict State 仅存在于单次 ainvoke；跨轮状态由 SQLiteMemoryStore 和 SQLiteDatePlanningTaskStore 显式保存。证据：全仓库无 checkpointer/MemorySaver/SqliteSaver；src/loveapp/adapters/date_tasks.py:42-139。

### 2.4 三张 StateGraph 事实表

| Workflow | State | Nodes | Conditional Edge | 持久状态 |
|---|---|---|---|---|
| [已实现] 顶层会话图 | ConversationState：request、recent_messages、route、date_task_state、advice_turn、date_plan、memory_task、trace 等 | load_history、route、high_risk_response、relationship_advice、date_planning、casual_chat | route 依据 risk/task 分四支 | messages、relationship context、date task 由 SQLite 显式保存 |
| [已实现] 关系建议图 | AdviceState：request、context、scenario、safety、documents、history、policy、response、memory_task 等 | classify、assess_safety、record_normal/high、load_context、resolve_policy、retrieve、compose、enforce_policy、compose_safety、save_response | assess_safety 分 normal/high | 消息与记忆侧路写 SQLite |
| [已实现] 日期规划图 | DatePlanningState：request、existing_plan、mutation、context、POIs、weather、response、trace | load_memory、load_weather、search_places、build_plan | 无，线性图 | 顶层 ConversationAgent 将 DatePlanningTaskState 写 SQLite |

证据：src/loveapp/agents/conversation.py:37-48、108-132；src/loveapp/agents/advice.py:31-46、97-127；src/loveapp/agents/date_planner.py:26-42、83-94。

### 2.5 当前运行配置与外部依赖

以下值来自本次通过 Settings 解析的有效配置；API Key 未输出。

| 组件 | 当前有效配置 | 代码证据 |
|---|---|---|
| [已实现] 最终回答模型 | DeepSeek OpenAI-compatible，deepseek-v4-pro；120s、2 retries、4096 tokens | src/loveapp/core/config.py:20-26；src/loveapp/bootstrap.py:241-257 |
| [已实现] Router 模型 | auto，继承 deepseek-v4-pro；thinking disabled；20s、0 retries、2048 tokens | src/loveapp/core/config.py:28-36；src/loveapp/bootstrap.py:315-336 |
| [已实现] Memory Flash | deepseek-v4-flash；thinking disabled；30s、0 retries、1536 tokens | src/loveapp/core/config.py:53-60；src/loveapp/bootstrap.py:282-294 |
| [已实现] Memory Strong | deepseek-v4-pro；thinking enabled；60s、1 retry、4096 tokens | src/loveapp/core/config.py:61-66；src/loveapp/bootstrap.py:295-311 |
| [已实现] Embedding | AI-ModelScope/bge-small-zh-v1.5，CPU，batch 16 | src/loveapp/core/config.py:44-49 |
| [已实现] 向量库 | Qdrant，http://localhost:6333，collection love_knowledge，min score 0.45 | src/loveapp/core/config.py:38-42 |
| [已实现] 业务持久化 | SQLite .data/loveapp.db | src/loveapp/core/config.py:51-52 |
| [已实现] 地图 | Amap；20s、每页 25、最小请求间隔 0.6s、2 retries | src/loveapp/core/config.py:75-81 |
| [部分实现] 天气 | provider 当前为 disabled；Demo 和 Amap 代码均存在 | src/loveapp/core/config.py:82-83；src/loveapp/adapters/weather.py:11-121 |

[已实现] .env 由 SettingsConfigDict 加载，所有变量使用 LOVEAPP_ 前缀，API Key 类型为 SecretStr；.env 被 .gitignore 排除。证据：src/loveapp/core/config.py:9-14、22、76；.gitignore:9-10。

## 3. Router

### 3.1 标签体系

| 层级 | 当前标签 |
|---|---|
| [已实现] TaskType | general_chat、relationship_advice、date_planning |
| [已实现] AdviceScenario | pursuit、conflict、chat_analysis、relationship_maintenance、boundary、breakup |
| [已实现] AdviceGoal | initiate、understand、progress、repair、communicate、set_boundary、end_relationship |
| [已实现] RiskLevel 类型 | normal、sensitive、high |
| [部分实现] SafetyPolicy 实际输出 | normal 或 high；当前确定性扫描不会产生 sensitive |
| [已实现] DateRequestMode | none、evaluate、category_recommendation、place_search、itinerary、modify |
| [已实现] DateTaskIntent | none、new_request、supplement、continue、switch、cancel |
| [已实现] DatePlanMutation | none、add、replace、remove、reorder、update_constraint、replan |

证据：src/loveapp/domain/enums.py:4-115；src/loveapp/safety/policy.py:60-70。

### 3.2 规则、LLM 与 merge guard 如何协同

~~~mermaid
flowchart LR
    Q[latest_query + recent_messages + active_task + date_task_state]
      --> N[Unicode NFKC + lowercase + whitespace normalization]
    N --> SAFE[Deterministic SafetyPolicy]
    SAFE -->|high| OUT[Return rule result immediately]
    SAFE --> RULE[Task / Goal / Scenario weighted rules<br/>Date mode + slots]
    RULE --> FAST{Fast path?}
    FAST -->|yes| RESULT[RouteResult]
    FAST -->|no| LLM[RouteCorrector JSON]
    LLM --> VALID[Pydantic + evidence validation]
    VALID --> GUARD[Python merge + task override guard]
    GUARD --> RESULT
~~~

[已实现] Python 规则先生成完整 RouteResult，包含 rule_task_type、task/scenario/goal scores、date slots、missing fields 和证据。证据：src/loveapp/application/routing.py:187-401；src/loveapp/domain/routing.py:76-107。

[已实现] Router LLM 收到最近 6 条消息、active task、date task state 和规则结果，只输出 RouteCorrection JSON；证据必须逐字来自当前/历史消息。结构失败最多执行一次应用层修正重试。证据：src/loveapp/adapters/routing/openai_compatible.py:32-77、83-139。

[已实现] LLM 不是最终裁判。merge_route_correction 保护高置信度一级任务，避免把明确关系建议降级为 general_chat，也禁止 evaluate/category_recommendation 直接启动约会收集流程。证据：src/loveapp/application/routing.py:136-178、442-560。

[已实现] 高风险在调用 Router LLM 前直接返回，因此 LLM 不能把 high 降低为 normal。证据：src/loveapp/application/routing.py:41-57。

### 3.3 Fast Path 与 LLM 调用条件

[已实现] 确定性 Fast Path 包括：

- high risk；
- 精确寒暄、感谢、告别；
- 已有 date active task 时的结构化 slot-only 补充；
- 规则置信度和分差足够、且不依赖上下文的明确请求；
- 明确约会执行请求达到 task confidence 0.82，且规则强度至少 5 或已有具体 slots。

证据：src/loveapp/application/routing.py:51-58、76-134、1185-1208、1453-1475。

[已实现] LLM 校正主要处理：

- 低于 confidence threshold 0.72 或 top margin 低于 0.16 的任务，且有历史或明显建议请求；
- 可恢复 date task 中不是纯 slot 的短回答；
- 弱约会候选，需要区分评价与执行；
- 上下文省略；
- 跨业务复合请求；
- 关系建议中真正含糊或有明确先后关系的多场景问题。

证据：src/loveapp/application/routing.py:76-134、767-827；src/loveapp/core/config.py:35-36。

[已实现] 多 Scenario/Goal 本身不会自动触发 LLM；标签主要作为建议策略和 RAG 软加权信号。证据：src/loveapp/application/routing.py:128-134、821-827；src/loveapp/agents/advice.py:225-245。

### 3.4 当前评测

[已实现] 本次重新执行 evals/routing/cases_v2.jsonl：13 个多轮会话、36 turns、24 个带上下文 turns；turn/conversation/task/primary scenario/context route accuracy 均为 1.0；Goal micro precision 0.95、recall 1.0、F1 0.9744；RecordingRouteCorrector 调用 4 次，调用率 0.1111；never violations 和 required misses 都为 0。证据：src/loveapp/evaluation/routing.py:42-222；evals/routing/cases_v2.jsonl；本次审计执行结果。

[已实现] cases_v3.jsonl 本次为 3 个多轮会话、6 turns，全部通过且没有 corrector 调用；Goal F1 为 0.8889。报告中的 secondary_scenario_recall=0 和 high_risk_recall=0 是因为该小集合没有对应正样例，不能解释为模型召回失败或成功。证据：evals/routing/cases_v3.jsonl；src/loveapp/evaluation/routing.py:191-220。

[部分实现] 该 evaluator 使用确定性 RecordingRouteCorrector，不调用真实 DeepSeek，因此适合验证“何时调用 LLM”和“merge 后是否正确”，不代表真实 Router 模型准确率、网络延迟或费用。证据：src/loveapp/evaluation/routing.py:14-39、70-100；evals/routing/README.md。

### 3.5 已知问题与整改事实

[已实现] 历史保存报告显示旧版 Router 曾过度调用 LLM。routing_v2_pre_change.json 与 routing_v2_post_change.json 记录了同一旧版 12 会话/33-turn 集的前后对比；当前 cases_v2 已扩为 13/36，不能把旧报告直接称为当前结果。证据：evals/baselines/routing_v2_pre_change.json；evals/baselines/routing_v2_post_change.json。

[部分实现] Router 仍包含较多中文规则与正则。这带来可解释 Fast Path，也意味着新表达、否定范围和上下文话语行为仍需要扩充评测；LLM 只缓解规则盲区，不消除它。证据：src/loveapp/application/routing.py:699-724、972-1689。

[已发现缺陷] 当前全量 pytest 失败不在 Router，而在 Memory lifecycle；Router 的当前离线多轮集全部通过。审计中应避免把“全量测试不绿”误写成“路由回归”。

## 4. RAG

### 4.1 知识格式与切块

[已实现] KnowledgeDocument 的统一模型包含 id、title、scenario、relationship_stages、goals、tags、question、query_variants、answer、context、原则/动作/示例话术、risk、source_type、source_ref 与 version。证据：src/loveapp/domain/knowledge.py:12-50。

[已实现] Markdown 不按固定 token/字符大小切块，而是一个二级标题 ## 问答块对应一个 KnowledgeDocument；同时支持 JSON 与 JSONL。证据：src/loveapp/adapters/knowledge/markdown.py:13-73；src/loveapp/adapters/knowledge/loader.py:15-33。

[已实现] 正式 Markdown 当前有 50 个问答块，ID 为 formal_v1_001 到 formal_v1_050；内置 Seed 为 6 条。CLI ingest 会先统一模型、按 ID/规范化问题去重，再将 56 个逻辑文档写入 Qdrant，前提是不存在重合问题。证据：src/loveapp/adapters/knowledge/markdown.py:101-115；src/loveapp/bootstrap.py:235-238；src/loveapp/cli.py:165-190。

[部分实现] 正式 Markdown 的 source_type 被 parser 固定为 synthetic_draft；当前没有仓库证据证明 50 条文档已完成人工审核。证据：src/loveapp/adapters/knowledge/markdown.py:112-115。

[已发现缺陷] 配置的 knowledge/ 目录当前只有 README.md 和 example.json.example，按 loader 规则没有可加载正式文档。Qdrant 模式依赖显式运行 knowledge ingest，不会在应用启动时自动入库。证据：src/loveapp/adapters/knowledge/loader.py:36-54；src/loveapp/bootstrap.py:89-100；knowledge/。

### 4.2 Embedding 与向量库

[已实现] 文档与查询使用同一个 SentenceTransformerEmbeddingProvider；查询额外添加中文检索 prefix，向量归一化后使用 cosine。证据：src/loveapp/adapters/embeddings/local.py:7-22、61-70、96-104；src/loveapp/adapters/knowledge/qdrant.py:43-51。

[已实现] 本地 Qdrant 配置文件显示 collection 向量维度为 512、距离为 Cosine。证据：.data/qdrant/collections/love_knowledge/config.json。

[无法从仓库确认] 审计时 http://localhost:6333 请求超时，Docker Desktop Linux daemon 不存在，因此无法确认实时 collection point count、当前 payload 是否真的是最新 56 条、或当前搜索结果。磁盘目录存在不能替代在线 count。

### 4.3 检索流程

~~~mermaid
flowchart LR
    Q[当前 user query] --> P[中文 query prefix]
    P --> EMB[Local BGE embedding]
    EMB --> V[Qdrant cosine search<br/>min score 0.45]
    V --> C[默认 max requested limit,15 candidates]
    C --> L[Lexical title/question/variant/tag boost]
    L --> M[Scenario/Goal/Stage soft boost]
    M --> TOP[ScenarioPolicy final limit: 5]
    TOP --> PROMPT[Answer prompt + source metadata]
~~~

[已实现] AdviceAgent 用当前 request.query 作为检索 Query；近期消息不会拼进 RAG query，只进入 Router/最终回答上下文。证据：src/loveapp/agents/advice.py:225-245；src/loveapp/adapters/embeddings/local.py:67-70。

[已实现] 默认不是 metadata hard filter。AdviceAgent 构造 scenario_weights/goal/stage preferences，但 KnowledgeFilters.hard 默认为 false；Qdrant 先取 max(limit, 15) 候选，再 soft_rerank。证据：src/loveapp/domain/knowledge.py:53-60；src/loveapp/adapters/knowledge/qdrant.py:88-133、147-176。

[已实现] lexical boost 上限为 title/question 0.12、query variant 0.06、tags 0.07；metadata boost 支持主次 Scenario、Goal 和 RelationshipStage。证据：src/loveapp/adapters/knowledge/scoring.py:29-86。

[已实现] ScenarioPolicyRegistry 的最终文档总量默认是 5，并按主次场景分配 retrieval quota。证据：src/loveapp/application/scenario_policy.py:18-71；src/loveapp/domain/policy.py:29-63。

[部分实现] 当前“rerank”是向量分加字符/bigram lexical 与 metadata boost，不是 BM25、多路召回、cross-encoder 或 LLM reranker；仓库没有这些实现。证据：src/loveapp/adapters/knowledge/scoring.py:7-86。

### 4.4 评测与缺陷

[已实现] evals/rag/cases_v1.jsonl 有 12 条样例，evaluator 计算 Recall@3、Recall@5、MRR 和 mean latency。证据：src/loveapp/evaluation/baseline.py:211-264。

[部分实现] 2026-07-18T05:59:57Z 保存的 post_change_full.json 报告在 12 条小集合上记录 Recall@3=1.0、Recall@5=1.0、MRR=1.0、mean latency=1120.902ms；第一条冷启动耗时 13069.488ms。它是历史快照，不是本次在线结果，也不应推广为真实用户召回质量。证据：evals/baselines/post_change_full.json。

[已发现缺陷] 本次无法重跑真实 RAG eval，因为 Qdrant/Docker 不可用。当前面试表述应是“仓库有历史 baseline 和完整 evaluator，但本次审计没有在线复验”，不能说“当前 Recall@5 已达到 100%”。

[已发现缺陷] 当前知识以合成问答为主、RAG 集只有 12 条，且检索 Query 不含对话消歧信息。后续应优先增加人工审核、难负例、上下文 Query rewrite 与独立 reranker 评测；这些是改进方向，不是已实现能力。

## 5. Memory

### 5.1 完整链路

~~~mermaid
flowchart TD
    MSG[用户消息先写入 messages] --> G[Memory Gate]
    G -->|skip| RUN0[extraction_run = skipped]
    G -->|durable| PRE[加载历史、活动记忆、RelationshipPlan]
    PRE --> BR[确定性高价值 bridge<br/>当前主要是表白接受/成功]
    PRE --> FLASH[Flash JSON extraction]
    FLASH --> JSON[去代码围栏/提取 JSON/去尾逗号/补默认字段/枚举归一化]
    JSON --> CLAIM[逐 claim Pydantic + 原文证据 + 原子性校验]
    CLAIM -->|部分有效| KEEP[保留有效 claim，单独丢弃无效 claim]
    CLAIM -->|普通格式错误| DROP[本轮模型 claim 丢弃，不升级]
    CLAIM -->|重要语义不确定/冲突/覆盖缺口| UP[Strong upgrade gate]
    UP --> STRONG[Strong model]
    KEEP --> CONF[按 perspective/status 置信度过滤]
    STRONG --> CONF
    BR --> CONF
    CONF --> NORM[normalize + service atomize + dedupe key]
    NORM --> LIFE[状态迁移 + RelationshipPlan 匹配]
    LIFE --> TX[SQLite transaction<br/>memory_items + plans + extraction_runs]
    TX --> NEXT[下一轮 context assembly]
    NEXT --> EXP[过期/legacy reconcile/终止计划过滤]
    EXP --> EVID[关系证据标准化与投影]
    EVID --> QUOTA[attention pin + role quota + query relevance<br/>max 20]
    QUOTA --> PROMPT[Advice/Date Planning 共用 RelationshipContext]
~~~

主要代码路径：

- Memory Gate：src/loveapp/application/memory_gate.py:10-71
- 编排服务：src/loveapp/application/memory.py:136-492
- Flash/Strong：src/loveapp/adapters/memory/openai_compatible.py:196-400
- 本地修复：src/loveapp/application/memory_repair.py:74-213
- 升级判定：src/loveapp/application/memory_upgrade.py:66-193
- 生命周期：src/loveapp/domain/memory_lifecycle.py:174-192、347-440
- 上下文：src/loveapp/domain/memory_context.py:25-105、108-217
- 计划：src/loveapp/domain/relationship_plan.py:14-205
- SQLite：src/loveapp/adapters/memory/sqlite.py:190-331、1406-1548

### 5.2 Gate

[已实现] Gate 在模型调用前跳过寒暄、假设、Agent 操作、通用知识问题和纯咨询，识别明确记住、偏好、时间互动、关系事件、计划、关系状态、主观判断与建议结果。证据：src/loveapp/application/memory_gate.py:10-71、100-275。

[部分实现] Gate 不是语义模型，主体仍是正则。它加入了通用“上次/之前/结束后/回来后”等回顾事件语义，以及一个读取历史和现有记忆的 contextual bridge，但 bridge 当前只保守处理表白接受/成功。证据：src/loveapp/application/memory_gate.py:37-54；src/loveapp/application/relationship_events.py:1-7、65-148；src/loveapp/domain/relationship_plan.py:259-267。

[已发现缺陷] 对任意上一轮追问的短回答、复杂指代和隐含事实，Gate 仍可能漏召回。代码注释明确把 deterministic bridge 定义为 narrow acceptance statement，而不是通用 discourse resolver。证据：src/loveapp/application/relationship_events.py:71-82。

### 5.3 Flash、修复与 Strong 升级

[已实现] Flash 使用 response_format=json_object、低温度、独立 timeout/token/retry/thinking 配置；Prompt 只带最近 6 条对话和最多 20 条已选择活动记忆。证据：src/loveapp/adapters/memory/openai_compatible.py:79-133、403-434。

[已实现] 本地修复顺序包含：去 BOM/代码围栏、提取平衡 JSON 对象、去 trailing comma、补安全容器默认值、枚举别名与语义字段归一化。普通 JSON 语法错误不会自动升级 Strong。证据：src/loveapp/application/memory_repair.py:74-135；src/loveapp/adapters/memory/openai_compatible.py:232-264；src/loveapp/application/memory_upgrade.py:78-91。

[已实现] 当前是逐 claim 校验。一个 Flash 返回中只要至少一条 claim 有效，就保留有效项并记录 invalid_claim_count/reasons；只有原输出有 claims 但全部无效时，才将整次模型输出判为失败。证据：src/loveapp/application/memory_repair.py:136-213。

[部分实现] 非原子 claim 的本地修复只会缩窄证据到模型已声明的主命题，不会凭空补出第二个 predicate；遗漏的第二事实只能由另一条 claim 或后续模型恢复。证据：src/loveapp/application/memory_repair.py:288-363。

[已实现] Strong 只在高价值语义不确定、复杂指代/时间、与旧记忆冲突、重要 claim 覆盖缺口或重要空/低置信抽取时考虑；importance 阈值当前为 4。Strong 失败会回退可用 Flash 结果，Strong 合法空结果也不会抹掉非空 Flash。证据：src/loveapp/application/memory_upgrade.py:23-193；src/loveapp/adapters/memory/openai_compatible.py:269-354。

### 5.4 数据结构与语义边界

| 类型/字段 | 真实含义 | 生命周期 |
|---|---|---|
| [已实现] stable_fact | 相对稳定的用户/对方/关系事实 | 默认长期；点时间事实会在上下文角色上作为 recent event |
| [已实现] preference | 饮食、活动、消费等偏好/限制 | 可确认、去重、被纠正 |
| [已实现] interaction_event | 一次有边界、已发生的互动 | 保留历史；不自动代表当前状态 |
| [已实现] interaction_pattern | 重复行为或时间区间趋势 | 作为 pattern，允许新状态替代 |
| [已实现] advice_outcome | 建议实施后的结果 | 作为近期重要事件 |
| [已实现] planned_event | 有未来锚点的关系活动 | 同步为 RelationshipPlan；完成/取消/过期后不进入 active plan |
| [已实现] action_intent | 尚未形成明确计划的行动意图 | 默认 14 天 TTL；关联计划结束后关闭 |
| [已实现] relationship_state | 熟悉度、接触机会、可联系性、冲突、互惠、对方感情状态等当前投影 | 按维度 TTL，同维度新值 supersede 旧值 |
| [已实现] perspective | user_reported、user_belief、model_inferred | 影响置信度阈值，避免把用户猜测写成客观事实 |
| [已实现] evidence_spans | 必须逐字来自用户原文的证据 | 校验失败的 claim 单独丢弃 |

证据：src/loveapp/domain/memory.py:15-74、287-407；src/loveapp/domain/memory_dimensions.py:28-120；src/loveapp/application/memory_repair.py:231-285。

[已实现] 每条 claim 包含 claim_id、kind、subject、predicate、object、中文 summary、evidence_spans、time_kind、occurred/period/expires 时间、valence、relationship_impact、importance、perspective、confidence、payload 和 supersedes_id。证据：src/loveapp/domain/memory.py:325-388。

[已实现] valence/relationship_impact 是属性，不是把记忆简单分成“积极表”和“消极表”；event/pattern/state 才决定更新语义。证据：src/loveapp/domain/memory.py:48-60、287-309。

### 5.5 去重、状态迁移和计划生命周期

~~~mermaid
stateDiagram-v2
    [*] --> Proposed: model extraction
    Proposed --> Confirmed: user confirm / deterministic preference
    Proposed --> Rejected: user reject
    Confirmed --> Rejected: user reject
    Proposed --> Superseded: correction / same-state replacement / semantic dedupe
    Confirmed --> Superseded: correction / same-state replacement
    Proposed --> Expired: expires_at reached
    Confirmed --> Expired: expires_at reached
    Rejected --> [*]
    Superseded --> [*]
    Expired --> [*]
~~~

~~~mermaid
stateDiagram-v2
    [*] --> Proposed
    Proposed --> Confirmed
    Proposed --> Completed
    Proposed --> Cancelled
    Proposed --> Expired
    Confirmed --> Completed
    Confirmed --> Cancelled
    Confirmed --> Expired
    Completed --> [*]
    Cancelled --> [*]
    Expired --> [*]
~~~

[已实现] Memory 状态为 proposed/confirmed/rejected/expired/superseded；SQLite 使用 user_id + relationship_id + dedupe_key 的 partial unique index 限制 active 重复。证据：src/loveapp/domain/memory.py:69-74；src/loveapp/adapters/memory/sqlite.py:1445-1482。

[已实现] 跨 predicate 迁移不是散落 if/else，而是 PredicateFamily + StateTransitionRule。目前注册了 contact unavailable/restored、repair started、relationship repaired、confession intent/started 和消费观冲突等概念。证据：src/loveapp/domain/memory_lifecycle.py:34-58、61-203、347-402。

[部分实现] 未注册 predicate 仍无法自动进入跨 predicate 迁移；系统会保留它，但不一定能关闭语义等价的旧状态。证据：src/loveapp/domain/memory_lifecycle.py:225-241、473-490。

[已实现] RelationshipPlan 是独立实体，包含 plan_id、activity_type、participants、scheduled window、status、source memory/message 和终止时间；完成事件优先按显式 plan ID 匹配，历史数据才使用结构化相似度回退。证据：src/loveapp/domain/relationship_plan.py:28-68、128-205。

[已实现] 上次/之前/回来以后等近期已发生语义会先抑制相冲突的 active plan，再异步/后续完成生命周期迁移，避免把已发生活动继续作为“即将发生”注入回答。证据：src/loveapp/domain/relationship_plan.py:227-267；src/loveapp/application/memory.py:938-963。

### 5.6 Pending clarification 与用户画像

[部分实现] pending clarification 不是独立表。比如“不知道她是否单身”会规范化为 relationship_state：state_dimension=partner_relationship_status、state_value=unknown、attention_status=unresolved，并优先固定到 active_context；后续 single/partnered/married 会替代 unknown。证据：src/loveapp/domain/memory_lifecycle.py:282-303；src/loveapp/domain/memory_context.py:182-217；tests/test_memory_attention.py:26-55、111-170。

[部分实现] 用户画像不是独立 UserProfile 实体，而是 RelationshipContext 对记忆的投影。它能分别聚合 user_preferences 与 partner_preferences，并保存 relationship_stage、当前状态、计划、近期事件和关系证据。证据：src/loveapp/domain/advice.py:29-45；src/loveapp/domain/memory_context.py:108-179。

[已实现] 对方是否 single/partnered/married 是已注册的 partner_relationship_status 维度，TTL 为 90 天。证据：src/loveapp/domain/memory_dimensions.py:96-119。

[部分实现] “用户本人全局处于单身/恋爱/已婚”没有专用字段。当前 relationship_stage 描述用户与本 relationship_id 的关系阶段，且自动投影只会在高置信表白成功后推进到 dating；不会自动表示全局婚姻状态。证据：src/loveapp/domain/enums.py:13-21；src/loveapp/application/memory.py:787-808、1093-1098、1147-1157。

### 5.7 关系证据状态

[已实现] 系统不再直接按事件名称或数量推断“关系很好/很差”，而是先标准化为 familiarity、trust、investment、conflict、boundary 五维证据；每条信号带方向、强度、置信度、来源和时间。证据：src/loveapp/domain/relationship_evidence.py:21-92、218-288。

[已实现] 投影会进行时间衰减和同源去重；五维半衰期分别为 365、120、45、14、180 天。证据：src/loveapp/domain/relationship_evidence.py:209-215、304-338。

[已实现] “能否读懂对方内心”与“是否有足够证据进行一次低压力推进”被拆开：ScenarioPolicy 仍禁止读心，而 supports_low_pressure_progression 根据熟悉/信任/投入与边界证据判断。证据：src/loveapp/domain/relationship_evidence.py:95-125；src/loveapp/application/scenario_policy.py:78-104、126-154。

### 5.8 上下文装配

[已实现] active memory 先按状态与 expires_at 过滤、按语义 key 去重，再按 current state、preference、action intent、planned event、stable profile、pattern、recent event 配额选择，总上限默认 20。证据：src/loveapp/domain/memory_context.py:25-105；src/loveapp/core/config.py:70。

[已实现] unresolved、约束、current state、active plan 和高重要性事实会进入 attention pin，不只依赖 Query lexical relevance。证据：src/loveapp/domain/memory_context.py:82-105、182-254。

[已实现] 关系咨询和约会规划读取同一个 MemoryService/RelationshipContext；约会规划再将记忆中的偏好与禁忌转成 POI 排序和排除条件。证据：src/loveapp/agents/advice.py:147-163；src/loveapp/agents/date_planner.py:96-126、1911-1964。

### 5.9 指定六类历史问题的当前状态

#### a. 推理模型造成抽取延迟

[已发现缺陷] SQLite 历史 extraction_runs 中，tier=flash 的 62 次 completed 尝试平均 5424.608ms，范围 1120.889-114036.922ms；tier=strong 的 9 次 completed 平均 66473.008ms，范围 11180.370-163544.778ms。这些数据跨越配置历史，不是干净的当前 benchmark，但足以证明 Strong/历史推理链路曾是显著延迟源。证据：.data/loveapp.db 的 memory_extraction_runs.attempts_json。

[已实现] 当前 Flash 已改为 deepseek-v4-flash + thinking disabled，Strong 才使用 deepseek-v4-pro + thinking enabled；交互式回答与记忆侧路并行。证据：当前有效 Settings；src/loveapp/agents/advice.py:129-145、248-301。

[部分实现] 后台化降低前台等待，不降低模型调用本身的延迟。下一轮只等 2 秒，因此超慢抽取仍可能赶不上立即下一轮。证据：src/loveapp/application/memory.py:585-606；src/loveapp/core/config.py:72。

#### b. Gate 漏掉上下文事实

[部分实现] 已加入 conversation_history/existing_memories 参数、表白成功 bridge 与通用回顾事件入口。证据：src/loveapp/application/memory_gate.py:10-54。

[已发现缺陷] bridge 仍是窄实现，无法保证理解任意助手追问后的“她同意了/有过/不是”等回答；复杂指代仍依赖 Flash 能否被 Gate 放行。

#### c. Flash 输出未通过原子性校验

[已实现] stable_fact、interaction_pattern、relationship_state 会检测多个可独立更新维度；偏好数组也要求拆分。证据：src/loveapp/application/memory_repair.py:244-284；src/loveapp/domain/memory_dimensions.py。

[部分实现] 本地修复可缩窄一个主 claim，但不会创造遗漏 claim；因此“保住一条”不等于完整召回所有事实。证据：src/loveapp/application/memory_repair.py:288-363。

#### d. 全量校验导致有效 claim 一起丢弃

[已实现] 已改为 per-claim salvage：valid_claims 与 invalid_claim_reasons 分开累计。证据：src/loveapp/application/memory_repair.py:136-213；tests/test_memory_state_dimensions.py:108-157。

[已发现缺陷] 如果所有 claims 都无效，仍会得到空抽取或语义升级；这是保守设计，但会形成记忆漏召回，需要通过 evaluator 监控。

#### e. 历史事件和当前状态混在一起

[部分实现] 已通过 MemoryRole、relationship_state TTL、RelationshipPlan 与 relationship evidence projection 分开。证据：src/loveapp/domain/memory_lifecycle.py:24-31、206-261；src/loveapp/domain/relationship_plan.py；src/loveapp/domain/relationship_evidence.py。

[已发现缺陷] 当前迁移覆盖注册 predicate family；模型产生新的同义 predicate 时，历史事件与当前状态仍可能未正确关联。

#### f. 所有活动记忆都进入 Prompt

[已实现] 当前不再全量注入：存储查询先取最多 200 条供生命周期处理，实际 Prompt 只取角色配额和 query relevance 后最多 20 条；终止计划及关联 intent 会被过滤。证据：src/loveapp/application/memory.py:199-223、896-981；src/loveapp/domain/memory_context.py:25-105。

### 5.10 SQLite 当前只读快照

审计时 .data/loveapp.db：

| 数据 | 数量 |
|---|---:|
| users | 1 |
| relationships | 25 |
| conversations | 43 |
| messages | 329 |
| memory_items | 179 |
| memory_extraction_runs | 126 |
| relationship_plans | 2 |
| date_planning_tasks | 8 |

Memory 状态：proposed 158、confirmed 5、superseded 13、expired 3。  
Memory kind：stable_fact 64、interaction_pattern 31、interaction_event 27、preference 21、action_intent 15、planned_event 14、relationship_state 6、advice_outcome 1。  
Extraction run：completed 68、skipped 52、cancelled 4、failed 2。

[已实现] 这些数字来自 SQLite 当前数据，不代表线上规模或准确率。核心 schema 证据：src/loveapp/adapters/memory/sqlite.py:1406-1548。

## 6. Date Planning 与 Tool Calling

### 6.1 Slot、合并与多轮追问

[已实现] DatePlanSlots 定义 city、area、plan_mode、date/end_date、day_count/nights/target_day、start_time、budget/scope、preferences、dining/activity/meal keywords、schedule hints、replace names、exclusions、transport、notes、constraints 和 lodging notes。证据：src/loveapp/domain/routing.py:24-46。

[已实现] slot 抽取是 Python 规则加可选 LLM Router 校正；最终只合并用户原文明确提供的字段。证据：src/loveapp/application/routing.py:564-678、2149-2232；src/loveapp/adapters/routing/openai_compatible.py:196-240。

[已实现] DatePlanningTaskState 以 user_id + relationship_id + conversation_id 持久化 slots、missing/asked fields、clarification round、fallback、weather、current_plan、plan_version、locked items 和 last_mutation。证据：src/loveapp/domain/date_task.py:18-75；src/loveapp/adapters/date_tasks.py:60-116。

[已实现] 缺 city/date_time/budget 时只追问一轮；city 阻塞真实地图搜索，因此优先问一次。用户仍不提供时不循环追问，而用默认 500 元和通用结构生成草案。证据：src/loveapp/agents/conversation.py:781-804、845-880；src/loveapp/agents/conversation.py:367-369。

[已实现] 新增/替换/删除/重排/约束更新/重规划是独立 mutation；不明确要求 replan 时保留 current_plan。证据：src/loveapp/domain/enums.py:75-82；src/loveapp/agents/date_planner.py:322-375、974-1356。

### 6.2 单日与多日

[已实现] day_count > 1 自动切换 multi_day，日期区间最长 5 天；支持 total/per_day 预算、target_day 局部修改和每天独立天气/路线。证据：src/loveapp/domain/date_plan.py:15-89；src/loveapp/agents/date_planner.py:590-884、1358-1443。

[部分实现] 多日当前是单城市约会旅行：每天主要活动+用餐，跨夜不连接路线。lodging_notes 只进入日结构；没有酒店 POI、跨城交通或预订。证据：src/loveapp/agents/date_planner.py:835-865。

### 6.3 地图和地点工具

[已实现] AmapMapProvider 通过 v5 place text API 搜索 POI，按行政区、类别、显式 required keyword、排除项、预算、评分和偏好排序；每个显式餐饮/活动关键词独立搜索，避免要求一个 POI 同时满足“电影院+博物馆+火锅”。证据：src/loveapp/adapters/maps/amap.py:46-83；src/loveapp/agents/date_planner.py:169-316。

[已实现] 显式“西餐、日料、火锅、博物馆、景点、电影院”等会进入精确搜索 provenance，并在最终选点时校验 POI identity。证据：src/loveapp/domain/date_plan.py:92-129；src/loveapp/agents/date_planner.py:1445-1548。

[已实现] Amap 有请求节流、缓存和特定 infocode 重试；路线支持步行、公交、驾车、骑行。证据：src/loveapp/adapters/maps/amap.py:18-44、85-153。

[部分实现] 这不是 LLM function calling。仓库没有 tool schema、ToolNode、bind_tools 或 tool_choice；LangGraph 的 Python node 直接调用 typed MapProvider/WeatherProvider。面试中应称为“确定性工具适配器调用”，不要称为“模型自主 Tool Calling”。

### 6.4 工具失败

[已实现] 单日普通计划的路线调用失败时会保留已找到的地点，将异常写进 plan note，不再因为“高德没有返回路线”丢弃整轮。证据：src/loveapp/agents/date_planner.py:503-518。

[已实现] 多节点路线重建也逐段捕获异常并保留其余 itinerary。证据：src/loveapp/agents/date_planner.py:1324-1356。

[已发现缺陷] _search_places 内多个 search_places 使用 asyncio.gather 且没有 return_exceptions/局部降级；任一 Amap POI 请求异常仍可能让整个日期分支失败。证据：src/loveapp/agents/date_planner.py:211-301。

[部分实现] Weather 按多日并发查询并逐日捕获异常，但当前有效 provider=disabled，所以真实天气自适应代码没有在本次运行配置中启用。证据：src/loveapp/agents/date_planner.py:128-167；src/loveapp/adapters/weather.py:11-19。

### 6.5 LangGraph 如何保存任务状态

[已实现] LangGraph 自己不保存跨轮 State。ConversationAgent 在每轮开始从 DatePlanningTaskStore 读取，在澄清、暂停或规划后显式 save；SQLite 表保存完整 state_json。证据：src/loveapp/agents/conversation.py:152-156、261-269、324-337、416-445、489-495；src/loveapp/adapters/date_tasks.py:73-139。

## 7. Safety

[已实现] Risk 与业务 Task 独立。SafetyPolicy 在 HybridRouter 中先执行，AdviceAgent 内又执行一次防御性扫描；high risk 进入确定性 safety response，不执行 RAG 或普通最终回答模型。证据：src/loveapp/application/routing.py:41-57；src/loveapp/agents/advice.py:111-126、205-211、303-323。

[已实现] 确定性规则覆盖人身暴力、跟踪/限制自由/强迫、自伤、未经同意的亲密行为和报复，并有否定窗口降低“我不会跟踪她”之类误报。证据：src/loveapp/safety/policy.py:15-80。

[已实现] LLM 不允许降低 high-risk，因为 high 在 LLM corrector 之前返回。证据：src/loveapp/application/routing.py:51-57。

[已实现] normal 建议还会执行 ScenarioPolicy 硬约束：禁止操控/读心、尊重明确拒绝和关系边界、要求互惠、冲突先降温、区分事实与推断、禁止强迫复合。证据：src/loveapp/domain/policy.py:18-26；src/loveapp/application/scenario_policy.py:107-207。

[部分实现] high-risk 消息仍会写 messages，并启动 Memory Gate 侧路；代码没有“高风险文本禁止持久化”的专门策略。是否应保留属于产品隐私策略，仓库没有明确决定。证据：src/loveapp/agents/advice.py:113-120、129-145。

[已实现] 本次 safety v1 有 14 条固定样例，9 positive/5 negative；recall、precision、specificity、F1 均为 1.0。小样例结果不能解释为生产安全能力。证据：evals/safety/cases_v1.jsonl；src/loveapp/evaluation/baseline.py:156-208；本次审计执行结果。

## 8. Observability 与真实问题复盘

### 8.1 Trace、日志和调试命令

[已实现] ExecutionTrace 记录 step 名、开始偏移、耗时、running/completed/failed、error 和 details；支持后台任务和定位非 total 的实际 failed_step。证据：src/loveapp/core/timing.py:12-137；src/loveapp/domain/observability.py:6-25。

[已实现] RAG Trace 可见 embedding warmup、query embedding、vector search candidate count、soft rerank returned count；Memory Trace/SQLite runs 可见 Gate、tier、token、repair、invalid claim、upgrade/discard reason。证据：src/loveapp/adapters/knowledge/qdrant.py:95-133；src/loveapp/domain/memory.py:127-166。

[部分实现] ExecutionTrace 主要是单进程内存对象和 CLI 展示，不会统一写入日志/Tracing backend；只有 memory_extraction_runs 持久化。项目中没有 .log 文件，也没有 OpenTelemetry、LangSmith 或 metrics exporter。

常用命令：

~~~powershell
uv run loveapp chat --user-id local-user --relationship-id partner-x --debug-memory --debug-route --stream --timings
uv run loveapp memory watch --user-id local-user --relationship-id partner-x --include-inactive
uv run loveapp memory context --user-id local-user --relationship-id partner-x
uv run loveapp memory runs --user-id local-user --relationship-id partner-x --conversation-id <id> --json
uv run loveapp memory plans --user-id local-user --relationship-id partner-x --json
uv run loveapp knowledge search "和对象吵架后怎么沟通" --limit 5
uv run loveapp eval routing --dataset evals/routing/cases_v2.jsonl --output <report.json>
~~~

证据：src/loveapp/cli.py:193-224、256-470、505-550、875-1075；scripts/start_loveapp_debug.ps1:1-82；scripts/memory_debug.ps1:1-111。

### 8.2 典型问题、根因与整改

| 真实问题 | 证据支持的根因 | 当前整改 | 状态 |
|---|---|---|---|
| 普通寒暄也调用 Router LLM，延迟很高 | 旧触发策略把多标签/上下文过度等同于语义校正 | 精确 casual fast path、阈值/分差、date mode、compound/context 条件、task guard；多轮 evaluator 统计调用策略 | [已实现] 当前离线集通过；真实 LLM 延迟仍需线上数据 |
| 明确关系建议被 LLM 降为 general_chat | 旧 merge 把 LLM 当无约束最终裁判 | 同时保留 rule_task/llm_task，_allow_task_override 保护明确任务 | [已实现] |
| “约她吃饭你看怎么样”误入约会收集 | “约会/吃饭/电影”关键词没有区分评价与执行 | DateRequestMode 把 evaluate/category recommendation 留在 relationship_advice，只有 search/itinerary/modify 执行日期任务 | [已实现]，仍依赖规则+LLM覆盖 |
| RAG 首次查询约 13 秒 | 本地 embedding 冷加载 | 共享 warmup task，首个请求等待同一任务；历史 post_change 第一条 13069.488ms | [部分实现] 冷启动仍存在，当前 Qdrant 未在线复验 |
| 记忆抽取 20-60 秒甚至更久 | 强模型/推理、网络和结构重试；历史 DB strong mean 66473.008ms | Flash non-thinking、选择性 Strong、后台侧路、独立 timeout/token | [部分实现] 模型本身仍慢 |
| Flash 一条坏 claim 使全部结果失败 | 旧式整体校验 | 当前 per-claim salvage、atomic evidence narrowing、attempt telemetry | [已实现] |
| 已发生活动仍作为未来计划注入 | 计划只是普通句子，缺少独立生命周期与事件关联 | RelationshipPlan、状态机、显式 ID/结构匹配、近期事实冲突抑制 | [已实现]，未知 activity/predicate 仍有匹配风险 |
| 路线失败导致整轮约会计划失败 | 把 route 当作构建计划的硬前提 | route failure 降级为 note 并保留 POI | [已实现]；POI search 异常仍可能整轮失败 |
| 道歉后旧 contact outage 测试变 expired 而不是 superseded | MemoryService 注入 clock 与 InMemory store 直接 utc_now 不一致 | 本次尚未整改 | [已发现缺陷] 294/295 tests |
| 502/空响应难定位 | 同一错误文本可能来自 Qdrant、模型网关或 JSON 解析 | Trace.failed_step、分阶段 timing、memory attempts | [部分实现] 没有集中日志和 request correlation backend |

历史问题记录辅助证据：INTERVIEW_PROJECT_NOTES.md:61-272。该文件中的“102 passed”、旧 13-case Gate 和 12/33 Router 数字已过时；当前数字以本文 0.1、3.4 和 9 节为准。

## 9. 测试与评估

### 9.1 测试结构

[已实现] tests/ 有 31 个文件、244 个 test 函数定义，参数化后当前收集 295 tests。没有 unit/integration pytest markers，因此不能从 pytest 标签严格区分测试层级。

| 领域 | 主要测试 |
|---|---|
| Router | tests/test_routing.py、tests/test_routing_evaluation.py |
| Safety/Policy | tests/test_scenario_policy.py、tests/test_advice_agent.py |
| RAG/Embedding | tests/test_knowledge.py、test_markdown_knowledge.py、test_qdrant_knowledge.py、test_embedding_warmup.py |
| Memory Gate/Extractor/Repair | tests/test_memory_gate.py、test_memory_extractor.py、test_memory_upgrade.py、test_memory_state_dimensions.py |
| Memory 生命周期/存储/上下文 | tests/test_memory_lifecycle.py、test_memory_store.py、test_memory_service.py、test_memory_attention.py、test_relationship_plan_lifecycle.py、test_relationship_evidence.py |
| Date/Tool | tests/test_date_planner.py、test_date_plan_incremental.py、test_multi_day_date_planning.py、test_amap_provider.py、test_weather.py、test_date_task_store.py |
| 端到端编排 | tests/test_conversation_agent.py、test_streaming_and_timing.py |

[已实现] 测试混合使用纯函数、in-memory adapters、SQLite restart、mocked HTTP 和 ConversationAgent 集成，不会在默认测试中调用真实 DeepSeek/Amap/Qdrant。证据：tests/conftest.py；上述测试文件。

[已发现缺陷] 当前全量测试不是全绿：294 passed、1 failed。失败详情见 0.1 和 5.5。

### 9.2 当前离线指标

| 评测 | 当前结果 | 限制 |
|---|---|---|
| [已实现] routing v1 rules | 13 cases，pass rate 0.9231；task/scenario/Goal precision/recall 指标均 1.0，但组合检查仍有 1 case 未全过 | 纯规则，不含真实 LLM |
| [已实现] routing v2 | 13 conversations/36 turns；pass 1.0；Goal F1 0.9744；4 synthetic corrector calls | Corrector 是测试替身 |
| [已实现] routing v3 | 3 conversations/6 turns；pass 1.0；Goal F1 0.8889；0 calls | 数据很小，无 high-risk positive |
| [已实现] safety v1 | 14 cases；TP 9/TN 5/FP 0/FN 0；recall/precision/specificity/F1 1.0 | 规则小集合 |
| [已实现] Memory Gate v1 | 16 cases；TP 7/TN 9/FP 0/FN 0；recall/specificity 1.0 | 只评 Gate，不评模型 claim 质量 |
| [部分实现] RAG historical | 2026-07-18 报告：12 cases，Recall@3/5、MRR 1.0，mean 1120.902ms | 历史、很小、本次 Qdrant 未复验 |

证据：src/loveapp/evaluation/routing.py:42-222；src/loveapp/evaluation/baseline.py:88-208、211-264；evals/；本次审计执行结果。

### 9.3 已有 Eval 与缺口

[已实现] CLI baseline 可以组合规则 Router、Safety、真实 Qdrant RAG 和真实 Memory pollution，报告会写 JSON；可用 --no-rag/--no-live-memory 跳过外部依赖。证据：src/loveapp/cli.py:69-147；src/loveapp/evaluation/baseline.py:23-85。

[已实现] Memory Gate evaluator 已定义 pollution/store recall/gate recall/specificity、Flash direct/local repair、Strong upgrade 和 latency 指标。证据：src/loveapp/evaluation/baseline.py:267-360。

[部分实现] evals/memory/conversations_v1.jsonl、evals/memory/conversations_v2.jsonl 和 evals/memory/conversations_v3.jsonl 包含多轮原子化、时间、关系隔离和状态迁移语料，但仓库没有执行这些 corpus 的 evaluator；README 只描述目标指标。不能声称候选 precision/recall、kind accuracy、overmerge、temporal accuracy、关系串线率或上下文召回率已经测得。证据：evals/memory/README.md；对 src/、tests/、scripts/ 的搜索没有发现引用这三份语料的执行器。

[设计中] 仍缺：

- 真实 Router LLM 的标注集准确率、p95/p99、token 与费用；
- RAG 人工标注难负例、上下文 Query rewrite、metadata ablation 和 reranker 对照；
- Memory claim-level precision/recall、原子性、时间解析、污染、状态迁移和关系隔离统一 evaluator；
- Amap sandbox/live contract test、配额/限流/失败率；
- 真正端到端多轮成功率和用户满意度；
- 安全规则更大规模对抗/改写/多语言评测。

这些是缺口，不是已达到的目标。

## 10. Git、数据库与可审计性

[无法从仓库确认] 当前 main 没有 commit；无法展示 commit diff、release tag、PR 或 blame。面试中可以展示代码和 baseline，但不能宣称某功能由某个 Git commit 完成。

[已实现] SQLite 的核心表是 users、relationships、conversations、messages、memory_items、relationship_plans、memory_extraction_runs、date_planning_tasks。Memory 与关系计划有外键、状态索引和 active dedupe 约束。证据：src/loveapp/adapters/memory/sqlite.py:1406-1548；src/loveapp/adapters/date_tasks.py:60-68。

[部分实现] ExecutionTrace 不持久化，故障复盘主要依赖终端输出和 memory_extraction_runs。当前仓库没有统一日志保留策略，历史会话问题只能从 messages/memory runs 和人工复盘文档还原。

## 11. 面试中建议采用的准确表述

[已实现] 推荐一句话：

> LoveApp 是一个用 LangGraph 编排、以自定义 Python ports/adapters 实现的关系咨询与约会规划 Agent。它用确定性安全规则和混合 Router 控制调用路径，用问答块 RAG 提供领域依据，用分层模型完成原子记忆抽取，并把关系长期记忆与约会短期任务状态分别持久化到 SQLite；地图地点与路线由高德适配器确定性调用。

[已发现缺陷] 不应说：

- “项目基于 LangChain”：仓库没有 LangChain。
- “所有测试通过”：当前是 294/295。
- “当前 RAG Recall@5 100%”：只有历史 12-case 报告，本次 Qdrant 不可用。
- “Memory 已完全理解多轮指代”：只有窄 contextual bridge，Gate 仍偏规则。
- “支持自主 Tool Calling”：当前是 Python node 直接调用工具适配器。
- “支持完整旅游产品”：多日仅单城市、最多 5 天，无酒店/票务/预订。
- “有完整用户画像”：当前是关系级 Memory/Context 投影，不是独立 Profile 服务。

## 12. 项目事实清单

| 功能名称 | 当前状态 | 关键代码位置 | 有测试 | 面试重点 |
|---|---|---|---|---|
| 顶层多任务 LangGraph | [已实现] | src/loveapp/agents/conversation.py:37-132 | 是，test_conversation_agent.py | 是，讲条件路由与边界 |
| 关系建议 LangGraph | [已实现] | src/loveapp/agents/advice.py:31-127 | 是，test_advice_agent.py | 是，讲并行 context/RAG 与 policy |
| 日期规划 LangGraph | [已实现] | src/loveapp/agents/date_planner.py:26-94 | 是，test_date_planner.py | 是 |
| LangGraph checkpointer | [设计中] | 当前无实现 | 否 | 否；重点说明自定义持久化 |
| 未采用 LangChain（技术事实） | [已实现] | pyproject.toml:11-25；全仓库无 langchain import | 不适用 | 面试中明确“未使用” |
| 混合 Router | [已实现] | src/loveapp/application/routing.py:27-178 | 是 | 是，最适合讲延迟与 guard |
| LLM Router 校正器 | [已实现] | src/loveapp/adapters/routing/openai_compatible.py:10-139 | 是，使用替身/解析测试 | 是，但不要把替身指标当模型指标 |
| Task/Scenario/Goal 多标签 | [已实现] | src/loveapp/domain/enums.py:4-37 | 是 | 是 |
| 确定性 Safety | [已实现] | src/loveapp/safety/policy.py:15-80 | 是；14-case eval | 是 |
| ScenarioPolicy 四部分 | [已实现] | src/loveapp/domain/policy.py:8-63；application/scenario_policy.py:18-207 | 是 | 是 |
| Q&A Markdown chunking | [已实现] | src/loveapp/adapters/knowledge/markdown.py:27-115 | 是 | 是 |
| 本地中文 Embedding | [已实现] | src/loveapp/adapters/embeddings/local.py:7-147 | 是 | 是，讲 warmup 竞态 |
| Qdrant 检索 | [部分实现] | src/loveapp/adapters/knowledge/qdrant.py:18-200 | mocked tests；本次服务不可用 | 是，需披露环境状态 |
| Metadata soft boost | [已实现] | src/loveapp/adapters/knowledge/scoring.py:7-86 | 是 | 是 |
| BM25/cross-encoder rerank | [设计中] | 当前无实现 | 否 | 作为改进，不当作能力 |
| Memory Gate | [部分实现] | src/loveapp/application/memory_gate.py:10-275 | 是；16-case Gate eval | 是，讲污染与漏召回取舍 |
| Flash + Strong 分层抽取 | [已实现] | src/loveapp/adapters/memory/openai_compatible.py:196-400 | 是 | 是，讲成本/延迟治理 |
| JSON 本地修复 | [已实现] | src/loveapp/application/memory_repair.py:74-213 | 是 | 是 |
| Per-claim salvage | [已实现] | src/loveapp/application/memory_repair.py:136-213 | 是 | 是 |
| 原子性检测与缩窄 | [部分实现] | src/loveapp/application/memory_repair.py:231-363 | 是 | 是，说明不能补造遗漏事实 |
| Memory 去重/状态迁移 | [部分实现] | src/loveapp/domain/memory_lifecycle.py | 是；当前有 1 个时钟失败 | 是，连同缺陷讲 |
| RelationshipPlan 状态机 | [已实现] | src/loveapp/domain/relationship_plan.py | 是，test_relationship_plan_lifecycle.py | 是 |
| 关系证据五维投影 | [已实现] | src/loveapp/domain/relationship_evidence.py | 是 | 是，领域建模亮点 |
| Pending clarification | [部分实现] | src/loveapp/domain/memory_lifecycle.py:282-303；src/loveapp/domain/memory_context.py:182-217 | 是 | 是，准确说是 unknown state |
| 统一关系上下文 | [已实现] | src/loveapp/domain/advice.py:29-45；src/loveapp/domain/memory_context.py | 是 | 是 |
| 独立用户画像服务 | [设计中] | 当前无 Profile 实体 | 否 | 否，避免夸大 |
| SQLite 长期持久化 | [已实现] | src/loveapp/adapters/memory/sqlite.py | 是，含 restart tests | 是 |
| Date Task State | [已实现] | src/loveapp/domain/date_task.py；src/loveapp/adapters/date_tasks.py | 是 | 是 |
| 增量编辑行程 | [已实现] | src/loveapp/agents/date_planner.py:974-1356 | 是 | 是 |
| 单城市多日计划 | [已实现] | src/loveapp/domain/date_plan.py:15-89；src/loveapp/agents/date_planner.py:590-884 | 是 | 是，说明最多 5 天 |
| 高德 POI 与路线 | [部分实现] | src/loveapp/adapters/maps/amap.py | mocked tests | 是，说明 POI 异常缺口 |
| 天气适配 | [部分实现] | src/loveapp/adapters/weather.py；src/loveapp/agents/date_planner.py:128-167 | 是；运行时 disabled | 次要 |
| LLM 自主 Tool Calling | [设计中] | 当前无 ToolNode/tool schema | 否 | 不应宣称已实现 |
| 结构化流式预览 | [已实现] | src/loveapp/adapters/advice/openai_compatible.py:123-158；src/loveapp/cli.py:962-1045 | 是 | 是 |
| 模块级 Trace | [已实现] | src/loveapp/core/timing.py | 是 | 是 |
| 集中日志/Tracing backend | [设计中] | 当前无实现 | 否 | 作为生产化缺口 |
| 固定 Router/Safety/RAG eval | [已实现] | src/loveapp/evaluation；evals | 是 | 是 |
| 完整多轮 Memory evaluator | [设计中] | 仅有 evals/memory/conversations_v1.jsonl、v2、v3 数据 | 否 | 是，作为下一步 |
| Web/API/前端 | [设计中] | 当前无实现 | 否 | 不作为当前项目能力 |

## 13. 最终审计结论

[已实现] LoveApp 已经超出“单轮 ChatBot + 简单 RAG”范畴：它有三张 LangGraph、混合 Router、确定性 Safety、场景策略、Q&A RAG、分层原子记忆、关系计划生命周期、关系证据投影、持久化日期任务和真实地图适配器。

[部分实现] 项目的面试价值主要在工程边界与问题治理，而不是知识库规模或 UI：规则/LLM 职责划分、异步侧路、结构化校验、状态迁移、工具失败降级和固定评测集都可以用代码说明。

[已发现缺陷] 当前必须如实披露：Qdrant/Docker 未运行、全量测试有一个 Memory clock 回归、POI 搜索异常缺少局部降级、Gate 仍偏正则、完整 Memory evaluator 未落地、Trace 未集中持久化。

[无法从仓库确认] 没有 Git commit 历史，也没有生产日志、真实用户指标或当前在线 RAG 复验，所以不能声称具备生产 SLA、线上准确率或完整开发演进证据。
