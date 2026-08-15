#!/usr/bin/env python3
"""
多方检索效果评测 — 浅层记忆检索(EventRetrieval / event_query) vs 深度因果回忆(DepthRecall / deep_recall)

评测维度:
  1. 精确命中查询 — 直接检索到目标事件
  2. 语义相近查询 — 换种说法仍能召回
  3. 因果溯源 (trace)  — "为什么 X" 深度回忆能否给出因果链
  4. 后果预测 (predict) — "X 会有什么后果"
  5. 规律归纳 (generalize)
  6. 噪声查询 — 无关问题应返回空（精度）
  7. 时间维度 — 时间范围过滤 + 时间衰减

隔离性:
  - 事件库/FAISS/因果图全部落在临时目录，绝不触碰 data/ 生产数据
  - 嵌入默认用真实模型(本地缓存,离线加载)；加载失败自动降级到确定性嵌入器

运行: python scripts/eval_retrieval_effect.py
"""
import asyncio
import math
import os
import shutil
import sys
import tempfile
import time
import traceback
from datetime import datetime, timezone

from config.settings import settings

# ════════════════════════════════════════════════════════════════════
# 1. 隔离环境 — 所有存储路径指向临时目录，生产数据只读不碰
# ════════════════════════════════════════════════════════════════════
_TMP = tempfile.mkdtemp(prefix="eval_retrieval_")
print(f"[setup] 隔离临时目录: {_TMP}")


def _patch_setting(name, value):
    object.__setattr__(settings, name, value)


_patch_setting("MEMORY_DB_PATH", os.path.join(_TMP, "memory.db"))
_patch_setting("MEMORY_FAISS_INDEX", os.path.join(_TMP, "events_faiss.index"))
_patch_setting("MEMORY_ID_MAP", os.path.join(_TMP, "events_id_map.json"))
_patch_setting("CAUSAL_DB_PATH", os.path.join(_TMP, "causal.db"))
_patch_setting("EMBEDDING_LOCAL_FILES_ONLY", True)   # 只走本地缓存，禁止联网
_patch_setting("EMBEDDING_BACKGROUND_WORKER", False)  # 禁用后台向量化线程

# 防止真实模型加载时重建"生产" FAISS 索引
from modules.memory.embedding import EmbeddingEngine

try:
    from modules.memory.event_store import EventStore
    from modules.memory.causal_graph import CausalGraph, CausalNode, CausalEdge
    from modules.memory.event_retrieval import EventRetrieval
    from modules.memory.depth_recall import DepthRecallScheduler, should_trigger_deep_recall
except Exception:  # pragma: no cover
    traceback.print_exc()
    sys.exit(1)


# ════════════════════════════════════════════════════════════════════
# 2. 嵌入引擎 — 真实模型优先，失败降级到确定性嵌入器
# ════════════════════════════════════════════════════════════════════

class _FallbackEmbedder:
    """确定性中文 bigram / 英文词 嵌入器（真实模型不可用时兜底，结果可复现）"""

    def __init__(self, dim=256):
        self.dim = dim
        self._loaded = True
        self._attempted = True

    def _load_model(self):
        return True

    def embed(self, text):
        import hashlib
        import re
        tokens = re.findall(r"[\u4e00-\u9fff]{2}|[a-zA-Z0-9]+", text or "")
        if not tokens:
            return [0.0] * self.dim
        vec = [0.0] * self.dim
        for t in set(tokens):
            idx = int(hashlib.md5(t.encode("utf-8")).hexdigest(), 16) % self.dim
            vec[idx] += 1.0
        norm = math.sqrt(sum(x * x for x in vec))
        return [x / norm for x in vec]

    def embed_batch(self, texts):
        return [self.embed(t) for t in texts]


def _select_embedder() -> tuple:
    """返回 (embedder, 描述)。优先真实模型。"""
    real = EmbeddingEngine.get_instance()
    try:
        v = real.embed("预热：项目延期")
        if v:
            return real, f"真实模型 {settings.EMBEDDING_MODEL} (dim={real.dim})"
    except Exception:
        pass
    fb = _FallbackEmbedder()
    EmbeddingEngine.get_instance = classmethod(lambda cls: fb)  # 兜底：全链路指向确定性嵌入器
    return fb, "确定性降级嵌入器 (FallbackEmbedder)"


