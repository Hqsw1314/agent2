# MiniFlow 设计文档

## 1. 设计目标

实现一个**简单但完整**的多 Agent 工作流 MVP：

- 足够简单：核心引擎 + 上下文约 700 行，无任何工作流框架依赖；
- 足够完整：覆盖作业要求的顺序、分支、循环、并行、错误处理、状态管理六大机制；
- 可演示：无 API Key 也能离线端到端跑通（MockLLM），有 Key 即切换真实 LLM；
- 可扩展：工作流用 YAML 声明，新增流程不改引擎代码；新增 Agent 只需继承 `BaseAgent`。

## 2. 总体架构

```text
                        ┌────────────────────────────────────────────────┐
                        │                  main.py (CLI)                 │
                        │   --topic / --workflow / --config / --resume   │
                        └───────────────┬────────────────────────────────┘
                                        │ 加载 YAML
        ┌───────────────────────────────▼───────────────────────────────┐
        │                     WorkflowEngine (引擎)                      │
        │  顺序(next) / 分支(branches+when) / 循环(回边+保护) / 并行(线程池) │
        │  重试(指数退避) / 快照(state.json) / 断点恢复(--resume) / 日志    │
        └───────┬───────────────────────────────┬───────────────────────┘
                │ 每步: inputs解析($引用) → agent.run() → output写回        │
                ▼                               ▼
   ┌────────────────────────┐      ┌─────────────────────────────────┐
   │  Agent 节点(可插拔)      │      │     WorkflowContext (中枢)       │
   │  RequirementAgent      │      │  data      共享状态(产物仓库)     │
   │  DesignAgent           │◄────►│  messages  消息传递(通信链路)     │
   │  CodingAgent(+FE/BE)   │ 读写 │  events    事件日志(执行追踪)     │
   │  ReviewAgent(项目1延续) │      │  visits    访问计数(循环保护)     │
   │  FixAgent / DeliverAgent│     │  snapshot  序列化(断点恢复)       │
   └───────────┬────────────┘      └─────────────────────────────────┘
                │ ask(): 拼提示词 → LLM → 解析JSON
                ▼
   ┌─────────────────────────────────────────┐
   │             LLM 接入层 (llm.py)           │
   │  OpenAICompatLLM  DashScope qwen-plus    │
   │  MockLLM          离线确定性演示          │
   └─────────────────────────────────────────┘
```

分层职责：

| 层 | 模块 | 职责 |
|---|---|---|
| 应用层 | `main.py`, `workflow.yaml` | 命令行参数、流程声明 |
| 引擎层 | `engine.py` | 编排调度：分支/循环/并行/重试/快照/恢复 |
| 协作层 | `context.py` | Agent 间共享状态、消息、事件、快照 |
| 节点层 | `agents/*.py` | 各角色的业务逻辑（提示词+产物加工） |
| 模型层 | `llm.py` | 统一 LLM 接口，真实/离线可切换 |

## 3. 关键设计决策

### 3.1 Agent 间如何协作：状态为主、消息为辅

MiniFlow 同时实现了两种经典协作机制，各司其职：

**共享状态（主通道——传内容）**
每个步骤的返回值由引擎写入 `ctx.data[output键]`，下游步骤在 YAML 里用
`inputs: {code: $code}` 声明依赖，引擎在调度前解析注入。产物本体永远走这条通道，
Agent 拿到的是结构化 dict，直接进提示词。

**消息传递（辅通道——传通知）**
Agent 交接时调用 `ctx.send(收件人, 类型, 摘要)`，只传"发生了什么"不传大内容：

- `artifact`：产物已就绪的通知（如"需求文档已就绪，功能需求4条"）；
- `control`：控制指令（如审查Agent通知修复Agent"2个问题待修复"）；
- 审查Agent 还会读取收件箱（`ctx.messages_for`）感知修复Agent的回复。

