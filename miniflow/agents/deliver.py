# -*- coding: utf-8 -*-
"""交付Agent：终点节点，把全流程产物汇编为最终交付报告(Markdown)。"""
from ..agent import BaseAgent
from ..utils import to_json_str


def _section_requirements(req) -> str:
    if not isinstance(req, dict):
        return ""
    lines = ["### 需求文档", "", "**概述**: {}".format(req.get("summary", "-")), ""]
    fr = req.get("functional_requirements") or []
    if fr:
        lines.append("功能需求:")
        lines += ["- {}".format(x) for x in fr]
    nfr = req.get("non_functional_requirements") or []
    if nfr:
        lines += ["", "非功能需求:"] + ["- {}".format(x) for x in nfr]
    ac = req.get("acceptance_criteria") or []
    if ac:
        lines += ["", "验收标准:"] + ["- {}".format(x) for x in ac]
    return "\n".join(lines) + "\n"


def _section_design(design) -> str:
    if not isinstance(design, dict):
        return ""
    lines = ["### 设计方案", "", "**架构**: {}".format(design.get("architecture", "-")), ""]
    stack = design.get("tech_stack") or []
    if stack:
        lines += ["技术选型: " + "、".join(str(x) for x in stack)]
    modules = design.get("modules") or []
    if modules:
        lines += ["", "| 模块 | 职责 | 接口 |", "|---|---|---|"]
        for m in modules:
            lines.append("| {} | {} | {} |".format(
                m.get("name", "?"), m.get("responsibility", "?"), m.get("interface", "?")))
    flow = design.get("data_flow")
    if flow:
        lines += ["", "**数据流**: {}".format(flow)]
    return "\n".join(lines) + "\n"


def _section_code(key, code) -> str:
    if not isinstance(code, dict):
        return ""
    lines = ["### 代码产物: {}".format(key),
             "", "语言: {}　运行方式: {}".format(
                 code.get("language", "?"), code.get("run_instructions", "-"))]
    notes = code.get("fix_notes")
    if notes:
        lines += ["", "> 修复说明: {}".format(notes)]
    for f in code.get("files", []):
        lines += ["", "**{}**".format(f.get("path", "?")), "",
                  "```", str(f.get("content", "")), "```"]
    return "\n".join(lines) + "\n"


def _section_review(review) -> str:
    if not isinstance(review, dict):
        return ""
    lines = ["### 审查结论", "",
             "- 是否通过: {}".format("✅ 通过" if review.get("passed") else "❌ 未通过"),
             "- 评分: {}".format(review.get("score", "?")),
             "- 评审意见: {}".format(review.get("comment", "-"))]
    for i in review.get("issues", []):
        lines.append("- 问题[{}]: {}（建议: {}）".format(
            i.get("severity", "?"), i.get("description", "?"), i.get("suggestion", "-")))
    return "\n".join(lines) + "\n"


class DeliverAgent(BaseAgent):
    name = "交付Agent"
    role = "deliver"
    persona = "你负责把多 Agent 协作产出汇编为一份结构清晰的交付报告。"
    downstream = ""

    def run(self, ctx, inputs: dict) -> dict:
        parts = ["# Mini SE 流程交付报告", ""]
        order = [("requirements", _section_requirements),
                 ("design", _section_design),
                 ("review", _section_review),
                 ("code", _section_code),
                 ("code_frontend", _section_code),
                 ("code_backend", _section_code)]
        for key, fn in order:
            if key in inputs and inputs[key] is not None:
                section = fn(key, inputs[key]) if key == "code" or key.startswith("code_") else fn(inputs[key])
                if section:
                    parts += [section, "---", ""]

        history = ctx.get("fix_history")
        if history:
            parts += ["### 修复循环记录", ""]
            parts += ["- 第{}轮: 修复{}个问题（{}）".format(
                h.get("round"), h.get("issues_fixed"), str(h.get("notes", ""))[:60])
                for h in history]
            parts.append("")

        report = "\n".join(parts)
        summary = "全流程完成: 需求/设计/编码/审查{}交付产物已汇编（报告约{}字符）".format(
            "与修复、" if history else "、", len(report))

        return {"summary": summary, "report_markdown": report}
