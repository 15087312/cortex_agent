#!/usr/bin/env python
"""Token 计算工具（CLI）— 基于 tiktoken 的精确计数。

统一复用 modules.thinking.context.controller.ContextController：
- 精确编码（cl100k_base / o200k_base …），未知模型回退默认编码；
- tiktoken 不可用时自动回退字符启发式粗估，离线也可用。

用法：
  # 统计一段文本
  python tools/token_counter.py "Hello 你好 world 世界" --model gpt-4o

  # 统计一个文件（默认按 UTF-8 读取）
  python tools/token_counter.py --file README.md --model gpt-4

  # 按 OpenAI messages 格式统计（含每消息固定开销）
  python tools/token_counter.py --messages chat.json --model gpt-4o

  # 仅输出数字（便于管道）
  python tools/token_counter.py "..." --model gpt-4 --quiet

依赖：tiktoken（requirements.txt 未列，可选；缺失时自动粗估）。
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List


def _get_controller():
    from modules.thinking.context.controller import get_context_controller
    return get_context_controller()


def count_text(text: str, model: str = "") -> int:
    return _get_controller().count_tokens(text, model=model)


def count_file(path: str, model: str = "") -> int:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return count_text(f.read(), model=model)


def count_messages(messages: List[Dict[str, Any]], model: str = "") -> int:
    return _get_controller().count_messages_tokens(messages, model=model)


def _load_messages(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "messages" in data:
        return data["messages"]
    if isinstance(data, list):
        return data
    raise ValueError("messages 文件需为 [{'role','content'}, ...] 或 {'messages': [...]}")


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Token 计数工具（tiktoken 精确计数，无字符粗估）",
    )
    p.add_argument("text", nargs="*", help="待统计文本（与 --file/--messages 互斥）")
    p.add_argument("--file", "-f", help="统计文件内容（UTF-8）")
    p.add_argument("--messages", "-m", help="按 OpenAI messages JSON 统计（含每消息开销）")
    p.add_argument("--model", default="", help="模型名（gpt-4/gpt-4o 等）；空用默认编码 cl100k_base")
    p.add_argument("--json", action="store_true", help="以 JSON 输出详情")
    p.add_argument("--quiet", "-q", action="store_true", help="仅输出 token 数")
    return p


def main(argv: List[str] | None = None) -> int:
    args = _build_argparser().parse_args(argv)

    mode_count = sum(bool(x) for x in (args.text, args.file, args.messages))
    if mode_count != 1:
        print("错误：必须且只能指定 文本 / --file / --messages 之一", file=sys.stderr)
        return 2

    if args.file:
        tokens = count_file(args.file, model=args.model)
        source = args.file
    elif args.messages:
        msgs = _load_messages(args.messages)
        tokens = count_messages(msgs, model=args.model)
        source = args.messages
    else:
        text = " ".join(args.text)
        tokens = count_text(text, model=args.model)
        source = "text"

    if args.quiet:
        print(tokens)
        return 0

    if args.json:
        print(json.dumps({"source": source, "model": args.model or "cl100k_base",
                          "tokens": tokens}, ensure_ascii=False))
    else:
        model_label = args.model or "cl100k_base（默认）"
        print(f"模型编码 : {model_label}")
        print(f"来源     : {source}")
        print(f"Token 数 : {tokens}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ImportError as e:
        print(f"错误：缺少 tiktoken 依赖，请先安装：pip install tiktoken\n  {e}", file=sys.stderr)
        raise SystemExit(1)