_EMBEDDER, _EMBEDDER_DESC = _select_embedder()
print(f"[setup] 嵌入引擎: {_EMBEDDER_DESC}")


# ════════════════════════════════════════════════════════════════════
# 3. 种子数据 — 两个真实感场景 + 干扰项
# ════════════════════════════════════════════════════════════════════

_NODES = [
    # 场景 A: 项目延期
    CausalNode(id="n_demand",  label="需求频繁变更", node_type="cause",   keywords=["需求", "变更"],      importance=0.8, confidence=0.80),
    CausalNode(id="n_staff",   label="人手不足",     node_type="cause",   keywords=["人手", "人力", "离职"], importance=0.85, confidence=0.85),
    CausalNode(id="n_review",  label="评审返工多",   node_type="cause",   keywords=["评审", "返工"],      importance=0.6, confidence=0.60),
    CausalNode(id="n_delay",   label="项目延期",     node_type="effect",  keywords=["项目", "延期"],      importance=0.9, confidence=0.90),
    CausalNode(id="n_quality", label="质量问题",     node_type="effect",  keywords=["质量", "故障", "bug"], importance=0.7, confidence=0.70),
    # 场景 B: 服务器宕机
    CausalNode(id="n_deploy",   label="发布事故",     node_type="cause",  keywords=["发布", "上线"],      importance=0.8, confidence=0.80),
    CausalNode(id="n_server",   label="服务器宕机",   node_type="effect", keywords=["服务器", "宕机"],    importance=0.9, confidence=0.90),
    CausalNode(id="n_complaint", label="用户投诉激增", node_type="effect", keywords=["投诉"],             importance=0.7, confidence=0.70),
    # 场景 C: 营销活动 → 销量增长 → 库存告急（时间错开的因果链）
    CausalNode(id="n_campaign",  label="营销活动",   node_type="cause",  keywords=["营销", "促销"],      importance=0.7, confidence=0.75),
    CausalNode(id="n_sales",     label="销量增长",   node_type="effect", keywords=["销量", "增长"],      importance=0.8, confidence=0.80),
    CausalNode(id="n_inventory", label="库存告急",   node_type="effect", keywords=["库存", "补货"],      importance=0.7, confidence=0.70),
    # 干扰节点（孤立，不连入任何因果链）
    CausalNode(id="n_patch", label="例行补丁发布", node_type="cause", keywords=["补丁"],                importance=0.4, confidence=0.40),
    CausalNode(id="n_db",    label="数据库慢查询", node_type="cause", keywords=["数据库", "慢查询"],     importance=0.5, confidence=0.50),
    # 场景 F: 代码质量回归（新功能 → 功能回归 → 紧急修复）
    CausalNode(id="n_feature",   label="新功能上线", node_type="cause",  keywords=["功能", "上线"],    importance=0.7, confidence=0.75),
    CausalNode(id="n_regression", label="功能回归",  node_type="effect", keywords=["回归", "报错"],     importance=0.8, confidence=0.80),
    CausalNode(id="n_hotfix",    label="紧急修复",   node_type="effect", keywords=["热修复", "紧急"],   importance=0.7, confidence=0.70),
]

_EDGES = [
    CausalEdge(id="e1", from_id="n_demand",  to_id="n_delay",   relation="causes", confidence=0.75),
    CausalEdge(id="e2", from_id="n_staff",   to_id="n_delay",   relation="causes", confidence=0.85),
    CausalEdge(id="e3", from_id="n_review",  to_id="n_delay",   relation="causes", confidence=0.60),
    CausalEdge(id="e4", from_id="n_delay",   to_id="n_quality", relation="causes", confidence=0.65),
    CausalEdge(id="e5", from_id="n_deploy",  to_id="n_server",  relation="causes", confidence=0.80),
    CausalEdge(id="e6", from_id="n_server",  to_id="n_complaint", relation="causes", confidence=0.70),
    CausalEdge(id="e7", from_id="n_campaign", to_id="n_sales",  relation="causes", confidence=0.75),
    CausalEdge(id="e8", from_id="n_sales",    to_id="n_inventory", relation="causes", confidence=0.70),
    CausalEdge(id="e9", from_id="n_feature",  to_id="n_regression", relation="causes", confidence=0.70),
    CausalEdge(id="e10", from_id="n_regression", to_id="n_hotfix", relation="causes", confidence=0.65),
]

