"""泄漏测试 J：文件句柄泄漏（打开的文件对象未关闭累积）

模块域: utils/output —— 模拟 IO 资源未释放：open() 对象被引用不 close
预期: 检测系统报告 ⚠ 疑似内存泄漏（且 fd 持续增长）
"""
import io

import pytest

pytestmark = pytest.mark.leak

_OPEN_FILES: list = []
_FILE_CONTENTS: list = []


@pytest.mark.parametrize("i", range(60))
def test_file_handles(i):
    # 模拟读取后未释放的文件内容 + 未关闭的文件对象
    content = b"x" * (1024 * 1024)  # 1MB 文件内容
    f = io.BytesIO(content)
    f.read()
    _OPEN_FILES.append(f)  # 文件对象未关闭
    _FILE_CONTENTS.append(content)  # 内容被引用，无法释放
