# Claude Code Agent 设计经验整理

本文先梳理 Claude Code 相关官方入口，再提炼 Anthropic 对 agent 设计的稳定经验，并补充一组与 Claude Code、MCP、内置 Tool 分层相关的关键判断，便于后续落地。

## Anthropic 官方比较稳定的 agent 设计经验

### 1）单 agent + 好工具 + 好上下文，通常优先于过早上多 agent

Anthropic 在《Building Effective AI Agents》中反复强调：有效方案通常不是从复杂框架起步，而是从简单、可组合的模式逐步扩展。复杂度上升过早，收益往往并不成比例。

对数据治理、元数据、找表这类场景，可以直接转化为以下判断：

- 不建议一开始就设计成 `orchestrator + planner + router + evaluator + 多个 worker`
- 更稳妥的起点是：一个主 agent + 一组高质量 MCP / 查询工具 + 一套稳定的上下文注入机制
- 多 agent 更适合在“并行搜索、多视角验证、长任务拆分”这些收益明确的场景中引入

很多项目最终效果不佳，并不是模型能力不够，而是系统架构过度设计。

### 2）需要从 prompt engineering 过渡到 context engineering

Anthropic 在 2025 年的相关表述已经很明确：真正决定 agent 质量的，往往不是一句 system prompt，而是如何准备、过滤、压缩并滚动传递上下文。Claude Code 本身也会在长对话中压缩历史，优先保留关键决策、未解决问题和实现细节，并结合最近访问的文件继续工作。

这点对数据agent的场景尤其重要。因为这里处理的不是闲聊，而是：

- 表结构
- 血缘
- 口径
- 枚举值
- 任务上下文
- 质量规则
- 历史争议

真正需要设计的是以下五层上下文：

1. 任务上下文：当前用户要解决什么问题
2. 领域上下文：广告 / 赔付 / UP主 / Kingdee / 简道云等业务域信息
3. 资产上下文：表、字段、分层、owner、热度、质量分等资产特征
4. 过程上下文：已经查询过什么、排除过什么、还缺什么证据
5. 决策上下文：最终为什么选择这张表，风险点是什么

如果这五层混在一起，agent 很容易退化成“会说话的 grep”，这类问题往往比模型本身的能力边界更致命。

### 3）`CLAUDE.md` 是低成本的项目级长期记忆

官方明确建议在仓库中放置 `CLAUDE.md`，用于帮助 Claude Code 理解代码库、约定和标准。企业部署页也将其视为团队共享配置的一部分。

经验上，`CLAUDE.md` 最适合承载这些信息：

- 项目目标与边界
- 目录结构
- 核心实体和术语
- 禁止事项
- 常见命令
- 测试 / 发布流程
- 风险约束
- 输出格式约定

结合的业务场景，可以直接固化为类似规则：

- `DWS` 优先，`DWD` 回退，`ODS` 仅作为证据来源
- 输出必须包含：候选表、命中原因、排除原因、口径风险
- 对包含 `temp` / `bak` / `test` / `history_fillback` 的资产降权
- 命中枚举值表时，必须回显枚举覆盖率

相比把这些规则全部写进 system prompt，放入 `CLAUDE.md` 更稳定，因为它属于项目长期上下文，而不是单轮会话的临时上下文。

### 4）subagent 的价值在于隔离上下文、权限和职责

官方 subagents 文档说明得很清楚：每个 subagent 都有独立的 system prompt、工具访问范围和权限，并运行在独立的上下文窗口中。Claude 会在任务匹配时将工作委派给相应 subagent。

因此，subagent 更适合承担三类任务：

- 专门化判断：例如 SQL reviewer、数据口径审计员、DDL 审核员
- 重工具操作：例如 lineage explorer、enum profiler、quality checker
- 高风险任务隔离：例如只允许读取 schema、不允许执行写操作

以下情况通常不适合上 subagent：

- 任务本身很短
- 领域边界并不清晰
- 只是为了追求“看起来高级”的架构形式

如果要做 “Best Table Agent”，更建议按业务功能拆分，例如：