_EVENTS = [
    # 场景 A
    dict(id="ev_demand_change", fact="评审会上客户临时新增三个需求，原排期全部打乱",
         keywords=["需求", "变更", "排期"], importance=0.75, time="2026-08-02T10:00:00", causal=["n_demand"], type="fact"),
    dict(id="ev_staff_leave", fact="前端组两名主力先后离职，人力只够支撑一半工作量",
         keywords=["离职", "人力", "人手"], importance=0.80, time="2026-07-20T09:00:00", causal=["n_staff"], type="fact"),
    dict(id="ev_review_rework", fact="每周评审都要返工两轮，迭代速度被拖慢",
         keywords=["评审", "返工"], importance=0.60, time="2026-07-25T14:00:00", causal=["n_review"], type="fact"),
    dict(id="ev_delay_launch", fact="项目延期两周，6月中旬才正式上线",
         keywords=["项目", "延期", "上线"], importance=0.90, time="2026-06-15T10:00:00", causal=["n_delay"], type="strategy"),
    dict(id="ev_quality_drop", fact="上线后线上故障明显增多，用户反馈质量问题",
         keywords=["质量", "故障"], importance=0.65, time="2026-06-20T18:00:00", causal=["n_quality"], type="fact"),
    # 场景 B
    dict(id="ev_deploy_crash", fact="凌晨发布新版本后服务崩溃，用户无法访问",
         keywords=["发布", "崩溃", "版本"], importance=0.90, time="2026-07-28T01:00:00", causal=["n_deploy"], type="fact"),
    dict(id="ev_server_down", fact="故障持续三小时，订单系统完全不可用",
         keywords=["服务器", "宕机", "故障"], importance=0.85, time="2026-07-28T02:00:00", causal=["n_server"], type="fact"),
    dict(id="ev_complaint", fact="事故后客服接到大量投诉电话",
         keywords=["投诉", "客服"], importance=0.60, time="2026-07-28T10:00:00", causal=["n_complaint"], type="fact"),
    # 干扰项（与因果场景无关）
    dict(id="ev_standup", fact="团队开始每日站会同步进度，协作效率提升",
         keywords=["站会", "协作"], importance=0.50, time="2026-07-10T09:00:00", causal=[], type="strategy"),
    dict(id="ev_outing", fact="公司组织年度团建，大家玩得很开心",
         keywords=["团建", "旅行"], importance=0.30, time="2026-07-05T09:00:00", causal=[], type="emotion"),
    # 场景 C: 时间错开的因果链（营销→销量→库存）
    dict(id="ev_campaign", fact="春季大促全渠道上线，预售爆满",
         keywords=["营销", "促销", "预售"], importance=0.70, time="2026-05-10T10:00:00", causal=["n_campaign"], type="strategy"),
    dict(id="ev_sales", fact="Q2销量环比上涨40%，超出预期",
         keywords=["销量", "增长"], importance=0.80, time="2026-07-05T10:00:00", causal=["n_sales"], type="fact"),
    dict(id="ev_inventory", fact="热销款库存告急，采购需加急补货",
         keywords=["库存", "补货"], importance=0.65, time="2026-08-01T10:00:00", causal=["n_inventory"], type="fact"),
    # 场景 D: 与 ev_server_down 同一天、但无因果的事件（时间相同无因果）
    dict(id="ev_patch_day", fact="例行补丁发布，运行无异常",
         keywords=["补丁", "发布"], importance=0.40, time="2026-07-28T10:00:00", causal=["n_patch"], type="fact"),
    dict(id="ev_daily_op", fact="每日运维晨会正常召开，未发现异常",
         keywords=["晨会", "运维"], importance=0.30, time="2026-07-28T09:30:00", causal=[], type="fact"),
    # 场景 E: 时间相隔很远且无因果的事件
    dict(id="ev_old_db", fact="年初数据库慢查询优化，DBA花了一周",
         keywords=["数据库", "慢查询"], importance=0.50, time="2026-02-10T10:00:00", causal=["n_db"], type="strategy"),
    # 场景 F: 代码质量回归（时间相近但有因果）
    dict(id="ev_feature_launch", fact="新功能v2.1正式上线，覆盖全部用户",
         keywords=["功能", "上线"], importance=0.70, time="2026-07-30T10:00:00", causal=["n_feature"], type="fact"),
    dict(id="ev_regression", fact="上线三天后老功能出现回归，接口批量报错",
         keywords=["回归", "报错"], importance=0.80, time="2026-08-02T10:00:00", causal=["n_regression"], type="fact"),
    dict(id="ev_hotfix", fact="紧急发布热修复版本，问题基本平息",
         keywords=["热修复", "紧急"], importance=0.70, time="2026-08-05T10:00:00", causal=["n_hotfix"], type="strategy"),
    # 更多时间/语义干扰项
    dict(id="ev_old_fail", fact="三个月前支付接口曾出现超时，后自动恢复",
         keywords=["支付", "超时"], importance=0.45, time="2026-05-20T10:00:00", causal=[], type="fact"),
    dict(id="ev_docs", fact="接口文档本周例行更新",
         keywords=["文档"], importance=0.30, time="2026-08-08T10:00:00", causal=[], type="fact"),
    dict(id="ev_budget", fact="Q3预算审批通过，采购额度放宽",
         keywords=["预算", "审批"], importance=0.40, time="2026-08-03T10:00:00", causal=[], type="fact"),
]

