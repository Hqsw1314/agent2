# -*- coding: utf-8 -*-
"""Mini SE 流程的 Agent 节点集合。

流程: 需求Agent -> 设计Agent -> 编码Agent -> 审查Agent ->(通过)-> 交付Agent
                                        |(不通过)> 修复Agent -> 审查Agent(循环)

其中"审查"能力延续自项目1 (Code Review Agent): 项目1实现了单 Agent 的
代码审查, 本项目把它升级为工作流中的一个可编排节点。
"""