- `table_retriever`
- `lineage_analyst`
- `semantic_scorer`
- `risk_reviewer`

这类按职能拆分的方式，通常比 `planner / executor / critic` 这类抽象角色更稳。

### 5）能确定性的部分，不应交给模型自行记忆

Anthropic 对 hooks 的定位非常明确：在 agent 生命周期的特定节点执行确定性动作，用于强制项目规则、自动化重复步骤以及对接现有工具链。它的目的之一，就是避免“依赖 LLM 记得去做”。

这里的经验非常直接：

- LLM 适合做判断
- hook 适合做刚性规则
- 两者混用，系统会逐渐漂移

因此，以下动作更适合放进 hook，而不是只写在 prompt 中：

- 每次修改 SQL 前先跑 lint
- 每次生成表推荐结果时附带风险标签
- 每次写文件前检查是否包含敏感路径
- 每次结束前自动汇总变更点
- 每次调用外部工具前记录 trace

可以压缩成一句话：需要“必做”的事，用 hook；需要“判断”的事，用 agent。

### 6）skills 适合沉淀高复用工作流，而不是替代 agent

官方将 skills 定义为通过 `SKILL.md` 扩展 Claude 能力的方式。Claude 可在相关场景下自动使用，也可以通过 `/skill-name` 显式调用。Anthropic 还专门给出了 skill authoring best practices，强调 skill 应当简洁、结构化、并经过真实使用验证。

更合适的理解方式是：

- skill 管复用经验
- tool 管执行能力
- agent 管任务决策

在的业务里，适合沉淀为 skill 的通常是：

- “找赔付分析表”
- “做字段口径比对”
- “审查 Hive DDL 分层是否合理”
- “生成资产卡片”

而不适合直接做成 skill 的通常是：

- 海量实时检索本身
- 复杂多轮规划本身
- 需要动态调度多个 MCP 的逻辑本身

### 7）工具设计质量，直接决定 agent 的上限

Anthropic 在 “Writing effective tools for agents” 中强调得很透彻：agent 的整体效果，很大程度上取决于工具质量。MCP 可以承载很多工具，但重点不是“工具越多越好”，而是模型能否正确选择、准确调用并稳定消费返回结果。

在 MCP / metadata 平台设计中，最常见的错误是把后端 API 原样暴露给模型。这通常远远不够。

一个适合 agent 使用的好工具，至少应满足：

- 名称能表达用途
- 入参少且稳定
- 返回结构化、简洁且可消费
- 错误信息可诊断
- 支持分页 / 裁剪 / `topK`
- 结果字段语义清晰
- 不把后端脏细节直接暴露给模型

例如，不建议直接暴露：

- `query_asset_by_xxx_v5beta`

更建议暴露成：

- `search_tables_by_business_term`
- `get_table_asset_card`
- `get_table_lineage_summary`
- `get_field_enums`
- `compare_metric_definitions`

这不是包装层面的美观问题，而是为了降低模型选错工具、填错参数、误读返回值的概率。

### 8）多 agent 真正有价值的场景是并行与证据分治

Anthropic 公开过多 agent research system，其核心并不在于“多 agent 很先进”，而在于两条原则：

- 并行：把独立信息搜集任务并行化
- 分治：让每个 agent 只处理一种证据域

映射到的数据治理场景，多 agent 只有在以下情况中更值得投入：

- 需要同时查询表描述、血缘、任务引用、枚举值、质量规则
- 需要同时从 Hive、元数据平台、任务平台、文档系统多源取证
- 需要多路候选交叉验证，以降低 hallucination 风险

如果只是回答一句“哪个表更好”，却把系统拆成 6 个 agent，通常只会放大原有不稳定性。

### 9）长任务需要 harness，而不是更长的 prompt

Anthropic 还单独写过 long-running agents 的 harness 文章。核心并不是把 prompt 写得更长，而是为 agent 提供一套能够跨 context window 持续推进任务的外部支架。

可以把 harness 理解为 agent 的“操作系统”，通常需要管理：

