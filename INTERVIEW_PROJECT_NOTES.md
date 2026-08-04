# LoveApp 项目开发复盘与面试素材

> 这份文档记录 LoveApp 开发过程中真实遇到的典型问题、定位依据、解决方案和验证结果。
> 面试时建议按“现象 -> 证据 -> 根因 -> 方案 -> 结果 -> 剩余风险”的顺序回答。
>
> 文档不包含 API Key，也不包含真实用户会话的原始标识。固定评测集较小，指标用于说明工程闭环，
> 不代表线上效果。

## 1. 项目简介

LoveApp 是一个面向单用户的恋爱沟通与约会决策 Agent。第一版以终端交互为主，核心能力包括：

- 恋爱关系咨询：追求、聊天分析、冲突修复、边界、分手和关系经营。
- 约会规划：结合城市、区域、预算、兴趣和交通偏好调用地图服务。
- RAG：把问答文档解析为独立知识单元，通过本地中文 Embedding 和 Qdrant 检索。
- 关系记忆：按关系隔离事实、偏好、互动事件、互动趋势和建议结果。
- 安全分流、结构化输出、流式预览和模块级 Trace。

### 30 秒项目介绍

我做的是一个关系咨询和约会规划 Agent。它不是每轮都直接调用大模型，而是先经过安全扫描和规则路由，明确请求走确定性路径，含糊请求才由 LLM Router 校正。关系建议会进行多场景软加权 RAG，记忆部分通过 Gate、原子化抽取和 SQLite 持久化完成。开发过程中我重点解决了路由误判和延迟、RAG 错误硬过滤、记忆污染和重复、模型结构化输出失败，以及异步任务竞态等问题，并用多轮评测集和 Trace 做回归验证。

## 2. 技术栈与分层

| 层 | 实现 |
|---|---|
| 语言 | Python 3.12 |
| 工作流编排 | LangGraph StateGraph |
| 模型 | OpenAI-compatible DeepSeek 接口，当前配置为 deepseek-v4-pro |
| RAG 向量模型 | 本地 BGE-small 中文模型，当前为 AI-ModelScope/bge-small-zh-v1.5 |
| 向量库 | Qdrant，Docker 本地运行 |
| 关系记忆 | SQLite，默认文件为 .data/loveapp.db |
| 地图服务 | 高德 Web API，另有 Demo provider 便于离线测试 |
| 数据校验 | Pydantic 结构化模型 |
| 测试与质量 | pytest、ruff、固定 JSONL 评测集 |

当前核心编排使用 LangGraph；没有把 LangChain 当作全局强耦合层。模型、知识库、记忆和地图都通过 ports/adapters 接口接入，便于替换真实实现和测试替身。

### 顶层工作流

~~~text
用户输入
  -> 历史加载 -> 文本标准化 -> 安全扫描 -> Task/Goal/Scenario 路由
  -> LangGraph 条件分支
       ├─ high_risk_response
       ├─ casual_chat
       ├─ relationship_advice
       └─ date_planning
  -> RAG、关系上下文、记忆后台任务
  -> 模型生成 -> Python 场景策略与安全硬约束
  -> 消息持久化和 Trace
~~~

关系咨询的记忆链路是：

~~~text
用户消息 -> Memory Gate -> 后台记忆模型 -> AtomicExtraction
  -> 置信度过滤 -> 原子化拆分 -> dedupe/supersession -> SQLite proposed memory
~~~

## 3. 经典问题复盘

### 3.1 LLM Router 触发过宽，普通问题也变慢

#### 现象

输入“下午好”时，结果虽然是 general_chat，但仍调用 Router LLM，单轮约 10 秒。更复杂的一轮中，路由阶段约 127 秒，最终模型生成又等待约 122 秒。

#### 已确认的证据

一次 Trace 显示总耗时约 127.91 秒，其中混合路由约 127.77 秒，而历史加载和消息保存只有几十毫秒。因此可以确认该轮主要等待发生在 Router LLM，不应归因于 SQLite。

#### 根因

旧逻辑把低置信度、多个 Goal/Scenario 或有历史上下文，过早当成 LLM 校正条件。它把“多标签”错误地等同于“需要语义判断”，并且没有确定性快速路径。

#### 解决

- 增加问候、感谢、告别的精确快速路径，并放在安全扫描之后。
- 明确请求优先由规则处理。
- 多个关系标签只作为 RAG 软信号，不再自动触发 LLM。
- 只有上下文省略、真正低置信度歧义或跨业务复合请求才调用 Router。
- 记录 rule_task、llm_task、task_guard 和调用耗时。

