"""MiniFlow —— 一个极简的多 Agent 工作流引擎（HW2: Simple Workflow MVP）。

核心概念:
  - WorkflowContext: Agent 间的共享状态 + 消息传递 + 事件日志
  - BaseAgent:       工作流节点, 子类实现 run(ctx, inputs)
  - WorkflowEngine:  按 YAML 定义的步骤图驱动 Agent 顺序/分支/循环/并行执行
"""

__version__ = "1.0.0"
