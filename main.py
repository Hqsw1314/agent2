# -*- coding: utf-8 -*-
"""MiniFlow 命令行入口。

常用命令:
  py -3 main.py                                   # 默认主题, 有Key用真实LLM, 否则离线演示
  py -3 main.py --topic "开发一个五子棋游戏"       # 自定义主题
  py -3 main.py --config config.example.yaml      # 指定配置
  py -3 main.py --resume                          # 从失败断点恢复
  py -3 main.py --workflow examples/parallel_example.yaml   # 并行示例
"""
import argparse
import os
import sys

try:
    import yaml
except ImportError:
    print("缺少依赖 pyyaml, 请先执行: pip install pyyaml")
    sys.exit(2)

try:
    from dotenv import load_dotenv  # 可选: 支持 .env 文件(与项目1一致)
except ImportError:
    def load_dotenv():
        return False

from miniflow.engine import WorkflowEngine
from miniflow.errors import WorkflowError
from miniflow.llm import create_llm


def load_yaml(path):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        prog="miniflow", description="MiniFlow - 简单工作流 MVP (HW2)")
    parser.add_argument("--topic", default="开发一个命令行待办事项(Todo)应用",
                        help="软件主题（需求Agent的输入）")
    parser.add_argument("--workflow", default="workflow.yaml", help="工作流定义文件")
    parser.add_argument("--config", default="config.yaml", help="LLM配置文件")
    parser.add_argument("--out-dir", default="outputs", help="产物输出目录")
    parser.add_argument("--resume", action="store_true", help="从失败断点恢复执行")
    args = parser.parse_args(argv)

    for required in (args.workflow,):
        if not os.path.exists(required):
            print("找不到工作流定义文件: {}".format(required))
            return 2

    # 与项目1一致: 支持 .env 中的 DASHSCOPE_API_KEY
    load_dotenv()

    cfg = {}
    if os.path.exists(args.config):
        cfg = (load_yaml(args.config) or {}).get("llm") or {}
    else:
        print("[i] 未找到 {} (正常, 该文件含密钥不入库). 参考流程: 无Key则自动进入离线演示模式".format(args.config))

    try:
        llm = create_llm(cfg)
    except Exception as e:
        print("[x] LLM 初始化失败: {}".format(e))
        return 2

    if llm.name == "mock":
        print("[i] 当前为离线演示模式(MockLLM): 无需API Key, 产物为模拟数据, 用于流程验证与演示")
        print("[i] 如需真实LLM: 复制 config.example.yaml 为 config.yaml 并在 .env 配置 DASHSCOPE_API_KEY")
    else:
        print("[i] LLM 后端: {} 模型: {}".format(llm.name, getattr(llm, "model", "?")))

    wf = load_yaml(args.workflow)
    engine = WorkflowEngine(wf, llm, out_dir=args.out_dir)

    print("=" * 60)
    print("工作流: {} | 主题: {}".format(wf.get("name", "-"), args.topic))
    print("=" * 60)

    try:
        ctx = engine.run(topic=args.topic, resume=args.resume)
    except WorkflowError as e:
        print("[x] 工作流失败: {}".format(e))
        print("[i] 已保存失败现场({}), 修复后可执行: py -3 main.py --resume".format(
            os.path.join(args.out_dir, "state_failed.json")))
        return 1
    except Exception as e:
        print("[x] 未预期的错误: {}".format(e))
        return 1

    print("=" * 60)
    print("工作流执行完成")
    print("  主题: {}".format(ctx.topic))
    print("  产物: {}".format(", ".join(k for k in ctx.data if k != "topic")))
    print("  通信: {}条消息, {}条事件, 步骤执行{}次".format(
        len(ctx.messages), len(ctx.events), sum(ctx.visits.values())))
    print("  输出: {} (report.md / events.json / state.json)".format(
        os.path.abspath(args.out_dir)))
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