#### 结果

固定 33 turn 路由集中的 Router 调用从 19 次降到 3 次，调用率从 57.58% 降到 9.09%，never 策略违例从 11 降到 0。

#### 面试表达

我没有简单换一个更快的模型，而是先缩小 Router 的职责。规则适合高频、可解释的确定性路由，LLM 只处理规则无法判断的上下文语义，从而同时改善延迟、成本和稳定性。

### 3.2 LLM 把关系建议降级成普通聊天

#### 现象

“我喜欢了一个女孩子，但她和我不太熟，该怎么办”已经有明确关系建议意图，却被模型改成 general_chat，最终只返回“我在听，你可以继续说”。

#### 根因

LLM 输出被当成最终任务类型，没有保护高置信度规则结果。

#### 解决

- RouteResult 同时保存 rule_task_type 和 llm_task_type。
- 规则高置信度识别为关系建议或约会规划时，LLM 只能校正 Goal、Scenario 和次级标签。
- 只有规则很弱且文本没有明确建议意图时，才允许降级。
- 高风险规则拥有最高优先级，不能被模型覆盖。

#### 经验

LLM Router 应该是校正器，不是没有约束的最终裁判。模型输出必须经过领域规则合并。

### 3.3 RAG 召回了相关但不够合适的问答

#### 现象

“我和她接触很少，怎么创造聊天搭讪机会”曾召回展示优秀、聊天开心但不见面、配不上对方等文档。这些内容不是完全无关，但不是最佳答案依据。

#### 根因

- 规则只覆盖“怎么搭讪”等固定表达，没有覆盖“创造机会、接触很少、第一次聊天”等自然说法。
- Goal 没识别出来后，过滤条件不准确。
- 文档元数据覆盖不完整。
- 错误路由可能把正确文档硬过滤掉。

#### 解决

- 扩展 Goal 和 Scenario 的自然语言表达。
- Goal/Scenario 改为软加权，不默认硬过滤。
- Qdrant 先召回 15 个候选，再结合标题、标签、Goal、Scenario 和关系阶段重排，最后取约 5 个。
- Seed 和正式文档先统一数据模型，再按 ID 和规范化问题去重。

#### 取舍

宁愿多召回候选再重排，也不让一次错误分类造成零召回。对于当前规模的知识库，这比复杂硬过滤更稳。

### 3.4 RAG 和记忆链路耗时过长

早期 Trace 中 RAG 约 12 秒，记忆抽取约 27 秒。不能简单把所有模块耗时相加，因为 LangGraph 中有并行任务，必须看开始时间和重叠关系。

处理方式：

- Chat 等待输入时后台预热 Embedding。
- 首个请求只等待同一个 warmup task，不重复加载模型。
- 每轮只生成一次查询向量。
- 记忆抽取在交互式 chat 中后台执行；单次 advice 命令默认等待。
- 记录模型尝试、重试原因、token 和耗时。
- 进程退出时保留 shutdown grace，减少后台任务丢失。

后台化降低用户等待，但不会降低模型本身的 CPU 或网络耗时。后续可以引入独立任务队列、批量 Embedding 和持久化 worker。

### 3.5 结构化 JSON 解析失败

记忆抽取曾出现“结果不是约定的 JSON 结构”。数据库 Trace 中第一次尝试输出被截断，JSON 在字符串中途 EOF，completion token 达到上限，第二次重试才成功。

处理方式：

- Pydantic 校验 AtomicExtraction、RouteCorrection 和最终回答结构。
- 解析失败执行有限次重试，而不是无限重试。
- Prompt 和 response_format 同时约束 JSON。
- 保存 attempt 级状态、耗时、token 和 retry_reason。
- 校验证据是否逐字来自用户原文。

结构化输出不能只依赖 Prompt，应该是 Prompt + SDK 约束 + Pydantic + 重试 + Trace。

### 3.6 502 或空响应不能靠猜原因

曾出现：

~~~text
Unexpected Response: 502 (Bad Gateway)
Raw response content: b''
~~~

不能把所有 502 都归因于模型。先看失败阶段：

- 阶段是 RAG 检索：检查 Qdrant 容器、端口、collection。
- 阶段是模型生成：检查 OpenAI-compatible endpoint、超时、重试和 response content。
- 阶段是结构化解析：检查 finish_reason、截断和 Pydantic 错误。

最小 Qdrant 检查：

~~~powershell
docker compose up -d
uv run loveapp knowledge search "测试检索"
~~~

关键经验是用 Trace 区分基础设施故障、模型故障和业务解析故障，而不是凭错误字符串猜测。

