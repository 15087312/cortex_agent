"""知识库切分器单测 — 标题路径、代码围栏、AST 边界、重叠与偏移一致性"""
from modules.knowledge.chunker import (
    chunk_document, chunk_embedding_text, count_tokens, derive_title, normalize_text,
)

MD_DOC = """# 项目架构

项目说明正文。

## 1. 概览

这里讲概览。

```python
# 这不是标题
x = 1
```

## 2. 鉴权流程

双令牌：access 短期 + refresh 长期。refresh 落库。

### 2.1 细节

令牌过期后前端静默刷新。
"""

PY_DOC = """import os

CONST = 1


def foo(a):
    return a + 1


class Bar:
    def method(self):
        return 2
"""


def test_normalize_text_is_idempotent_and_unifies_newlines():
    raw = "  a  \r\n\r\n\r\n\r\nb\t\r\n"
    once = normalize_text(raw)
    assert "\r" not in once
    assert "\n\n\n" not in once
    assert normalize_text(once) == once


def test_markdown_heading_path_and_code_fence():
    text = normalize_text(MD_DOC)
    drafts = chunk_document(text, "a.md")
    paths = {d.heading_path for d in drafts}
    assert "项目架构 > 2. 鉴权流程" in paths
    assert any("2.1 细节" in p for p in paths)
    # 代码围栏里的 # 不是标题
    assert all("这不是标题" not in p for p in paths)
    # 偏移必须精确指向原文
    for d in drafts:
        assert text[d.char_start:d.char_end] == d.text
        assert "双令牌" not in d.text or "鉴权" in d.heading_path


def test_python_ast_sections_and_offsets():
    text = normalize_text(PY_DOC)
    drafts = chunk_document(text, "mod.py")
    paths = [d.heading_path for d in drafts]
    assert any("def foo" in p for p in paths)
    assert any("class Bar" in p for p in paths)
    foo_chunk = next(d for d in drafts if "def foo" in d.heading_path)
    assert "return a + 1" in foo_chunk.text
    for d in drafts:
        assert text[d.char_start:d.char_end] == d.text


def test_long_document_splits_with_overlap():
    sentence = "这是一个用于测试切分的长句子，它需要被完整保留而不被截断。"
    text = sentence * 60
    drafts = chunk_document(text, "long.txt", target_tokens=80, overlap_tokens=20, min_tokens=5)
    assert len(drafts) > 3
    # 每块不超过目标太多（单个句子本身不可切）
    for d in drafts:
        assert d.token_count <= 80 + count_tokens(sentence)
    # 块间必须有重叠（下一块起点早于上一块终点）
    for prev, nxt in zip(drafts, drafts[1:]):
        assert nxt.char_start < prev.char_end
    assert drafts[0].char_start == 0
    assert drafts[-1].char_end == len(text)


def test_small_tail_is_merged():
    text = "第一句话内容比较长需要占用一些 token 空间。" * 5 + "尾巴。"
    drafts = chunk_document(text, "t.txt", target_tokens=40, overlap_tokens=0, min_tokens=20)
    assert all(d.token_count >= 1 for d in drafts)
    assert drafts[-1].char_end == len(text)
    # 碎片不应单独成为一个极小尾块
    assert not (len(drafts) > 1 and drafts[-1].token_count < 20)


def test_empty_and_blank_documents_return_no_chunks():
    assert chunk_document("", "a.md") == []
    assert chunk_document("   \n\n  ", "a.md") == []


def test_derive_title_prefers_h1_then_filename():
    assert derive_title("# 鉴权设计\n正文", "x.md") == "鉴权设计"
    assert derive_title("无标题正文", "docs/auth-flow.md") == "auth-flow"


def test_embedding_text_carries_heading_context():
    out = chunk_embedding_text("项目架构", "2. 鉴权流程", "双令牌")
    assert out.startswith("项目架构 > 2. 鉴权流程")
    assert "双令牌" in out