消息全部留痕，构成可审查的通信链路（打印到日志 + 写入 `events.json` + 汇总进 `report.md`）。

### 3.2 分支与循环：数据驱动的路由

- 分支条件是面向共享状态的表达式：`review.passed == true`、`review.score >= 80`，
  由 `eval_when()` 求值（路径解析 + 字面量比较）；
- 循环不需要专门语法：**分支指回上游步骤即成环**（review → fix → review）；
- 死循环双重保护：单步骤 `max_visits`（默认8）+ 全局 `max_steps`（默认50），
  超限即熔断并保存失败现场快照。

这样"审查不通过就修复再审"只是 YAML 里的两行路由规则，引擎零特判。

### 3.3 并行：线程池 + 汇合屏障

`type: parallel` 步骤把子步骤交给 `ThreadPoolExecutor` 并发执行（IO 密集的
LLM 调用场景下线程池足够），全部完成后才路由下一步。`ctx` 的读写都有锁保护。

### 3.4 错误处理：重试 → 现场 → 恢复

```text
步骤失败 ──► 重试(默认2次, 指数退避1s/2s/4s)
              │ 仍失败
              ▼
        保存失败现场 state_failed.json (含: 共享状态 + visits + 失败步骤指针)
              │
              ▼  人工排查修复后
        python main.py --resume
              │ 引擎读取快照, 从失败步骤重跑
              ▼
        已完成步骤不重复执行(LLM不重复计费), 成功后清除失败快照
```

每步成功也会写 `state.json`（含下一步指针），理论上可在任意步骤间断点续跑。

### 3.5 LLM 接入：复用项目1 + 离线降级

- 与项目1完全相同的接入方式：OpenAI 兼容 SDK + DashScope `qwen-plus` +
  `.env` 中的 `DASHSCOPE_API_KEY`；
- `provider: auto`：检测到 Key 用真实 LLM，没有则自动降级 `MockLLM`——
  按 `[ROLE:xxx]` 标记返回确定性模拟产物，并**确定性地演示"首查62分不通过 →
  修复 → 复审88分通过"**的分支循环，保证评审环境（无网无Key）也能看到完整流程；
- 所有 Agent 的输出强制 JSON Schema（`ask()` 内解析失败即抛 `AgentError` 触发重试），
  结构化产物是共享状态机制可行的前提。

## 4. 一次执行的完整时序（离线模式实测）

```text
main.py ──load──► workflow.yaml
   │ ──create──► MockLLM (无Key降级)
   ▼
Engine.run(topic)
   │
   ├─ requirement: RequirementAgent.run ──► ctx.data["requirements"]
   ├─ design:      DesignAgent.run($requirements) ──► ctx.data["design"]
   ├─ coding:      CodingAgent.run($requirements,$design) ──► ctx.data["code"]
   ├─ review#1:    ReviewAgent.run(...) ──► {passed:false, score:62}
   │                  └─分支: review.passed==true 不成立 → next=fix
   ├─ fix#1:       FixAgent.run($code,$review) ──► ctx.data["code"](覆盖) + fix_round=1
   │                  └─next=review (循环回边)
   ├─ review#2:    ReviewAgent.run(...,fix_round=1) ──► {passed:true, score:88}
   │                  └─分支命中 → next=deliver
   └─ deliver:     DeliverAgent.run(全部产物) ──► final_report
          │
          ▼
   outputs/: report.md(交付报告) + events.json(日志/消息) + state.json(终态)
```

## 5. 已知限制与展望

- 循环保护是"熔断"而非"语义收敛判断"：审查标准完全信任 LLM 输出，未做外部校验；
- 快照仅含共享状态，不含消息/事件历史（恢复后日志重新累计）；
- 条件表达式仅支持单比较运算，复杂条件需拆成多个路由步骤；
- 展望：人工审批节点（human-in-the-loop）、产物版本 diff、子工作流嵌套、基于事件流的执行回放。