_QUERIES = [
    dict(q="项目为什么延期", intent="trace", noise=False, exp_shallow={"ev_delay_launch", "ev_demand_change", "ev_staff_leave", "ev_review_rework"}, exp_deep={"ev_demand_change", "ev_staff_leave", "ev_review_rework", "ev_delay_launch"}),
    dict(q="人手不足导致的项目延期", intent="trace", noise=False, exp_shallow={"ev_staff_leave", "ev_delay_launch"}, exp_deep={"ev_staff_leave", "ev_delay_launch"}),
    dict(q="项目延期会带来什么后果", intent="predict", noise=False, exp_shallow={"ev_quality_drop", "ev_delay_launch"}, exp_deep={"ev_quality_drop"}),
    dict(q="项目延期的共同规律", intent="generalize", noise=False, exp_shallow={"ev_demand_change", "ev_staff_leave", "ev_review_rework"}, exp_deep=set()),
    dict(q="服务器为什么宕机", intent="trace", noise=False, exp_shallow={"ev_deploy_crash", "ev_server_down"}, exp_deep={"ev_deploy_crash", "ev_server_down"}),
    dict(q="销量为什么大涨", intent="trace", noise=False, exp_shallow={"ev_campaign", "ev_sales"}, exp_deep={"ev_campaign", "ev_sales"}),
    dict(q="新功能上线后为什么出现回归", intent="trace", noise=False, exp_shallow={"ev_feature_launch", "ev_regression"}, exp_deep={"ev_feature_launch", "ev_regression"}),
    dict(q="今天午饭吃什么", intent="shallow", noise=True, exp_shallow=set(), exp_deep=set()),
    dict(q="今天天气怎么样", intent="shallow", noise=True, exp_shallow=set(), exp_deep=set()),
]


# ════════════════════════════════════════════════════════════════════
# 4. 构建隔离环境
# ════════════════════════════════════════════════════════════════════

def build_env():
    from modules.memory.event_store import MemoryEvent

    store = EventStore(
        db_path=os.path.join(_TMP, "memory.db"),
        faiss_index_path=os.path.join(_TMP, "events_faiss.index"),
        id_map_path=os.path.join(_TMP, "events_id_map.json"),
    )
    store.clear_all()

    graph = CausalGraph(db_path=os.path.join(_TMP, "causal.db"))
    graph.clear_all()
    CausalGraph._instance = graph  # 让 depth_recall / _causal_search 内部的 get_instance 指向隔离图

    for n in _NODES:
        graph.save_node(n)
    for e in _EDGES:
        graph.save_edge(e)

    for spec in _EVENTS:
        ev = MemoryEvent(
            id=spec["id"], fact=spec["fact"], keywords=spec["keywords"],
            importance=spec["importance"], time=spec["time"],
            type=spec["type"], causal_node_ids=spec["causal"],
            last_accessed=spec["time"], owner_id="shared",
        )
        store.save_event(ev)

    retrieval = EventRetrieval()
    retrieval._store = store
    retrieval._embedder = _EMBEDDER

    scheduler = DepthRecallScheduler(graph=graph, store=store, retrieval=retrieval)
    return store, graph, retrieval, scheduler


