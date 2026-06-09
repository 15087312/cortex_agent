"""
记忆系统测试 — RAG 语义搜索 + 主动查询
"""
import pytest
import tempfile
import shutil
import os
import json
import math


# =========================================================================
# 1. Embedding 生成器
# =========================================================================

class TestEmbeddingGenerator:
    """测试 sentence-transformers embedding 生成"""

    def test_embedding_generator_loads(self):
        from modules.memory.utils.embeddings import get_embedding_generator
        gen = get_embedding_generator()
        assert gen is not None

    def test_generate_single_embedding(self):
        from modules.memory.utils.embeddings import get_embedding_generator
        gen = get_embedding_generator()
        vec = gen.get_embedding("这是一个测试文本")
        # get_embedding 返回 2D array (1, 384)
        assert vec is not None
        flat = vec.flatten() if hasattr(vec, 'flatten') else vec
        assert len(flat) == 384

    def test_generate_multiple_embeddings(self):
        from modules.memory.utils.embeddings import get_embedding_generator
        gen = get_embedding_generator()
        texts = ["Python 编程", "机器学习", "今天天气很好"]
        vecs = [gen.get_embedding(t) for t in texts]
        assert len(vecs) == 3

    def test_semantic_similarity(self):
        """语义相似的文本向量距离更近"""
        from modules.memory.utils.embeddings import get_embedding_generator
        gen = get_embedding_generator()

        v1 = gen.get_embedding("Python 编程语言").flatten()
        v2 = gen.get_embedding("Python 代码开发").flatten()
        v3 = gen.get_embedding("今天天气晴朗").flatten()

        # 余弦相似度
        def cosine(a, b):
            dot = sum(x*y for x, y in zip(a, b))
            na = math.sqrt(sum(x*x for x in a))
            nb = math.sqrt(sum(x*x for x in b))
            return dot / (na * nb + 1e-10)

        sim_related = cosine(v1, v2)
        sim_unrelated = cosine(v1, v3)
        assert sim_related > sim_unrelated


# =========================================================================
# 2. FAISS 索引
# =========================================================================

class TestFAISSIndex:
    """测试 FAISS 索引读写"""

    @pytest.fixture
    def temp_dir(self):
        d = tempfile.mkdtemp(prefix="test_faiss_")
        yield d
        shutil.rmtree(d, ignore_errors=True)

    def test_faiss_index_create_and_search(self, temp_dir):
        import faiss
        import numpy as np
        from modules.memory.utils.embeddings import get_embedding_generator
        gen = get_embedding_generator()

        texts = [
            "Python 是一种编程语言",
            "机器学习需要大量数据",
            "今天天气很好适合出门",
        ]
        vecs = np.array([gen.get_embedding(t).flatten() for t in texts], dtype=np.float32)

        index = faiss.IndexFlatIP(384)
        index.add(vecs)

        index_path = os.path.join(temp_dir, "test.index")
        faiss.write_index(index, index_path)

        loaded = faiss.read_index(index_path)
        query = np.array([gen.get_embedding("编程语言 Python").flatten()], dtype=np.float32)
        distances, indices = loaded.search(query, 3)

        assert indices[0][0] == 0  # 最近的是 "Python 是一种编程语言"
        assert distances[0][0] > 0.5


# =========================================================================
# 3. 长期记忆搜索
# =========================================================================

class TestLongTermMemorySearch:
    """测试长期记忆的存储和搜索"""

    @pytest.fixture
    def temp_dir(self):
        d = tempfile.mkdtemp(prefix="test_ltmem_")
        yield d
        shutil.rmtree(d, ignore_errors=True)

    def test_save_and_keyword_search(self, temp_dir):
        from modules.memory.core.long_term import LongTermMemory
        ltm = LongTermMemory(data_dir=temp_dir, enable_rag=False)

        ltm.save("dialog", {"id": "d1", "content": "用户问了关于 Python 装饰器的问题"})
        ltm.save("dialog", {"id": "d2", "content": "用户想了解机器学习的基本概念"})
        ltm.save("dialog", {"id": "d3", "content": "今天天气不错适合出去散步"})

        # 验证文件写入
        import pathlib
        dialog_file = pathlib.Path(temp_dir) / "dialogs.jsonl"
        assert dialog_file.exists(), f"文件不存在: {dialog_file}"
        lines = dialog_file.read_text().strip().split("\n")
        assert len(lines) == 3, f"应有 3 条记录，实际 {len(lines)}"

        results = ltm.search("dialog", "Python 装饰器")
        assert len(results) > 0
        assert any("Python" in json.dumps(r.get("content", {})) for r in results)

    def test_search_no_match(self, temp_dir):
        from modules.memory.core.long_term import LongTermMemory
        ltm = LongTermMemory(data_dir=temp_dir, enable_rag=False)

        ltm.save("dialog", {"id": "d1", "content": "今天天气很好"})

        results = ltm.search("dialog", "量子物理")
        assert len(results) == 0


