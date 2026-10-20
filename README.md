# MiniFlow —— 简单工作流 MVP（HW2）

> 课程作业二：Simple Workflow MVP（工作流模式）
>
> 在项目1（单 Agent 代码审查：[Code Review Agent](../CodeAgent)）的基础上，
> 实现一个**可编排的多 Agent 工作流框架**，并用它跑通一个 Mini 软件工程流程：
> **需求 → 设计 → 编码 → 审查 →（不通过则修复 → 再审查）→ 交付**。

## 1. 项目简介

MiniFlow 是一个用纯 Python 实现的极简多 Agent 工作流引擎（约 1000 行代码，仅 3 个第三方依赖）。

工作流用 YAML 声明，引擎负责按定义驱动多个 Agent 协作完成一个软件工程任务。
Agent 之间通过**共享状态**传递产物、通过**消息**交接通知，全流程有**事件日志**与**快照**可追踪、可恢复。

### 与项目1（Code Review Agent）的关系

| | 项目1 Code Review Agent | 项目2 MiniFlow |
|---|---|---|
| 形态 | 单 Agent 交互式 REPL | 多 Agent 工作流编排框架 |
| 流程 | 用户输入 → 审查 → 输出（直线） | 需求→设计→编码→审查→修复循环→交付 |
| 复用点 | LLM 接入方式（OpenAI 兼容 + DashScope qwen-plus + `DASHSCOPE_API_KEY`）、代码审查职责 | 审查 Agent 由项目1升级节点化而来 |
| 协作 | 无 | 共享状态 + 消息传递 + 分支/循环/并行 |

## 2. 工作流展示

### 2.1 Mini SE 主流程（workflow.yaml）

```text
                    ┌──────────────────────────────────────────┐
                    │                (不通过)                    │
                    ▼                                          │
需求Agent ──► 设计Agent ──► 编码Agent ──► 审查Agent ──► 修复Agent
 需求文档      设计方案       代码产物       │ 审查结论      │ 修复后代码
              │            │            │(通过)         │
              ▼            ▼            ▼                │
           [共享状态 WorkflowContext: requirements/design/code/review...]
                                           │              │
                                           ▼              │
                                        交付Agent ◄────────┘
                                        交付报告 report.md
```

一次离线模式的实际运行日志（节选，完整见 [outputs/events.json](outputs/events.json)）：

```text
[08:43:11] >> [requirement] 需求Agent 开始执行, 输入: topic
[08:43:11] OK  [requirement] 需求Agent 完成 -> 输出键: requirements
[08:43:11] >> [design]      设计Agent 开始执行, 输入: requirements
[08:43:11] >> [coding]      编码Agent 开始执行, 输入: requirements, design
[08:43:11] MSG 审查Agent -> 修复Agent [control]: 审查不通过（得分62），2个问题待修复
[08:43:11] MSG 修复Agent -> 审查Agent [artifact]: 第1轮修复完成（处理2个问题），请重新审查
[08:43:11] MSG 审查Agent -> 交付Agent [artifact]: 审查通过（得分88），可以交付
[08:43:11] OK  [deliver]    交付Agent 完成 -> 输出键: final_report
[08:43:11] ==  工作流完成, 产物: requirements, design, code, review, fix_round, fix_history, final_report
```

可以看到**条件分支与循环真实发生**：首查 62 分不通过 → 走修复分支 → 复审 88 分通过 → 走交付分支。

### 2.2 并行流程示例（examples/parallel_example.yaml）

需求完成后，前端/后端两个编码 Agent **并发执行**，全部完成后汇合交付：

```text
                 ┌──► 前端编码Agent ──┐
需求Agent ───────┤                    ├──► 交付Agent
                 └──► 后端编码Agent ──┘
```

## 3. 环境准备与运行

```bash
# 1) 安装依赖（Python 3.9+）
pip install -r requirements.txt

# 2) 运行主工作流（无 API Key 时自动进入离线演示模式，零配置可跑）
python main.py

# 3) 自定义主题
python main.py --topic "开发一个五子棋游戏"

# 4) 运行并行示例
python main.py --workflow examples/parallel_example.yaml --out-dir outputs_parallel
```

### 使用真实 LLM（可选）