# ════════════════════════════════════════════════════════════════════
# 5. 评测工具
# ════════════════════════════════════════════════════════════════════

def raw_max_score():
    from modules.memory.event_retrieval import SCORE_WEIGHTS
    return sum(SCORE_WEIGHTS.values()) * 1.15


async def shallow_with_scores(retrieval, query, max_results=5, **kwargs):
    """浅层检索完整管线 + 归一化分数（等价于 event_query 的检索路径）"""
    qv = retrieval._get_embedder().embed(query)
    if not qv:
        return []
    vec = await retrieval._vector_search(qv, top_k=max_results * 3)
    causal_events = retrieval._causal_search(query)
    causal_results = retrieval._compute_similarities(qv, causal_events)

    merged = {}
    for ev, s in list(vec) + causal_results:
        if ev.id not in merged or s > merged[ev.id][1]:
            merged[ev.id] = (ev, s)

    scored = retrieval._calculate_all_scores(list(merged.values()), datetime.now(timezone.utc))
    ranked = retrieval._rank_and_filter(scored, threshold=0.06, max_results=max_results)
    score_map = {ev.id: s / raw_max_score() for ev, s in scored}
    return [(ev, score_map[ev.id]) for ev in ranked]


def _fmt_chain(chain):
    labels = [n.label for n in chain.nodes]
    return f"{' → '.join(labels)} ({chain.confidence:.0%})"


def run_query(idx, q, store, retrieval, scheduler):
    query = q["q"]
    print(f"\n{'─' * 62}")
    print(f"[查询 {idx}/{len(_QUERIES)}] {query}  (期望意图: {q['intent']})")
    print(f"{'─' * 62}")

    trigger, reason = should_trigger_deep_recall(query)
    print(f"深度触发判定: should_trigger_deep_recall → {trigger}  (原因: {reason or '无'})")

    # ── 浅层记忆检索 ──
    t0 = time.time()
    shallow = asyncio.run(shallow_with_scores(retrieval, query, max_results=5))
    dt_shallow = time.time() - t0
    print(f"\n── 浅层记忆检索 (top5, 耗时 {dt_shallow:.2f}s) ──")
    if not shallow:
        print("  (无结果 — 全部未过 0.30 语义门槛)")
    for i, (ev, score) in enumerate(shallow, 1):
        hit = "✓" if ev.id in q["exp_shallow"] else "·"
        print(f"  {i}. [{score:.2f}] {hit} {ev.id:18s} {ev.fact[:30]}  (imp {ev.importance:.0%}, {ev.type}, {ev.time[:10]})")

    shallow_ids = {ev.id for ev, _ in shallow}
    exp_s = q["exp_shallow"]
    hit_s = len(shallow_ids & exp_s)

    # ── 深度因果回忆 ──
    t0 = time.time()
    deep = asyncio.run(scheduler.deep_recall(query, max_results=5, depth_level=1))
    dt_deep = time.time() - t0
    print(f"\n── 深度因果回忆 (耗时 {dt_deep:.2f}s) ──")
    ok = deep.success and not deep.fallback
    if ok:
        print(f"  成功={ok} | 锚点: {deep.anchor.label if deep.anchor else '-'} "
              f"(置信度 {deep.confidence:.0%}) | intent={deep.intent}")
        print(f"  因果链({len(deep.causal_chains)}):")
        for c in deep.causal_chains[:5]:
            print(f"    · {_fmt_chain(c)}")
        if deep.shared_factors:
            print(f"  共享因子: {'、'.join(deep.shared_factors)}")
        print(f"  佐证事件({len(deep.supporting_events)}):")
        for ev in deep.supporting_events[:5]:
            hit = "✓" if ev.id in q["exp_deep"] else "·"
            print(f"    · {hit} {ev.id:18s} {ev.fact[:30]}  (imp {ev.importance:.0%})")
        if deep.counter_examples:
            print(f"  反例: {[e.id for e in deep.counter_examples[:3]]}")
        if deep.causal_conclusion:
            print(f"  因果结论: {deep.causal_conclusion}")
    else:
        print(f"  成功=False | 回退=True | 原因: {deep.error}")
        print(f"  (已回退到浅层检索)")

    deep_support_ids = {ev.id for ev in deep.supporting_events}
    exp_d = q["exp_deep"]
    hit_d = len(deep_support_ids & exp_d) if exp_d else 0
    if exp_d:
        label_d = f"{hit_d}/{len(exp_d)}"
    elif q["noise"]:
        label_d = "误召回%d" % len(deep_support_ids)
    else:
        label_d = "已召回%d(免判定)" % len(deep_support_ids)

    return {
        "query": query,
        "intent_expected": q["intent"],
        "trigger": trigger,
        "shallow_ids": shallow_ids,
        "deep_ids": deep_support_ids,
        "deep_ok": ok,
        "deep_fallback": not ok,
        "deep_chains": len(deep.causal_chains) if ok else 0,
        "hit_shallow": f"{hit_s}/{len(exp_s)}" if exp_s else ("精确(空)" if not shallow_ids else f"误召回{len(shallow_ids)}"),
        "hit_deep": label_d,
    }


