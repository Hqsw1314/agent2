# -*- coding: utf-8 -*-
"""MiniFlow 异常类型定义。"""


class WorkflowError(Exception):
    """工作流级错误：由引擎在流程无法继续时抛出（步骤失败、死循环保护触发等）。"""


class AgentError(Exception):
    """Agent 执行错误：Agent 内部逻辑或 LLM 输出解析失败时抛出。"""