### 3.7 记忆模型把多条事实合成一条

一句话可能同时包含用户喜欢对方、持续主动聊天、过去互动减少和最近一次互动改善。早期模型容易返回一条无法独立更新的综合记忆。

解决：

- 一条 claim 只能表达一个可独立确认、更新或删除的信息。
- event 记录一次有边界互动，pattern 记录重复行为或时间趋势。
- valence 是属性，不是记忆类型。
- evidence_spans 必须来自用户原文。
- 服务层二次 atomize，并过滤低置信度候选。
- supersedes_id 只有在用户明确纠正、更新或否定旧记忆时才使用。

### 3.8 event、pattern 和 advice_outcome 的边界

| 类型 | 例子 | 处理 |
|---|---|---|
| stable_fact | 我们是同学、已经确认关系 | 通常长期 |
| preference | 对方喜欢清淡、预算不超过 300 | 长期，允许纠正 |
| interaction_event | 昨晚因为消费问题吵架 | 一次事件 |
| interaction_pattern | 最近两周联系明显减少 | 区间趋势 |
| advice_outcome | 调整做法后双方和好 | 重要结果 |
| planned_event | 下周有小组讨论、后天参加活动 | 带时间窗口的未来安排，过期后不进入有效上下文 |

“下周准备带她吃饭”是短期计划，不应伪装成已经发生的 event；当前使用 planned_event 保存，并通过 expires_at 控制有效期。

### 3.9 Memory Gate 漏掉真实有价值的事实

#### 真实案例

~~~text
我对象其实平时都很勤俭节约，她买衣服鞋子都是买很经济实惠的，
可能就是因为消费观念不一样造成的吧，你觉得呢
~~~

以及：

~~~text
我考虑到她的消费观，我还是选择了一家平价餐厅，
她得知之后很开心，我俩和好了
~~~

旧 Gate 都返回 no_durable_signal，因此记忆模型根本没有收到文本。

#### 根因

Gate 是抽取前的轻量规则层，不是语义模型。旧规则没有覆盖勤俭节约、经济实惠、消费观、和好、开心和建议采纳结果。

#### 解决

- 扩展 preference、relationship_event 和 advice_outcome 信号。
- 事实陈述和“你觉得呢”混合时仍允许进入模型，问题部分由模型放入 discarded_spans。
- Prompt 区分已发生的 interaction_event 与未来的 planned_event，现实结果使用 advice_outcome。
- 增加 partner 偏好、user_belief 和 outcome 的回归样例。

当前期望：

- “对象平时勤俭节约” -> partner preference 或稳定习惯。
- “可能是消费观不同” -> user_belief，不能当客观原因。
- “选择平价餐厅后她开心、双方和好” -> advice_outcome + interaction_event。
- “下周准备带她吃饭” -> planned_event；不是已发生的 interaction_event，并设置有效期。

### 3.10 记忆重复与关系串线

曾发现 memory watch 中出现重复内容，或者不同会话/关系的内容容易混在观察结果里。

处理方式：

- user_id + relationship_id 是长期记忆隔离边界。
- conversation_id 只表示短期消息历史。
- 分开保存 messages、memory_items 和 memory_extraction_runs。
- 使用 dedupe_key、status、supersedes_id 做去重和状态归档。
- compact 先预览，再把重复记忆标记为 superseded，不直接物理删除。
- watch 默认按关系显示，定位单次会话时显式传 conversation_id。

### 3.11 安全规则与否定表达

“我不会跟踪她”和“我准备跟踪她”有相似词但风险完全不同。安全扫描在普通路由和 LLM Router 之前，支持否定窗口和同义表达；高风险请求直接进入 high_risk_response，不能被 LLM 降级。高风险召回率单独评测。

### 3.12 异步任务竞态

Embedding 预热任务和首个请求可能同时到达。解决方式是保存唯一 warmup task，首个请求等待同一任务；记忆抽取由 MemoryService 跟踪，chat 可后台执行，单次 advice 默认等待，进程退出时使用 shutdown grace。

## 4. 路由整改前后数据

固定数据集 routing/cases_v2.jsonl：12 个多轮会话、33 个 turn、22 个带历史上下文的 turn。

| 指标 | 整改前 | 整改后 |
|---|---:|---:|
| turn 通过率 | 36.36% | 100% |
| 会话通过率 | 8.33% | 100% |
| Task 准确率 | 90.62% | 100% |
| 主场景准确率 | 52.17% | 100% |
| 高风险召回率 | 100% | 100% |
| Goal F1 | 0.48 | 1.00 |
| Router 调用次数 | 19/33 | 3/33 |
| Router 调用率 | 57.58% | 9.09% |
| never 策略违例 | 11 | 0 |

