"""context_budget 测试（此前 0% 覆盖）：预算分配/估计/推荐"""
from infra.tool_manager.context_budget import ContextBudget, ContextBudgetManager


def _mgr():
    return ContextBudgetManager()


# ── ContextBudget.allocate ──────────────────────────────────────────────────

def test_allocate_default():
    b = ContextBudget()
    a = b.allocate()
    assert a["system_prompt"] == 400  # 8000*5%
    assert a["conversation_history"] == 4000  # 8000*50%
    assert a["memory_retrieval"] == 800  # 8000*10%
    assert a["tool_descriptions"] > 0


def test_allocate_few_tools():
    b = ContextBudget()
    a = b.allocate(actual_tool_count=2)
    # 工具少 → 最小工具预算
    assert a["tool_descriptions"] == int(8000 * 15 / 100)  # min 15%


def test_allocate_many_tools():
    b = ContextBudget()
    a = b.allocate(actual_tool_count=20)
    assert a["tool_descriptions"] == int(8000 * 40 / 100)  # max 40%


def test_allocate_medium_tools():
    b = ContextBudget()
    a = b.allocate(actual_tool_count=8)
    assert a["tool_descriptions"] == int(8000 * 27 / 100)  # (15+40)//2=27%


# ── estimate_tokens ─────────────────────────────────────────────────────────

def test_estimate_tokens_empty():
    assert _mgr().estimate_tokens("") == 0


def test_estimate_tokens_chinese():
    m = _mgr()
    # 4 个中文字 → (4+0)//3 = 1
    assert m.estimate_tokens("你好世界") == 1
    # 中文按 3 字符/token 估算
    assert m.estimate_tokens("中华人民共和国" * 10) > 10


# ── estimate_tool_descriptions_tokens ───────────────────────────────────────

def test_estimate_tool_desc():
    m = _mgr()
    assert m.estimate_tool_descriptions_tokens(2) == 200
    assert m.estimate_tool_descriptions_tokens(8) == 800
    assert m.estimate_tool_descriptions_tokens(15) == 2000
    assert m.estimate_tool_descriptions_tokens(30) == 4000


# ── should_simplify_tool_descriptions ───────────────────────────────────────

def test_should_simplify():
    m = _mgr()
    # 工具多（2000 tokens 估计）超出预算（1600）→ 简化
    assert m.should_simplify_tool_descriptions(15, {"tool_descriptions": 1600}) is True
    # 工具少（200 tokens）在预算内 → 不简化
    assert m.should_simplify_tool_descriptions(2, {"tool_descriptions": 1600}) is False


# ── 推荐方法 ────────────────────────────────────────────────────────────────

def test_recommend_memory_search_count():
    m = _mgr()
    assert m.recommend_memory_search_count({"memory_retrieval": 800}) == 3  # 800//250
    assert m.recommend_memory_search_count({"memory_retrieval": 100}) == 1  # 至少 1


def test_recommend_conversation_turns():
    m = _mgr()
    assert m.recommend_conversation_turns(400, {"conversation_history": 4000}) == 10
    assert m.recommend_conversation_turns(0, {"conversation_history": 3200}) == 5  # 默认
    assert m.recommend_conversation_turns(8000, {"conversation_history": 4000}) == 1  # 至少 1


# ── create_budget_for_role ──────────────────────────────────────────────────

def test_create_budget_customer():
    m = _mgr()
    a = m.create_budget_for_role("customer", tool_count=3)
    # 工具少角色：对话占 70%
    assert a["conversation_history"] == int(8000 * 70 / 100)


def test_create_budget_large():
    m = _mgr()
    a = m.create_budget_for_role("large", tool_count=20)
    assert a["conversation_history"] == int(8000 * 40 / 100)


def test_create_budget_expert():
    m = _mgr()
    a = m.create_budget_for_role("code_writer", tool_count=8)
    assert a["conversation_history"] == int(8000 * 50 / 100)  # 专家保持默认


def test_get_singleton():
    from infra.tool_manager.context_budget import get_context_budget_manager
    assert get_context_budget_manager() is get_context_budget_manager()