def run_time_demo(retrieval, store):
    print(f"\n{'─' * 62}")
    print("[时间维度] 时间范围过滤 + 时间衰减")
    print(f"{'─' * 62}")

    print("── store.search_by_time('2026-07-01' 起) ──")
    for ev in store.search_by_time(start_time="2026-07-01", limit=10):
        print(f"  · {ev.id:18s} {ev.time[:16]}  {ev.fact[:30]}")

    print("── retrieve(query='项目延期', start_time='2026-07-01') → 6月事件应被过滤 ──")
    r = asyncio.run(retrieval.retrieve("项目延期", max_results=5, start_time="2026-07-01"))
    for ev in r:
        print(f"  · {ev.id:18s} {ev.time[:10]}  {ev.fact[:30]}")
    print(f"  是否含 6 月事件(ev_delay_launch/ev_quality_drop): {any(e.id in ('ev_delay_launch', 'ev_quality_drop') for e in r)}")


def run_time_hypothesis(store, retrieval):
    """验证：时间(recency) 是否会导致无关事件被召回？
    做法：把与天气无关的事件 ev_server_down 的 time 改为"现在"（recency 最大化，importance 85%），
    再查"今天天气怎么样"——如果仍不召回，说明时间只是排序权重，不是召回门槛。
    """
    print(f"\n{'─' * 62}")
    print("[时间假说验证] 无关事件改成'现在'时间后，天气查询会被时间召回吗?")
    print(f"{'─' * 62}")
    ev = store.get_event("ev_server_down")
    vec = store.get_embedding(ev.id)
    old_time = ev.time
    ev.time = datetime.now(timezone.utc).isoformat()
    ev.last_accessed = ev.time
    if vec:
        ev.embedding = vec  # 保持原向量，防止 save_event 重新嵌入造成重复向量
    store.save_event(ev)

    r = asyncio.run(retrieval.retrieve("今天天气怎么样", max_results=5))
    print(f"  ev_server_down.time: {old_time} → {ev.time[:19]} (recency=1.0)")
    if r:
        print(f"  '今天天气怎么样' 浅层召回: {[e.id for e in r]}  ← 命中(说明时间参与了召回?)")
    else:
        print(f"  '今天天气怎么样' 浅层召回: 空")
        print(f"  → 结论: 即使 recency 拉满，过不了 0.30 语义门槛仍不会被召回，时间只是排序因子")

    # 对照：同一事件在语义相关查询中，recency 提升会提高排位
    print(f"  对照 '服务器故障' 浅层排序(recency 拉满后):")
    scored = asyncio.run(shallow_with_scores(retrieval, "服务器故障", max_results=5))
    for i, (e, s) in enumerate(scored, 1):
        if e.id == "ev_server_down":
            print(f"    → ev_server_down 排位 #{i} (归一化 {s:.2f})  ← recency 拉满后靠前")


