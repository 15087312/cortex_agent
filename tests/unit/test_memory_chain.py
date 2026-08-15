"""记忆深度召回/因果树测试（此前 11-12% 覆盖）——纯逻辑全覆盖"""
import math
from datetime import datetime, timezone

import pytest

from modules.memory.depth_recall import (
    classify_intent,
    should_trigger_deep_recall,
    DeepRecallResult,
    DepthRecallScheduler,
)
from modules.memory.causal_tree import CausalChain, EvidenceItem, EvidenceTree
from modules.memory.causal_graph import CausalNode


# ── classify_intent ─────────────────────────────────────────────────────────

def test_classify_intent_various():
    assert classify_intent("为什么项目延期") == "trace"
    assert classify_intent("后果是什么") == "predict"
    assert classify_intent("分析这个问题的根因") == "analyze"
    assert classify_intent("如何优化性能") == "optimize"
    assert classify_intent("如果当时重做会怎样") == "counterfactual"
    assert classify_intent("普通聊天") == "shallow"


def test_classify_intent_cached():
    first = classify_intent("为什么缓存")
    second = classify_intent("为什么缓存")
    assert first == second == "trace"


# ── should_trigger_deep_recall ──────────────────────────────────────────────

def test_trigger_logic_words():
    trigger, reason = should_trigger_deep_recall("为什么项目延期")
    assert trigger is True
    assert reason == "query_contains_logic_words"


def test_trigger_low_confidence():
    trigger, reason = should_trigger_deep_recall("普通聊天", shallow_confidence=0.1)
    assert trigger is True
    assert reason == "shallow_recall_low_confidence"


def test_trigger_decision_task():
    trigger, reason = should_trigger_deep_recall("看看", task_type="decision")
    assert trigger is True
    assert reason == "decision_task"


def test_no_trigger_shallow():
    trigger, reason = should_trigger_deep_recall("今天天气不错", shallow_confidence=0.8)
    assert trigger is False
    assert reason == ""


# ── DeepRecallResult.format ─────────────────────────────────────────────────

def test_format_fallback_returns_empty():
    r = DeepRecallResult(fallback=True)
    assert r.format() == ""
    r2 = DeepRecallResult(success=False)
    assert r2.format() == ""


def test_format_with_conclusion_and_factors():
    r = DeepRecallResult(
        success=True,
        causal_conclusion="需求变更导致延期",
        shared_factors=["需求", "测试"],
    )
    out = r.format()
    assert "需求变更导致延期" in out
    assert "需求、测试" in out


# ── _time_decay ─────────────────────────────────────────────────────────────

def test_time_decay_empty():
    assert DepthRecallScheduler._time_decay("") == 0.5


def test_time_decay_recent():
    now = datetime.now(timezone.utc).isoformat()
    assert DepthRecallScheduler._time_decay(now) == pytest.approx(1.0, abs=0.05)


def test_time_decay_old():
    old = datetime.now(timezone.utc).replace(year=2000).isoformat()
    assert 0.0 < DepthRecallScheduler._time_decay(old) < 0.5


def test_time_decay_invalid():
    assert DepthRecallScheduler._time_decay("not-a-date") == 0.5


# ── _build_conclusion ───────────────────────────────────────────────────────

def test_build_conclusion_empty():
    assert DepthRecallScheduler._build_conclusion([], []) == ""


def test_build_conclusion_forward():
    chain = CausalChain(
        nodes=[CausalNode(label="需求变更"), CausalNode(label="项目延期")],
        direction="forward", confidence=0.9,
    )
    out = DepthRecallScheduler._build_conclusion([chain], ["需求"])
    assert "需求变更 → 项目延期" in out
    assert "共享因子: 需求" in out


def test_build_conclusion_backward():
    # 补全锚点后链路节点恒为"因→果"顺序，统一用 → 拼接
    chain = CausalChain(
        nodes=[CausalNode(label="项目延期"), CausalNode(label="需求变更")],
        direction="backward", confidence=0.8,
    )
    out = DepthRecallScheduler._build_conclusion([chain], [])
    assert "项目延期 → 需求变更" in out


# ── CausalChain.summary ─────────────────────────────────────────────────────

def test_chain_summary():
    c = CausalChain(nodes=[CausalNode(label="A"), CausalNode(label="B"), CausalNode(label="C")])
    assert c.summary() == "A → B → C"


def test_chain_summary_limits():
    c = CausalChain(nodes=[CausalNode(label=f"N{i}") for i in range(8)])
    assert c.summary(max_nodes=3) == "N0 → N1 → N2"


# ── EvidenceTree.format ─────────────────────────────────────────────────────

def test_evidence_tree_format():
    node = CausalNode(label="项目延期")
    ev = EvidenceItem(event_id="e1", fact="需求变更导致延期一个月", importance=0.8)
    tree = EvidenceTree(
        node=node,
        evidence=[ev],
        parent_chain=[CausalNode(label="需求变更")],
        child_chains=[[CausalNode(label="用户投诉")]],
        confidence=0.9,
    )
    out = tree.format()
    assert "【项目延期】" in out
    assert "原因链: 需求变更" in out
    assert "证据 (1 条)" in out
    assert "需求变更导致延期一个月" in out
    assert "后果" in out
    assert "用户投诉" in out


def test_evidence_tree_format_minimal():
    tree = EvidenceTree(node=CausalNode(label="孤点"), confidence=0.5)
    out = tree.format()
    assert "【孤点】" in out
    assert "原因链" not in out