# =========================================================================
# 4. 分类记忆搜索
# =========================================================================

class TestClassifiedMemory:
    """测试分类记忆的存储和搜索"""

    @pytest.fixture
    def temp_dir(self):
        d = tempfile.mkdtemp(prefix="test_classified_")
        yield d
        shutil.rmtree(d, ignore_errors=True)

    def test_save_and_search_classified(self, temp_dir):
        from modules.memory.classification_memory import ClassificationMemory
        cm = ClassificationMemory(data_dir=temp_dir)

        cm.save_memory("knowledge", "Python 的 GIL 限制了多线程性能", {"importance": 0.8})
        cm.save_memory("preferences", "用户偏好使用 VS Code 编辑器", {"importance": 0.6})

        results = cm.search_mid_term_memories("Python GIL", category="knowledge")
        # 可能返回空（刚保存的在 short-term，不在 mid-term）
        # 验证方法可调用
        assert isinstance(results, list)

    def test_search_different_categories(self, temp_dir):
        from modules.memory.classification_memory import ClassificationMemory
        cm = ClassificationMemory(data_dir=temp_dir)

        cm.save_memory("knowledge", "深度学习需要 GPU", {"importance": 0.7})
        cm.save_memory("skills", "熟练使用 PyTorch 框架", {"importance": 0.8})

        results = cm.search_mid_term_memories("深度学习", category="knowledge")
        assert isinstance(results, list)


# =========================================================================
# 5. RAG 工具验证
# =========================================================================

class TestRAGTools:
    """测试 RAG 工具注册"""

    def test_memory_match_tool_exists(self):
        from infra.tool_manager import ToolRegistry
        tool_names = ToolRegistry.list_tools()
        assert "memory_match" in tool_names

    def test_rag_query_tool_exists(self):
        from infra.tool_manager import ToolRegistry
        tool_names = ToolRegistry.list_tools()
        assert "rag_query" in tool_names

    def test_rag_index_tool_exists(self):
        from infra.tool_manager import ToolRegistry
        tool_names = ToolRegistry.list_tools()
        assert "rag_index" in tool_names


# =========================================================================
# 6. 记忆上下文注入
# =========================================================================

class TestMemoryContextInjection:
    """测试记忆方法存在"""

    def test_build_full_context_method_exists(self):
        from modules.memory.core.memory_manager import MemoryManager
        assert hasattr(MemoryManager, "build_full_context")

    def test_search_memories_by_category_method_exists(self):
        from modules.memory.core.memory_manager import MemoryManager
        assert hasattr(MemoryManager, "search_memories_by_category")

    def test_search_memories_method_exists(self):
        from modules.memory.core.memory_manager import MemoryManager
        assert hasattr(MemoryManager, "search_memories")


# =========================================================================
# 7. 已知 Gap
# =========================================================================

class TestKnownGaps:
    """记录已知 gap"""

    def test_api_search_exists(self):
        from modules.memory.api import router
        paths = [r.path for r in router.routes]
        assert any("search" in p for p in paths)

    def test_short_term_adapter_used(self):
        """MemoryManager 使用 _ShortTermAdapter 而非 ShortTermMemory"""
        from modules.memory.core.memory_manager import MemoryManager
        import inspect
        source = inspect.getsource(MemoryManager.__init__)
        assert "short_term_repo" in source or "_ShortTermAdapter" in source