def run_time_causal_matrix(store, graph, retrieval, scheduler):
    """时间×因果 误检测矩阵
    ① 时间不同但有因果 → 应召回佐证（防漏检）
    ② 时间相同但无因果 → 不应召回佐证（防误检）
    ③ 时间相隔很远且无因果 → 不应召回佐证（防误检）
    """
    print(f"\n{'=' * 62}")
    print("  时间×因果 误检测矩阵 — 时间因素是否会误导因果判定")
    print(f"{'=' * 62}")
    print("  因果关联 = _causal_relevance(事件, 锚点因果节点集合)  [准入门槛 ≥0.35]")
    print("  时间衰减 = _time_decay(事件时间)                     [仅参与排序]")

    scenarios = [
        {
            "title": "① 时间不同但有因果 → 应召回(防漏检)",
            "query": "销量为什么大涨",
            "must_include": {"ev_campaign", "ev_sales"},
            "must_exclude": set(),
        },
        {
            "title": "② 时间相同但无因果 → 不应召回(防误检)",
            "query": "服务器为什么宕机",
            "must_include": {"ev_deploy_crash", "ev_server_down"},
            "must_exclude": {"ev_patch_day", "ev_daily_op", "ev_old_db", "ev_old_fail"},
        },
        {
            "title": "③ 时间相近但有因果 → 应召回(正向对照)",
            "query": "新功能上线后为什么出现回归",
            "must_include": {"ev_feature_launch", "ev_regression"},
            "must_exclude": set(),
        },
    ]

    for sc in scenarios:
        scheduler.invalidate_cache()
        print(f"\n{'-' * 62}")
        print(f"[{sc['title']}]  查询: {sc['query']}")
        print(f"{'-' * 62}")
        deep = asyncio.run(scheduler.deep_recall(sc["query"], max_results=8))
        if not deep.success or deep.fallback:
            print(f"  深度回忆回退: {deep.error}")
            continue
        anchor = deep.anchor
        causal_ids = {anchor.id}
        if deep.intent == "trace":
            neigh = graph.get_predecessors(anchor.id)
        elif deep.intent == "predict":
            neigh = graph.get_successors(anchor.id)
        else:
            neigh = [n for n, _, _ in graph.get_neighbors(anchor.id, hops=2)]
        for n in neigh:
            causal_ids.add(n.id)
        for c in deep.causal_chains:
            for n in c.nodes:
                causal_ids.add(n.id)

        sup_ids = {e.id for e in deep.supporting_events}
        print(f"  锚点: {anchor.label} (置信度 {deep.confidence:.0%}) | intent={deep.intent}")
        print(f"  因果节点集合: {sorted(causal_ids)}")

        print(f"  {'事件':<18}{'时间':<12}{'因果关联':<9}{'时间衰减':<9}{'预期':<11}{'佐证?':<6}判定")
        all_fine = True
        for ev in store.list_events(limit=200):
            if ev.id not in sc["must_include"] and ev.id not in sc["must_exclude"]:
                continue
            rel = scheduler._causal_relevance(ev, causal_ids)
            decay = scheduler._time_decay(ev.time)
            in_sup = ev.id in sup_ids
            if ev.id in sc["must_include"]:
                expect, ok = "应进", in_sup
            else:
                expect, ok = "不应进", not in_sup
            mark = "✓" if ok else "✗ 误检"
            if not ok:
                all_fine = False
            print(f"  {ev.id:<18}{ev.time[:10]:<12}{rel:<9.3f}{decay:<9.3f}{expect:<11}{('是' if in_sup else '否'):<6}{mark}")

        missed = sorted(e for e in sc["must_include"] if e not in sup_ids)
        leaked = sorted(e for e in sc["must_exclude"] if e in sup_ids)
        print(f"  → 漏检: {missed or '无'} | 误检: {leaked or '无'} | 结论: {'通过' if all_fine else '发现问题'}")
        if all_fine:
            print("  → 时间因素未误导因果判定：有因果的(哪怕时间错开)被召回，无因果的(哪怕时间相同)被排除")