```bash
# 复制配置模板并填入 Key（与项目1同一个 DashScope Key，可直接复用）
copy config.example.yaml config.yaml
copy .env.example .env      # 编辑 .env: DASHSCOPE_API_KEY=sk-xxxx

python main.py --topic "开发一个命令行记账工具"
```

`config.yaml` 与 `.env` 均已被 `.gitignore` 排除，**不会进入 Git**。
任何 OpenAI 兼容服务（智谱/DeepSeek/OpenAI…）只需改 `config.yaml` 的 `base_url` 和 `model`。

### 断点恢复

步骤失败会保存现场快照 `outputs/state_failed.json`，修复问题后：

```bash
python main.py --resume    # 从断点继续, 已完成步骤不会重复执行
```

## 4. 工作流如何定义（YAML）

```yaml
steps:
  - id: review                 # 步骤唯一标识
    agent: review              # 使用哪个 Agent（agents 表中注册）
    inputs: {requirements: $requirements, code: $code}   # $开头=引用共享状态
    output: review             # 返回值写入共享状态的键
    next: fix                  # 默认下一步
    max_visits: 6              # 循环保护上限
    branches:                  # 条件分支（按序求值，第一个命中生效）
      - when: "review.passed == true"
        next: deliver
```

支持的操作符：`==` `!=` `>` `<` `>=` `<=`；比较符左边是共享状态路径，右边是字面量。

## 5. 引擎能力一览

| 能力 | 说明 | 实现位置 |
|---|---|---|
| 顺序执行 | `next` 指针串联 | `engine.py` |
| 条件分支 | `branches[].when` 表达式 | `engine.py: eval_when` |
| 循环 | 分支指回上游步骤成环 + 双重死循环保护（`max_visits`/`max_steps`） | `engine.py` |
| 并行执行 | `type: parallel` 子步骤线程池并发、汇合后继续 | `engine.py: _run_parallel` |
| 状态共享 | `ctx.data` + `$路径` 引用 | `context.py` |
| 消息传递 | `ctx.send()` / 收件箱 `ctx.messages_for()` | `context.py` |
| 执行日志 | 全事件实时打印 + `events.json` 落盘 | `context.py / engine.py` |
| 状态追踪 | 每步写快照 `state.json`（含下一步指针） | `engine.py: _snapshot` |
| 错误恢复 | 步骤重试（指数退避）+ 失败现场快照 + `--resume` 断点续跑 | `engine.py` |
| 离线模式 | 无 Key 自动降级 MockLLM，流程可确定性演示 | `llm.py: MockLLM` |

## 6. 测试

```bash
python -m unittest discover -s tests -v
```

11 个用例覆盖：顺序数据流、条件分支、审查-修复循环、循环保护（死循环熔断）、
失败重试、断点恢复（已完成步骤不重复执行）、并行汇合、表达式求值、配置错误。

## 7. 目录结构

```text
agent2/
├── main.py                  # CLI 入口
├── workflow.yaml            # 主工作流定义（Mini SE 流程）
├── config.example.yaml      # LLM 配置模板（真实配置不入库）
├── .env.example             # API Key 模板
├── requirements.txt
├── miniflow/                # 框架代码
│   ├── context.py           #   WorkflowContext: 状态/消息/事件/快照
│   ├── engine.py            #   WorkflowEngine: 编排/分支/循环/并行/恢复
│   ├── agent.py             #   BaseAgent 基类
│   ├── llm.py               #   LLM 接入（OpenAI兼容 + MockLLM 离线模式）
│   ├── agents/              #   Mini SE 流程的六个 Agent 节点
│   │   ├── requirement.py   #     需求Agent
│   │   ├── design.py        #     设计Agent
│   │   ├── coding.py        #     编码Agent（+前端/后端并行子类）
│   │   ├── review.py        #     审查Agent（项目1职责节点化）
│   │   ├── fix.py           #     修复Agent
│   │   └── deliver.py       #     交付Agent
│   ├── errors.py            #   异常类型
│   └── utils.py             #   JSON 提取等工具
├── examples/
│   └── parallel_example.yaml#   并行示例工作流
├── tests/                   # 单元测试 + 端到端测试
└── outputs/                 # 运行产物（report.md / events.json / state.json，不入库）
```

## 8. 设计文档

架构与协作机制的详细说明见 [DESIGN.md](DESIGN.md)。
