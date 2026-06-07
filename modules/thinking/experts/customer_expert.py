"""
客户专家 (CustomerExpert) — 从用户视角验收交付成果

继承 RuntimeExpert 基类。
职责：
- 以"对技术一无所知"的用户视角审视代码和交付成果
- 提出天真的、非技术化的问题
- 判断交付成果是否满足用户需求
- 验收(accept)或拒绝(reject)交付成果，给出用户视角的具体原因

使用方式：
  模型通过 delegate_task 委托客户验收任务 → 探针激活 CustomerExpert
  CustomerExpert 审查后给出 accept/reject 意见
"""
import json
import re
from typing import Dict, Any, List, Optional

from modules.thinking.experts.base import RuntimeExpert, register_runtime_expert
from utils.logger import setup_logger

logger = setup_logger("customer_expert")


class CustomerExpert(RuntimeExpert):
    """客户专家 — 用户视角的交付验收者

    继承 RuntimeExpert，通过 process() 验收交付成果。
    核心特点：不懂技术、只关注用户体验和需求满足度。
    """

    template_key = "expert_customer"

    # 验收结果阈值
    ACCEPT_CONFIDENCE_THRESHOLD = 0.6  # 满意度 >= 此值视为通过

    def __init__(self, model_instance=None,
                 session_id="", model_id=""):
        super().__init__(
            model_instance=model_instance,
            session_id=session_id,
            model_id=model_id,
        )

        # 验收历史
        self._verdicts: List[Dict[str, Any]] = []
        self._accepted_count: int = 0
        self._rejected_count: int = 0

        logger.info(
            f"[CustomerExpert] 初始化完成: "
            f"验收历史: 0, 通过: 0, 拒绝: 0"
        )

    # ------------------------------------------------------------------
    # RuntimeExpert 抽象方法实现
    # ------------------------------------------------------------------

    async def process(
        self,
        request_text: str,
        messages: List[Dict[str, Any]],
        dialog_context: str,
    ) -> str:
        """验收交付成果 — 从用户视角判断是否满足需求

        流程：
        1. 解析请求，提取需验收的内容
        2. 以"不懂技术"的视角审查
        3. 给出 accept/reject 判定 + 具体原因
        """
        # 提取验收内容
        deliverable = self._extract_deliverable(request_text, messages, dialog_context)

        if not deliverable.strip():
            return (
                "【客户验收】未找到可验收的交付内容。"
                "请提供具体的功能描述、代码或交付说明。"
            )

        # 用模型生成客户视角的验收意见
        if self.model_instance and hasattr(self.model_instance, 'client'):
            try:
                # 权限检查：CustomerExpert 不应该直接调用底层 client
                # 只有在通过合法的 API 层时才允许执行
                if not getattr(self.identity, 'can_use_model_inference', False):
                    self.logger.warning(
                        "CustomerExpert 没有权限直接调用模型推理，降级到规则检查"
                    )
                    verdict = self._fallback_review(deliverable)
                else:
                    verdict = await self._customer_review_with_model(deliverable)
            except Exception as e:
                self.logger.warning(f"模型验收失败，降级到规则检查: {e}")
                verdict = self._fallback_review(deliverable)
        else:
            verdict = self._fallback_review(deliverable)

        # 记录验收历史
        self._verdicts.append(verdict)
        if verdict.get("accepted"):
            self._accepted_count += 1
        else:
            self._rejected_count += 1

        # 写入 Blackboard
        self.write_response(json.dumps(verdict, ensure_ascii=False, default=str))

        # 格式化输出
        return self._format_verdict(verdict)

    # ------------------------------------------------------------------
    # 验收核心
    # ------------------------------------------------------------------

    def _extract_deliverable(
        self,
        request_text: str,
        messages: List[Dict[str, Any]],
        dialog_context: str,
    ) -> str:
        """从请求中提取需验收的交付内容"""
        parts = []

        # 请求文本本身
        if request_text:
            # 清理委托标记
            cleaned = re.sub(r'【[^】]+】', '', request_text).strip()
            if cleaned:
                parts.append(f"## 验收请求\n{cleaned}")

        # MessageBus 消息
        for msg in messages:
            content = str(msg.get("content", ""))
            if content:
                parts.append(f"## 相关消息\n{content[:1000]}")

        # Blackboard 上下文（最近的对话）
        if dialog_context:
            parts.append(f"## 对话上下文\n{dialog_context[:2000]}")

        return "\n\n".join(parts)

    async def _customer_review_with_model(self, deliverable: str) -> Dict[str, Any]:
        """用模型进行客户视角审查"""
        client = self.model_instance.client

        prompt = (
            "你是「客户」角色，完全不懂编程和技术。请验收以下交付成果。\n\n"
            "## 验收标准（从用户视角）\n"
            "1. 这个功能/代码解决了我提出的问题吗？\n"
            "2. 使用起来直观吗？（即使你不懂技术也能判断）\n"
            "3. 有没有什么让你困惑的地方？\n"
            "4. 结果是否符合你的预期？\n\n"
            "## 验收规则\n"
            "- 如果你觉得功能满足需求、使用直观 → accepted: true\n"
            "- 如果你觉得有问题、不清晰、不符合需求 → accepted: false\n"
            "- 你是客户，你有权拒绝不合格的交付！\n\n"
            "严格返回 JSON 格式（不要额外文字）：\n"
            '{"accepted": true/false,\n'
            ' "satisfaction": 0.0-1.0 满意度评分,\n'
            ' "concerns": ["用户视角的具体问题1", "问题2"],\n'
            ' "questions": ["你不理解的方面1", "方面2"],\n'
            ' "verdict": "用中文一句话总结你的验收结论（非技术语言）",\n'
            ' "suggestion": "如果不满意，用非技术语言描述你期望的改进方向"}\n\n'
            f"交付内容：\n{deliverable[:2000]}"
        )

        result = await client.generate(prompt, max_tokens=512, temperature=0.3)
        result_text = result if isinstance(result, str) else str(result)

        # 解析 JSON
        json_match = re.search(r'\{[^{}]*\}', result_text, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group(0))
                return {
                    "accepted": data.get("accepted", False),
                    "satisfaction": float(data.get("satisfaction", 0.5)),
                    "concerns": data.get("concerns", []),
                    "questions": data.get("questions", []),
                    "verdict": data.get("verdict", ""),
                    "suggestion": data.get("suggestion", ""),
                    "source": "model",
                }
            except (json.JSONDecodeError, ValueError):
                pass

        # 解析失败，从文本推断
        return self._parse_verdict_from_text(result_text)

    def _fallback_review(self, deliverable: str) -> Dict[str, Any]:
        """降级审查 — 基于规则检查交付内容"""
        concerns = []
        questions = []

        # 规则1: 检查是否为空或过短
        if len(deliverable.strip()) < 50:
            concerns.append("交付内容太少，看不出来做了什么")
            questions.append("这到底是什么功能？能再详细说明一下吗？")

        # 规则2: 检查是否有用户可见的功能描述
        user_facing_keywords = ["用户", "界面", "显示", "按钮", "输入", "输出", "功能", "页面"]
        has_user_facing = any(kw in deliverable for kw in user_facing_keywords)
        if not has_user_facing:
            concerns.append("没有说明对用户有什么好处，我只关心我能不能用")
            questions.append("这个改动对我（用户）来说意味着什么？")

        # 规则3: 检查是否包含太多技术术语（客户完全不懂这些）
        tech_terms = ["API", "数据库", "SQL", "缓存", "异步", "中间件", "序列化",
                      "endpoint", "middleware", "serialize", "JWT", "token",
                      "bcrypt", "加密算法", "哈希", "OAuth", "REST", "GraphQL",
                      "Docker", "Kubernetes", "微服务", "消息队列", "负载均衡"]
        found_tech = [t for t in tech_terms if t.lower() in deliverable.lower()]
        if len(found_tech) > 3:
            concerns.append(f"用了太多技术术语（{', '.join(found_tech[:5])}），完全听不懂")

        # 规则4: 检查是否有验收标记
        acceptance_keywords = ["通过", "验收", "accept", "完成", "done", "通过测试"]
        has_acceptance = any(kw in deliverable.lower() for kw in acceptance_keywords)

        accepted = len(concerns) == 0 or (has_acceptance and len(concerns) <= 1)
        satisfaction = max(0.2, 1.0 - len(concerns) * 0.25)

        return {
            "accepted": accepted,
            "satisfaction": satisfaction,
            "concerns": concerns,
            "questions": questions,
            "verdict": (
                "验收通过，功能满足基本需求" if accepted
                else f"验收不通过：{concerns[0] if concerns else '需要更多信息'}"
            ),
            "suggestion": (
                "" if accepted
                else "请用我能理解的语言重新说明这个功能，减少技术术语"
            ),
            "source": "fallback_rules",
        }

    def _parse_verdict_from_text(self, text: str) -> Dict[str, Any]:
        """从非 JSON 文本中推断验收结果"""
        text_lower = text.lower()

        # 推断是否通过
        accept_signals = ["通过", "验收通过", "accept", "满意", "没问题", "可以"]
        reject_signals = ["不通过", "拒绝", "reject", "有问题", "不满意", "不行",
                          "重做", "修改", "不清楚", "不明白"]

        accept_score = sum(1 for s in accept_signals if s in text_lower)
        reject_score = sum(1 for s in reject_signals if s in text_lower)

        accepted = accept_score > reject_score

        return {
            "accepted": accepted,
            "satisfaction": 0.6 if accepted else 0.3,
            "concerns": [],
            "questions": [],
            "verdict": text[:200],
            "suggestion": "",
            "source": "text_parsed",
        }

    def _format_verdict(self, verdict: Dict[str, Any]) -> str:
        """格式化验收结果为可读文本"""
        accepted = verdict.get("accepted", False)
        satisfaction = verdict.get("satisfaction", 0.5)
        verdict_text = verdict.get("verdict", "")
        concerns = verdict.get("concerns", [])
        questions = verdict.get("questions", [])
        suggestion = verdict.get("suggestion", "")

        icon = "✅" if accepted else "❌"
        status = "验收通过" if accepted else "验收不通过"

        lines = [
            f"{icon} 【客户验收】{status}",
            f"满意度: {'★' * max(1, int(satisfaction * 5))}{'☆' * max(0, int((1 - satisfaction) * 5))} ({satisfaction:.0%})",
            f"结论: {verdict_text}",
        ]

        if concerns:
            lines.append("")
            lines.append("⚠️ 客户疑虑:")
            for c in concerns:
                lines.append(f"  • {c}")

        if questions:
            lines.append("")
            lines.append("❓ 客户想问:")
            for q in questions:
                lines.append(f"  • {q}")

        if suggestion:
            lines.append("")
            lines.append(f"💡 改进方向: {suggestion}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------

    def review_deliverable(self, content: str, description: str = "") -> Dict[str, Any]:
        """外部调用：同步验收指定内容（使用降级规则）"""
        deliverable = f"## 说明\n{description}\n\n## 内容\n{content}"
        verdict = self._fallback_review(deliverable)
        self._verdicts.append(verdict)
        if verdict.get("accepted"):
            self._accepted_count += 1
        else:
            self._rejected_count += 1
        return verdict

    def get_acceptance_stats(self) -> Dict[str, Any]:
        """获取验收统计"""
        total = self._accepted_count + self._rejected_count
        return {
            "total_reviews": total,
            "accepted": self._accepted_count,
            "rejected": self._rejected_count,
            "acceptance_rate": (
                self._accepted_count / max(total, 1)
            ),
            "recent_verdicts": self._verdicts[-5:],
        }

    def get_status(self) -> Dict[str, Any]:
        """获取状态（扩展基类）"""
        status = super().get_status()
        status.update(self.get_acceptance_stats())
        return status


# 注册：让 ModelRunner 能根据 role="customer" 自动激活 CustomerExpert
register_runtime_expert("customer", CustomerExpert)
