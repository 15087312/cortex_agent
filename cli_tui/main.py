"""入口 — 命令行参数解析 + 启动 Textual App"""

import argparse
import os
import sys


def parse_args():
    parser = argparse.ArgumentParser(
        description="AI CLI — 多模型协作 TUI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python -m cli_tui.main                        # 连接 localhost:8080
  python -m cli_tui.main --api-url http://x:8080 # 连接远程服务
  python -m cli_tui.main --model claude-sonnet-4-5  # 指定模型
        """,
    )
    parser.add_argument(
        "--version", "-v",
        action="version",
        version=_get_version_info(),
        help="显示版本信息"
    )
    parser.add_argument(
        "--api-url",
        default=os.environ.get("API_BASE_URL", "http://localhost:8080"),
        help="后端 API 地址 (默认: http://localhost:8080)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="指定主模型 (如 claude-opus-4-6, claude-sonnet-4-5)",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("SIMPLE_API_KEY", ""),
        help="API 认证密钥 (默认从 SIMPLE_API_KEY 环境变量读取)",
    )
    return parser.parse_args()


def _get_version_info() -> str:
    """获取版本信息"""
    try:
        from cortex.version import get_version_string
        return get_version_string()
    except ImportError:
        return "unknown"


def main():
    args = parse_args()

    # 消除 Rich/Textual 的日志干扰
    os.environ.setdefault("LOGGING_ENABLED", "false")

    from .app import AICLIApp
    from .version_check import get_update_prompt
    from rich.console import Console

    # 检查版本更新
    update_prompt = get_update_prompt()
    if update_prompt:
        console = Console()
        console.print(update_prompt)

    app = AICLIApp(api_url=args.api_url, api_key=args.api_key)
    app.run()


if __name__ == "__main__":
    main()