- 状态持久化
- 阶段性 checkpoint
- 工具调用日志
- 中间产物缓存
- 失败恢复机制
- 人工接管点
- 任务结束条件

因此，一个真正面向生产的 agent，需要重点设计的是：

- `state`
- `artifacts`
- `memory`
- `tool traces`
- `retry policy`
- `handoff policy`

而不是持续打磨 prompt wording 本身。

## 适合业务的设计原则

如果目标是做 AI 找表 / 元数据治理 / 质量闭环 agent，更合理的推进顺序建议如下。

### 第一阶段：先做单 agent

- 一个主 agent
- 5 到 8 个高质量 MCP 工具
- 一个 `CLAUDE.md`
- 若干 hooks 负责刚性校验
- 固定 JSON 输出结构

### 第二阶段：再做专职 subagents

- 检索 agent
- 血缘 agent
- 口径核验 agent
- 风险审查 agent

### 第三阶段：最后再做 orchestration

- 并行取证
- 多候选交叉验证
- 长任务 checkpoint
- 自动摘要与上下文压缩

这一路径与 Anthropic 的公开经验基本一致：从简单、可组合、可控的结构开始，复杂性只在收益明确时引入。

## 补充视角：Claude Code、MCP 与内置 Tool 的分层判断

### 1）Claude Code、MCP、Skills 分别占住了三个关键中间层

这套组合真正有价值的地方，不只是功能多，而是分别卡住了三个高杠杆位置：

- Claude Code：把模型从“会回答”推进到“会执行”
- MCP：把模型接入外部系统的方式，从私有集成提升为通用协议
- Skills：把一次性提示词经验沉淀为可复用的任务能力模块

本质上，这不是单点产品创新，而是“默认入口 + 协议层 + 经验模块层”的闭环。

### 2）MCP 的核心不是 tool calling，而是协议化模型与外部环境的耦合关系

对 MCP 更准确的理解不是“给大模型接工具”，而是：

- 面向模型的外部能力总线
- 上下文分发协议
- 工具调用契约
- AI 时代的异构系统适配中间层

一句话概括：MCP 协议化的不是某一个工具，而是模型如何发现、理解、调用外部世界的整套边界。

### 3）MCP 更像 AI 时代的 ODBC / JDBC 或 LSP，而不是一个普通插件规范

这份分析报告里最精辟的一个判断是：MCP 的历史原型更接近 ODBC / JDBC、LSP，以及 JSON-RPC / function calling 的上层组织。

它解决的是类似的组合爆炸问题：

- 没有标准时，是 `N × M` 的私有对接
- 有了标准后，变成 `N + M` 的适配关系

所以，MCP 的价值不在某个单独 server，而在于 client 与 server 可以围绕协议独立演进，并形成生态效应。

### 4）Claude Code 故意保留 Built-in Tools 与 MCP 的双层结构

Claude Code 并没有试图把所有能力都 MCP 化，而是刻意保留了两层分工：

- Built-in Tools：负责本地高频、低延迟、强状态、强耦合的执行底盘
- MCP：负责外部异构系统、跨产品复用、可插拔扩展的接入能力

所以 “内置 Tool 与 MCP 并存” 不是设计不统一，而是清晰分层。

### 5）Bash / Read / Edit / grep 更像 runtime 底盘，而不是普通插件

报告里有一个很准确的类比：

- Built-in Tools 像内核 `syscall`
- MCP 像外部设备总线

这也是为什么 Bash、Read、Edit、grep 不适合强行做成 MCP：

- 它们是本地运行时能力的一部分
- 它们与 agent loop、权限控制、上下文裁剪、状态保持深度耦合
- 一旦强行协议化，链路会变长，状态同步会更复杂，核心体验会更脆弱

不是做不到，而是层级不对。

### 6）对企业级 Agent / 数据平台最重要的启发，是分层而不是全协议化

这一点对 AI 找表、元数据治理、质量闭环尤其关键：

- MCP 很适合暴露 schema、血缘、枚举值、资产卡片、文档检索、质量规则等能力
- 但 MCP 不适合直接承担“最佳表选择”“口径冲突裁决”“策略加权”“证据充分性判断”这类决策问题

