"""输出文本清洗 —— 思考侧与输出侧共用的纯函数。

为什么放在 infra
    `clean_response` 原本是 `OutputSystem` 的静态方法，但它是**纯文本变换**：
    只依赖入参字符串，不读任何实例状态、不碰配置、不触发 IO。思考循环需要
    在产出最终回复前做同一套清洗（去记事本标记、压缩空行），于是
    `modules/thinking/adapters.py` 直接 import 了 output_system。

    这条 import 让 think 依赖 output —— 但 think 的扫描根是 modules/thinking/core/，
    顶层 adapters.py 落在 E302 的扫描范围之外，属于检测盲区，长期无人发现。

    纯函数下沉 infra 后：两侧都只依赖 infra，模块边消失，且语义没有搬家
    （清洗规则仍是同一份实现，不会出现两处规则漂移）。

约定
    改清洗规则只改这里。OutputSystem.clean_response 保留为转发壳，避免破坏
    既有调用方与测试；新增调用方请直接用本模块。
"""
from __future__ import annotations

import re


def clean_response(text: str) -> str:
    """清洗模型输出：去除记事本标记、格式化冗余等。

    规则（顺序有意义）：
      1. 截掉「【更新记事本】」及其之后的全部内容（该段落是内部记账，不外发）
      2. 丢弃记事本块内的续行（"- 任务状态:" / "- 当前进度:" 起始的列表块）
      3. 把 3 个以上连续换行压成 2 个
      4. 去掉首尾空白
    """
    if not text:
        return text
    text = re.sub(r'【更新记事本】.*', '', text, flags=re.DOTALL)
    lines = text.split('\n')
    in_notebook = False
    result = []
    for line in lines:
        s = line.strip()
        if s.startswith('- 任务状态:') or s.startswith('- 当前进度:'):
            in_notebook = True
            continue
        if in_notebook and s.startswith('- '):
            continue
        if in_notebook and s and not s.startswith('- '):
            in_notebook = False
        result.append(line)
    text = '\n'.join(result)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()
