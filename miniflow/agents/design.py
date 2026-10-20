# -*- coding: utf-8 -*-
"""设计Agent：把需求文档加工成模块化的技术设计方案。"""
from ..agent import BaseAgent
from ..utils import to_json_str


class DesignAgent(BaseAgent):
    name = "设计Agent"
    role = "design"
    persona = ("你是一名务实的软件架构师，设计遵循 KISS 原则：模块划分清晰、"
               "接口明确、能不引入的依赖就不引入，输出始终为简体中文。")
    downstream = "编码Agent"

    def run(self, ctx, inputs: dict) -> dict:
        requirements = inputs.get("requirements")
        if not isinstance(requirements, dict):
            raise ValueError("设计Agent需要上游产物 requirements（dict），实际: {}".format(
                type(requirements).__name__))

        prompt = """## 任务
请根据需求文档输出技术设计方案。

## 输入: 需求文档
{requirements}

## 输出JSON格式
{{
  "architecture": "总体架构与分层说明",
  "tech_stack": ["技术选型1"],
  "modules": [
    {{"name": "模块名", "responsibility": "职责", "interface": "关键接口/函数签名"}}
  ],
  "data_flow": "一次典型请求在各模块间的流转路径"
}}""".format(requirements=to_json_str(requirements))

        result = self.ask(prompt)
        result.setdefault("_requirement_summary", requirements.get("summary", ""))

        self.handover(ctx, self.downstream,
                      "设计方案已就绪: 共{}个模块（{}）".format(
                          len(result.get("modules", [])),
                          str(result.get("architecture", ""))[:40]))
        return result