换句话说：

- MCP 适合做能力暴露层
- Agent / Planner / Policy 适合做决策层

真正值得学习的，不是把所有能力都统一成 MCP，而是明确三件事：

- 哪些能力必须内建为底盘
- 哪些能力应该协议化暴露
- 哪些能力必须保留在上层决策与治理

## 补充视角：如何看待“基模派”和 “harness 派”

这两派的核心判断都成立，但通常都会高估自己的适用边界。

### 1）基模派解决的是能力上限问题

“基模派”的核心观点是：很多复杂问题，最终会被更强的基础模型直接吸收，很多工程补丁只是阶段性方案。这种判断并不空泛，它有很强的历史支撑。模型一旦跨过某个能力阈值，原本需要复杂流程编排、手工规则和提示技巧才能完成的任务，往往会迅速退化成“直接问模型即可”。

这一路线的价值在于，它能提醒团队不要把短期工程技巧误当成长期壁垒。

但它的常见问题也很明确：容易低估真实系统中的运行约束。生产环境里的难点，往往不只是“模型能不能做”，而是：

- 能不能稳定做
- 能不能失败恢复
- 能不能被审计
- 能不能控成本
- 能不能跨长任务持续推进
- 能不能平滑接入人工接管

这些问题不是模型更强就会自然消失的，它们本质上更接近运行时系统问题，而不是纯推理问题。

### 2）harness 派解决的是可运营性问题

“harness 派”更关注系统在真实环境中的长期运行约束。它的重点不是让模型更聪明，而是给模型一套外部运行时支架，例如：

- `state`
- `checkpoint`
- `trace`
- `retry`
- `memory`
- `handoff`
- `policy`

只要任务足够长、足够昂贵、足够高风险，harness 基本就是必需品。尤其是 coding agent、research agent、数据治理 agent 这类任务，如果没有 harness，系统很快就会出现漂移、遗忘、不可恢复、不可审计的问题。

但 harness 派也有典型风险：容易把本来可以由更强模型直接解决的问题，过早包装成一套厚重框架。结果通常是：

- 调用链路变长
- 维护成本升高
- 调试难度上升
- 系统越来越像在服务框架本身，而不是服务任务目标

### 3）更合理的判断方式是分清两者各自负责什么

更准确的理解方式不是二选一，而是明确它们各自解决的问题：

- 基模决定能力上限
- harness 决定系统可运营性

因此：

- 短任务、低风险任务，应优先吃基模红利，少做框架
- 长任务、高风险任务、多阶段任务，必须引入 harness
- 模型能力不够时硬堆 harness，通常效果很差
- 任务已经明显是系统工程问题时，仍幻想只靠更强基模，也同样站不住

可以压缩成一句话：

**基模派解决“能力够不够”，harness 派解决“系统能不能长期稳定跑”。**

真正成熟的产品通常不会在两者之间做教条式二选一，而是先用强基模尽量把问题简化，再只为那些不可避免的运行时问题补上 harness。

### 4）映射到 AI 找表 / 元数据治理场景

放到 AI 找表、元数据治理、质量闭环这类场景，可以更明确地拆分：

- 检索理解、语义匹配、口径归纳，优先由基模承担
- 长链路取证、多源查询、结果可追溯、失败恢复、人工复核点，必须由 harness 负责

单靠其中任何一边都不够。真正可落地的系统，通常都是“基模吃理解，harness 吃运行”。

## 现在最值得收藏的官方入口

按实用优先级排序如下：

1. Claude Code 产品页
2. Claude Code docs overview / quickstart
3. `settings` / `CLAUDE.md` / 配置作用域
4. `sub-agents`
5. `hooks guide` + `hooks reference`
6. Agent SDK overview
7. Prompting best practices
8. Building Effective AI Agents
9. Effective context engineering for AI agents
10. Writing effective tools for agents

如果后续需要，可以继续在这份文档基础上扩出一版面向“AI 找表 / 元数据治理”的 Claude Code agent 目录结构、`CLAUDE.md` 模板和 subagent 划分方案。
