"""
一键启动所有模块的脚本
"""
import signal
import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_memory_scheduler = None


def _graceful_shutdown(signum, frame):
    """优雅退出：停止记忆调度器"""
    global _memory_scheduler
    print("\n正在关闭...")
    if _memory_scheduler:
        _memory_scheduler.stop()
        print("✓ 记忆调度器已停止")
    sys.exit(0)


def main():
    """启动服务"""
    global _memory_scheduler

    # 注册信号处理器
    signal.signal(signal.SIGINT, _graceful_shutdown)
    signal.signal(signal.SIGTERM, _graceful_shutdown)

    print("Starting Humanoid AGI server...")

    # 后台记忆由 EventReducer 在每次会话结束后自动处理，无需独立调度器
    print("记忆系统: 事件驱动 (EventReducer + EventStore)")

    import uvicorn
    workers = int(os.environ.get("MAX_WORKERS", "1"))
    uvicorn.run(
        "api.main:app",
        host="0.0.0.0",
        port=int(os.environ.get("SERVER_PORT", "8080")),
        workers=workers,
        reload=False,
        log_level="info"
    )


if __name__ == "__main__":
    main()