3 次 Router 调用来自跨业务复合、上下文省略和真正含糊的输入。评测使用确定性的 RecordingRouteCorrector，不包含真实 DeepSeek 网络延迟，目的是隔离路由策略。

记忆 Gate 的 13 个固定样例在当前版本上达到 100% 召回和 100% 特异度；这是 Gate 指标，不等价于真实模型抽取质量。

## 5. 如何验证问题是否解决

### 单元测试和静态检查

~~~powershell
uv run pytest -q
uv run ruff check .
~~~

当前版本全套测试：102 passed。

### 路由回归

~~~powershell
uv run loveapp eval routing --dataset evals/routing/cases_v2.jsonl --output evals/baselines/routing_v2_post_change.json
~~~

### RAG 检查

~~~powershell
uv run loveapp knowledge search "和对象吵架后怎么沟通？" --limit 5
~~~

### 记忆检查

~~~powershell
uv run loveapp memory runs --user-id local-user --relationship-id <relationship-id> --conversation-id <conversation-id> --json
uv run loveapp memory list --user-id local-user --relationship-id <relationship-id> --json
~~~

先看 Gate 是否通过，再看模型 attempts，最后看 saved_memory_ids。这样可以区分 Gate 漏召回、模型空响应、JSON 校验失败、置信度过滤和持久化/去重问题。

## 6. 面试高频问题

### 为什么不每轮调用 LLM Router？

明确输入不需要开放式语义判断。每轮调用会增加延迟、成本和故障面，所以规则处理高频路径，LLM 只处理上下文歧义和复合任务。

### 为什么 RAG 不直接按 Scenario 硬过滤？

路由可能错，硬过滤会造成零召回。当前知识库先宽召回，再用 Scenario、Goal、标题和标签软重排。

### 如何避免记忆污染？

Gate 先拦截纯咨询和假设，但会召回带明确时间锚点的未来安排；模型输出原子 claims，服务层校验证据、置信度、时间、expires_at 和 supersedes_id，再以 proposed 状态落库。助手回复不能作为用户事实。

### 如何证明路由变快？

用 Trace 分解阶段，再用不调用真实模型的固定评测集统计 Router 调用率和策略违例；真实模型端到端延迟另行记录，避免把两种指标混在一起。

### 目前还剩什么问题？

- 更复杂的用户主动目标仍可继续拆出 pending_intention；具体未来事件已由 planned_event 和过期机制覆盖。
- 真实模型的 JSON 截断、网络错误和成本仍需持续统计。
- RAG 评测集规模较小，需要更多人工标注和难负例。
- 记忆 Gate 规则仍需从关键词逐步演进为“规则召回 + 轻量语义二次判断”。

## 7. 项目取舍与工程方法

1. 规则负责边界和高频路径，模型负责语义补充。
2. RAG 先宽召回，再软重排，避免错误硬过滤。
3. 先定义 event、pattern、preference 和 advice_outcome，再设计 Prompt。
4. summary 可以概括，但 evidence_spans 必须来自原文。
5. 没有 Trace 证据时，不把可能原因说成已确认根因。
6. 固定评测集独立于临时单元样例，避免用同一句数据证明实现有效。

## 8. 面试复盘模板

~~~text
当时的现象是……
我先通过哪个 Trace 或数据库字段确认了……
因此可以排除……
真正的根因是……
我在什么边界内修改……
用什么固定数据和指标验证……
结果从……变成……
目前还剩什么风险……
~~~

## 9. 证据索引

- 顶层会话图：src/loveapp/agents/conversation.py
- 关系建议图：src/loveapp/agents/advice.py
- 混合路由：src/loveapp/application/routing.py
- 记忆 Gate：src/loveapp/application/memory_gate.py
- 记忆服务与原子化：src/loveapp/application/memory.py
- 记忆模型 Prompt：src/loveapp/adapters/memory/openai_compatible.py
- Qdrant 召回与重排：src/loveapp/adapters/knowledge/qdrant.py、scoring.py
- SQLite 适配器：src/loveapp/adapters/memory/sqlite.py
- Trace：src/loveapp/core/timing.py
- 多轮路由集：evals/routing/cases_v2.jsonl
- 路由整改前报告：evals/baselines/routing_v2_pre_change.json
- 路由整改后报告：evals/baselines/routing_v2_post_change.json
- 记忆 Gate 测试：tests/test_memory_gate.py