def run_causal_edit_demo(graph, retrieval, scheduler):
    """因果图编辑工具演示：删除一条错误因果边，深度回忆随即不再输出该链路"""
    print(f"\n{'=' * 62}")
    print("  因果图编辑工具演示 (causal_graph_edit)")
    print(f"{'=' * 62}")
    print("  场景: AI 认为「评审返工多 → 项目延期」这条因果是错的，用工具删除")
    try:
        from infra.tool_manager.tools import causal_graph_edit
    except Exception as e:  # pragma: no cover
        print(f"  !! 工具导入失败: {e}")
        return

    scheduler.invalidate_cache()
    before = [c.summary() for c in asyncio.run(
        scheduler.deep_recall("项目为什么延期", max_results=5)).causal_chains]

    out = causal_graph_edit.causal_graph_edit(
        action="delete_edge", from_label="评审返工多", to_label="项目延期")

    scheduler.invalidate_cache()
    after = [c.summary() for c in asyncio.run(
        scheduler.deep_recall("项目为什么延期", max_results=5)).causal_chains]

    print(f"  删除前因果链: {before}")
    print(f"  工具返回: {out}")
    print(f"  删除后因果链: {after}")
    removed = any("评审返工多" in c for c in before) and not any("评审返工多" in c for c in after)
    print(f"  → 「评审返工多」链路已从深度回忆中消除: {'✓' if removed else '✗'}")


def main():
    store, graph, retrieval, scheduler = build_env()
    print(f"[setup] 种子数据: {store.count_events()} 事件, "
          f"{len(graph.list_nodes())} 因果节点, {len(graph.list_all_edges())} 因果边")

    results = []
    for i, q in enumerate(_QUERIES, 1):
        try:
            results.append(run_query(i, q, store, retrieval, scheduler))
        except Exception as e:  # pragma: no cover
            print(f"  !! 查询执行失败: {e}")
            traceback.print_exc()
            results.append({
                "query": q["q"], "intent_expected": q["intent"], "trigger": False,
                "shallow_ids": set(), "deep_ids": set(), "deep_ok": False,
                "deep_fallback": True, "deep_chains": 0,
                "hit_shallow": "ERR", "hit_deep": "ERR",
            })

    try:
        run_time_demo(retrieval, store)
    except Exception as e:  # pragma: no cover
        print(f"  !! 时间维度演示失败: {e}")

    try:
        run_time_hypothesis(store, retrieval)
    except Exception as e:  # pragma: no cover
        print(f"  !! 时间假说验证失败: {e}")

    try:
        run_time_causal_matrix(store, graph, retrieval, scheduler)
    except Exception as e:  # pragma: no cover
        print(f"  !! 时间×因果矩阵失败: {e}")

    try:
        run_causal_edit_demo(graph, retrieval, scheduler)
    except Exception as e:  # pragma: no cover
        print(f"  !! 因果图编辑演示失败: {e}")

    # ── 汇总表 ──
    print(f"\n{'=' * 62}")
    print("  汇总 — 浅层记忆检索 vs 深度因果回忆")
    print(f"{'=' * 62}")
    header = f"{'查询':<20} | {'触发深追':<6} | {'浅层命中':<8} | {'深度链':<5} | {'深度佐证':<8} | 深度回退"
    print(header)
    print("-" * len(header))
    for r in results:
        q = r["query"][:20]
        print(f"{q:<20} | {('是' if r['trigger'] else '否'):<6} | {r['hit_shallow']:<8} | {r['deep_chains']:<5} | {r['hit_deep']:<8} | {'是' if r['deep_fallback'] else '否'}")
    print("-" * len(header))

    # 汇总统计
    ok_deep = sum(1 for r in results if r["deep_ok"])
    print(f"\n深度回忆成功率: {ok_deep}/{len(results)} (其余回退到浅层)")

    # 清理
    store.close()
    graph.close()
    try:
        shutil.rmtree(_TMP, ignore_errors=True)
        print(f"\n[cleanup] 已清理临时目录 {_TMP}")
    except Exception:
        pass


if __name__ == "__main__":
    main()
