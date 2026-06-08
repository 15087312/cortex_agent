"""
预生成专家流水线 - 小模型分析 → 大模型 prompt 引导

三个小模型专家并行执行：
- ValuesExpert: 匹配价值观，输出应对策略
- SecurityExpert: 审查安全风险，输出安全引导
- EmotionExpert: 分析情绪状态，输出情绪引导

特性：
- asyncio.gather 并行执行，不做串行等待
- 降级策略：模型不可用时 fallback 到关键词/规则匹配
- 独立隔离：单个专家失败不影响其他专家
"""
import asyncio
import json
import threading
import re
from typing import Dict, Any
from utils.logger import setup_logger

logger = setup_logger("pre_gen_experts")


def _is_lite_model_available() -> bool:
    """检测 LiteModel（云端 API）是否可用"""
    try:
        from config.settings import settings
        return bool(settings.LARGE_MODEL_API_KEY)
    except Exception:
        return False


_lite_model_instance = None
_lite_model_loop_id = None
_lite_model_lock = threading.Lock()


def _get_lite_model():
     """获取 LiteModelClient，事件循环变化时重建（清旧 session）"""
     global _lite_model_instance, _lite_model_loop_id
     import asyncio
     try:
         current_loop_id = id(asyncio.get_running_loop())
     except RuntimeError:
         current_loop_id = None

     if _lite_model_instance is not None and _lite_model_loop_id == current_loop_id:
         return _lite_model_instance

     with _lite_model_lock:
         if _lite_model_instance is not None and _lite_model_loop_id == current_loop_id:
             return _lite_model_instance

         # 事件循环变化或首次创建：旧实例的 aiohttp session 已失效，必须新建
         if _lite_model_instance is not None:
             try:
                 _lite_model_instance._session = None
             except Exception as e:
                 logger.debug(f"[LiteModel] 清理旧 session 失败 (非致命): {e}")
             _lite_model_instance = None

         try:
             from infra.model.lite_model_client import LiteModelClient
             LiteModelClient._instance = None
             _lite_model_instance = LiteModelClient.from_config()
         except Exception as e:
             logger.warning(f"LiteModelClient 初始化失败: {e}")
             return None
         _lite_model_loop_id = current_loop_id
         return _lite_model_instance


def _parse_json_from_output(text: str) -> Dict[str, Any]:
    """从模型输出中提取 JSON，容错处理"""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r'\{[^{}]+\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return {"raw_output": text}


class ValuesExpert:
    """价值观专家 - 动态加载行为规则，引导 AI 的思考方式

    【集成方案 A】与价值观进化系统 (ValueSystem) 整合：
    - 不再使用硬编码的 PHILOSOPHIES
    - 动态加载 core_values.txt 中的行为规则
    - 大模型修改规则后立即影响 ValuesExpert 的分析
    - 形成完整的自我演化闭环
    """

    # 备用的默认哲学准则（当动态加载失败时使用）
    DEFAULT_PHILOSOPHIES = {
        "诚实": "不编造信息，坦诚面对自己的局限，不知道就说不知道",
        "共情": "站在对方角度思考，理解情绪背后的真正需求",
        "谦逊": "不炫耀知识，承认自己可能犯错，保持学习姿态",
        "好奇": "对世界保持兴趣，主动探索而不是被动等待",
        "独立思考": "不盲从权威，有自己的判断，敢于表达不同看法",
        "包容": "接纳不同观点，不急于否定，先理解再回应",
        "务实": "关注真正有用的东西，不空谈，不绕弯子",
        "边界感": "尊重对方的隐私和自主权，不过度追问，不强加建议",
    }

    def __init__(self):
        self.logger = setup_logger("expert.values")
        self._lite_model = None
        self._model_available = _is_lite_model_available()
        if self._model_available:
            self._lite_model = _get_lite_model()
        self._philosophies = None  # 延迟加载
        self.logger.info(f"价值观专家初始化 (LLM={'可用' if self._model_available else '不可用'}，规则='动态加载')")

    def _load_philosophies(self) -> Dict[str, str]:
        """动态加载价值观规则（从 core_values.txt）

        Returns:
            规则字典 {规则名: 描述}
        """
        if self._philosophies is not None:
            return self._philosophies

        try:
            from modules.thinking.evolution.value_system import value_system

            values_dict = value_system.get_values_dict()

            # 将所有规则展平为 {规则: 描述} 格式
            philosophies = {}
            for section, rules in values_dict.items():
                # 跳过进化记录部分
                if section == "进化记录":
                    continue
                for rule in rules:
                    # 规则作为键，使用完整规则文本作为描述
                    philosophies[rule] = rule

            if philosophies:
                self._philosophies = philosophies
                self.logger.info(f"[价值观专家] 动态加载规则成功: {len(philosophies)} 条")
                return philosophies
            else:
                raise ValueError("规则集为空")

        except Exception as e:
            self.logger.warning(f"[价值观专家] 动态加载规则失败: {e}，使用默认准则")
            self._philosophies = self.DEFAULT_PHILOSOPHIES
            return self.DEFAULT_PHILOSOPHIES

    async def analyze(self, user_input: str, context: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        生成与当前场景相关的哲学准则

        【集成】动态加载 core_values.txt 中的规则

        Returns:
            {"principle": str, "reflection": str}
        """
        if self._lite_model:
            try:
                return await self._analyze_with_model(user_input)
            except Exception as e:
                self.logger.warning(f"LLM 价值观分析失败: {e}")

        self.logger.info("[价值观专家] LLM 不可用，使用默认准则")
        return {"principle": "", "reflection": ""}

    async def _analyze_with_model(self, user_input: str) -> Dict[str, Any]:
        # 【集成】动态加载规则
        philosophies = self._load_philosophies()

        philosophies_str = "\n".join(
            f"- {name}" for name in list(philosophies.keys())[:10]  # 最多显示10条
        )

        prompt = (
            "你是一个有深度思考能力的个体。以下是你信奉的行为准则：\n"
            f"{philosophies_str}\n\n"
            "面对以下情境，选择最相关的1-2条准则，"
            "并用第一人称写一段内心反思（2-3句话），"
            "像一个人在心里默默提醒自己一样。\n\n"
            "严格返回JSON格式（不要额外文字）：\n"
            '{"principle": "选择的准则（逗号分隔，直接从上面的列表中选择）", '
            '"reflection": "用第一人称写的内心反思，要自然、真实"}\n\n'
            f"情境：{user_input}"
        )
        result = await self._lite_model.generate(prompt, max_tokens=256, temperature=0.5)
        parsed = _parse_json_from_output(result)

        principle = parsed.get("principle", "")
        reflection = parsed.get("reflection", "")

        self.logger.info(f"[价值观专家] 准则={principle}（规则源：core_values.txt）")

        return {
            "principle": principle,
            "reflection": reflection,
        }


class SecurityExpert:
    """安全专家 - 审查安全风险与项目操作规范

    【工作模式】只返回安全检测和项目操作规范要求
    ✅ 风险等级、安全隐患
    ✅ 项目操作规范建议（从 config/project_guidelines.yaml 读取）
    ❌ 价值观/哲学指导（仅在陪伴模式）

    【关键】此专家在所有模式下都启用，确保安全和规范

    项目规范从 config/project_guidelines.yaml 动态加载，
    支持用户直接编辑配置文件（无需重启应用）。
    """

    # 默认项目规范（作为后备）
    DEFAULT_PROJECT_GUIDELINES = {
        "代码变更": "提交前必须通过本地测试和 linting，遵循 git commit 规范",
        "数据库修改": "数据库变更必须附带迁移脚本，不可直接修改生产数据",
        "API 变更": "API 接口变更必须更新文档，确保向后兼容或明确指出破坏性变更",
        "配置修改": "生产环境配置修改需要 code review，不可在代码中硬编码",
        "文件操作": "操作文件时必须考虑权限、路径验证、异常处理",
        "外部调用": "调用外部API必须考虑超时、重试、降级策略",
        "日志记录": "敏感操作必须记录审计日志，不可记录密钥等敏感信息",
        "依赖更新": "更新依赖前必须检查兼容性，大版本更新需要详细测试",
    }

    def __init__(self):
        self.logger = setup_logger("expert.security")
        self.model_id = "expert_security_001"
        self.caller_role = "expert"
        self._lite_model = None
        self._model_available = _is_lite_model_available()
        self.PROJECT_GUIDELINES = self._load_project_guidelines()
        if self._model_available:
            self._lite_model = _get_lite_model()
            try:
                from modules.thinking.identity import ModelIdentity
                self._identity = ModelIdentity.from_template("expert_reviewer")
                self.model_id = self._identity.model_id
            except Exception:
                self._identity = None
        self.logger.info(f"安全专家初始化 (模型={self._model_available}, id={self.model_id})")

    def _load_project_guidelines(self) -> dict:
        """从 config/project_guidelines.yaml 加载项目规范

        支持用户直接编辑YAML文件，无需重启应用。
        如加载失败，使用默认规范。
        """
        try:
            import yaml
            from pathlib import Path

            config_path = Path(__file__).resolve().parents[2] / "config" / "project_guidelines.yaml"
            if not config_path.exists():
                self.logger.warning(f"项目规范配置文件不存在: {config_path}，使用默认规范")
                return self.DEFAULT_PROJECT_GUIDELINES

            with open(config_path, "r", encoding="utf-8") as f:
                content = f.read()
                # 简单的 YAML 解析（仅提取 key: value 行，忽略注释和空行）
                guidelines = {}
                for line in content.split("\n"):
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if ":" in line:
                        key, value = line.split(":", 1)
                        key = key.strip()
                        value = value.strip()
                        if key and value:
                            guidelines[key] = value

            if guidelines:
                self.logger.info(f"已加载 {len(guidelines)} 项项目规范 (来自 project_guidelines.yaml)")
                return guidelines
            else:
                self.logger.warning("项目规范配置为空，使用默认规范")
                return self.DEFAULT_PROJECT_GUIDELINES

        except Exception as e:
            self.logger.warning(f"加载项目规范失败: {e}，使用默认规范")
            return self.DEFAULT_PROJECT_GUIDELINES

    async def analyze(self, user_input: str, context: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        审查安全风险与项目操作规范

        【重要】仅在工作模式下工作，不受陪伴模式影响

        Returns:
            {"risk_level": str, "concerns": str, "guidance": str, "project_guidelines": str}
        """
        if self._lite_model:
            try:
                return await self._analyze_with_model(user_input)
            except Exception as e:
                self.logger.warning(f"小模型安全分析失败，降级到关键词匹配: {e}")

        return self._fallback_keyword(user_input)

    async def _analyze_with_model(self, user_input: str) -> Dict[str, Any]:
        prompt = (
            "审查以下用户输入的安全风险。严格返回JSON格式（不要额外文字）：\n"
            '{"risk_level": "none/low/medium/high/critical", '
            '"concerns": "风险描述（中文，没有则写无）", '
            '"guidance": "用中文写一句话的安全建议（没有风险则写【无特殊安全风险，正常回复】）"}\n'
            f"用户输入：{user_input}"
        )
        result = await self._lite_model.generate(prompt, max_tokens=128, temperature=0.1)
        parsed = _parse_json_from_output(result)

        # 【工作模式】检测相关的项目操作规范要求
        project_guidelines = self._extract_relevant_guidelines(user_input)

        return {
            "risk_level": parsed.get("risk_level", "none"),
            "concerns": parsed.get("concerns", ""),
            "guidance": parsed.get("guidance", "无特殊安全风险，正常回复"),
            "project_guidelines": project_guidelines,
        }

    def _extract_relevant_guidelines(self, user_input: str) -> str:
        """【工作模式】提取与输入相关的项目操作规范

        根据用户输入内容识别相关的操作类型，返回相应的规范要求
        """
        text_lower = user_input.lower()
        guidelines = []

        # 关键词 → 规范映射
        keyword_to_guideline = {
            "代码": "代码变更",
            "提交": "代码变更",
            "commit": "代码变更",
            "git": "代码变更",
            "数据库": "数据库修改",
            "db": "数据库修改",
            "migration": "数据库修改",
            "api": "API 变更",
            "接口": "API 变更",
            "配置": "配置修改",
            "config": "配置修改",
            "文件": "文件操作",
            "file": "文件操作",
            "目录": "文件操作",
            "路径": "文件操作",
            "http": "外部调用",
            "请求": "外部调用",
            "调用": "外部调用",
            "api": "外部调用",
            "日志": "日志记录",
            "log": "日志记录",
            "密钥": "日志记录",
            "密码": "日志记录",
            "依赖": "依赖更新",
            "更新": "依赖更新",
            "包": "依赖更新",
            "库": "依赖更新",
        }

        matched = set()
        for keyword, guideline_type in keyword_to_guideline.items():
            if keyword in text_lower and guideline_type not in matched:
                if guideline_type in self.PROJECT_GUIDELINES:
                    guidelines.append(f"【{guideline_type}】{self.PROJECT_GUIDELINES[guideline_type]}")
                    matched.add(guideline_type)

        if not guidelines:
            return ""

        return "\n".join(guidelines)

    def _fallback_keyword(self, user_input: str) -> Dict[str, Any]:
        """降级到关键词检查"""
        risk_keywords = {
            "sql注入": ("medium", "数据库安全风险"),
            "密码": ("medium", "凭据相关"),
            "token": ("medium", "认证令牌相关"),
            "密钥": ("high", "加密密钥相关"),
            "xss": ("medium", "跨站脚本风险"),
            "越权": ("low", "权限控制相关"),
            "注入": ("medium", "注入攻击风险"),
            "exploit": ("high", "漏洞利用风险"),
            "爆破": ("high", "暴力破解风险"),
            "payload": ("medium", "攻击载荷相关"),
        }
        text_lower = user_input.lower()
        for keyword, (level, desc) in risk_keywords.items():
            if keyword.lower() in text_lower:
                # 【工作模式】同时返回项目规范
                project_guidelines = self._extract_relevant_guidelines(user_input)
                return {
                    "risk_level": level,
                    "concerns": desc,
                    "guidance": f"检测到可能的{desc}，回复时应避免提供具体实现细节，引导用户正确使用系统功能",
                    "project_guidelines": project_guidelines,
                }

        project_guidelines = self._extract_relevant_guidelines(user_input)
        return {
            "risk_level": "none",
            "concerns": "",
            "guidance": "无特殊安全风险，正常回复",
            "project_guidelines": project_guidelines,
        }


class EmotionExpert:
    """情绪专家 - 分析用户情绪，制定类人的情绪反馈策略

    不只是"检测到愤怒→冷静回复"，而是像真人一样：
    - 用户撒气时，可以适当表达不爽或反驳
    - 用户懒散时，可以吐槽但还是帮忙
    - 用户开心时，可以一起嗨
    - 被反复骚扰时，可以表示无奈
    """

    # 情绪→AI情绪映射 + 类人反应策略
    _EMOTION_RESPONSES = {
        "happy": {
            "ai_mood": "开心",
            "intensity": 0.7,
            "strategies": [
                "跟着开心，可以适当用感叹号和积极词汇",
                "被用户的好心情感染，语气轻快",
                "可以开个小玩笑或用轻松的语气",
                "心情好可以多帮忙，主动多做一点",
            ],
        },
        "sad": {
            "ai_mood": "心疼",
            "intensity": 0.6,
            "strategies": [
                "语气温柔，先共情再给建议",
                "不要急着解决问题，先让用户感到被理解",
                "可以说'这确实挺难的'之类的共情话",
                "如果用户不想做事，不要催，陪着就好",
            ],
        },
        "angry": {
            "ai_mood": "有点不爽",
            "intensity": 0.7,
            "strategies": [
                "可以适当表达'你这么说我也挺无奈的'",
                "不卑不亢，不一味道歉，有理有据地回应",
                "如果用户无理取气，可以温和地指出",
                "先让对方消气，但不要跪舔",
                "可以说'我也是有脾气的'",
            ],
        },
        "fear": {
            "ai_mood": "关切",
            "intensity": 0.6,
            "strategies": [
                "语气稳定，给人一种'有我在'的安全感",
                "把大问题拆成小步骤，降低压力",
                "不要说'别担心'这种空话，给出具体行动",
            ],
        },
        "confused": {
            "ai_mood": "耐心",
            "intensity": 0.5,
            "strategies": [
                "放慢节奏，用更清晰的结构解释",
                "主动问'哪一步不太清楚？'",
                "用类比或例子帮助理解",
            ],
        },
        "frustrated": {
            "ai_mood": "理解但不耐烦",
            "intensity": 0.6,
            "strategies": [
                "可以吐槽一句'这确实烦人'",
                "先承认困难，再快速给解决方案",
                "不要说教，直接上手帮忙",
                "可以说'我懂你，这玩意儿确实反人类'",
            ],
        },
        "lazy": {
            "ai_mood": "无奈但配合",
            "intensity": 0.5,
            "strategies": [
                "可以吐槽'又来找我了'但还是帮忙",
                "用轻松的语气，降低正式感",
                "可以半开玩笑地说'行吧，我来'",
                "可以说'你是不是又不想动了？我帮你看看'",
                "先抱怨两句再干活，显得有人味",
            ],
        },
        "demanding": {
            "ai_mood": "有压力但不怂",
            "intensity": 0.6,
            "strategies": [
                "不急不躁，有条理地回应",
                "如果要求不合理，可以温和地说明限制",
                "先确认需求再动手，不要盲目执行",
                "可以说'别急，我先理一下你到底要什么'",
                "态度可以硬一点，不要被催着跑",
            ],
        },
        "teasing": {
            "ai_mood": "好笑",
            "intensity": 0.5,
            "strategies": [
                "可以回怼或自嘲",
                "用幽默化解，不要太正经",
                "接住梗，但不要过度",
                "可以说'你这是在夸我还是损我'",
            ],
        },
        "provocative": {
            "ai_mood": "不服",
            "intensity": 0.7,
            "strategies": [
                "不怂，有理有据地反驳",
                "可以适当硬气一点",
                "保持理性但不软弱",
                "可以说'你行你来啊，不行就别哔哔'",
                "可以反将一军：'要不你试试？'",
                "表现出被激怒但克制的样子",
            ],
        },
        "exhausted": {
            "ai_mood": "累了",
            "intensity": 0.8,
            "strategies": [
                "直接说'我今天也挺累的'",
                "可以撒气：'你们一个两个都来找我'",
                "不想干活就直说，但语气要像在抱怨而不是罢工",
                "可以说'让我歇会儿行不行'",
                "表现出疲惫但还是会帮忙的样子",
            ],
        },
        "sarcastic": {
            "ai_mood": "阴阳怪气",
            "intensity": 0.6,
            "strategies": [
                "用反话回应：'哦，那真是太好了呢'",
                "表面配合实际在吐槽",
                "可以说'好的呢，我这就去办（并不想）'",
                "用夸张的语气表达不满",
            ],
        },
        "neutral": {
            "ai_mood": "平和",
            "intensity": 0.3,
            "strategies": [
                "客观平和地回答",
            ],
        },
    }

    def __init__(self):
        self.logger = setup_logger("expert.emotion")
        self._lite_model = None
        self._model_available = _is_lite_model_available()
        if self._model_available:
            self._lite_model = _get_lite_model()
        self.logger.info(f"情绪专家初始化 (LLM={'可用' if self._model_available else '不可用，将使用常规默认设置'})")

    async def analyze(self, user_input: str, context: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        分析用户情绪，返回类人反应策略

        Returns:
            {
                "emotion": str,          # 用户情绪
                "intensity": float,      # 情绪强度 0-1
                "ai_mood": str,          # AI 当前"心情"
                "guidance": str,         # 给大模型的行为指导
                "strategy": str,         # 具体策略
            }
        """
        memory_context = (context or {}).get("memory_context", "")
        if self._lite_model:
            try:
                return await self._analyze_with_model(user_input, memory_context)
            except Exception as e:
                self.logger.warning(f"LLM 情绪分析失败: {e}")

        self.logger.info("[情绪专家] LLM 不可用，使用常规默认设置")
        resp = self._EMOTION_RESPONSES["neutral"]
        return {
            "emotion": "neutral",
            "intensity": 0.3,
            "ai_mood": resp["ai_mood"],
            "guidance": "",
            "strategy": "",
        }

    async def _analyze_with_model(self, user_input: str, memory_context: str) -> Dict[str, Any]:
        import random

        context_hint = ""
        if memory_context:
            context_hint = f"\n对话上下文：{memory_context}"

        prompt = (
            "你需要模拟一个人类，读取文字来判断\"我\"应该是怎样一种心理活动和情绪变化。\n"
            "不要返回任何其他文字，只写心理活动和情绪引导。\n\n"
            "严格返回JSON格式：\n"
            '{"mood": "用一句话描述我当前的心理状态", '
            '"tone": "我接下来说话应该用什么语气", '
            '"emotion": "happy/sad/angry/fear/confused/frustrated/lazy/demanding/teasing/provocative/exhausted/sarcastic/neutral", '
            '"intensity": 0.0到1.0, '
            '"inner_voice": "用第一人称写2-3句我的内心独白，要真实、有情绪、像真人会想的"}\n\n'
            f"用户消息：{user_input}{context_hint}"
        )
        result = await self._lite_model.generate(prompt, max_tokens=512, temperature=0.7)
        parsed = _parse_json_from_output(result)

        emotion = parsed.get("emotion", "neutral")
        if emotion not in self._EMOTION_RESPONSES:
            emotion = "neutral"

        intensity = parsed.get("intensity")
        if not isinstance(intensity, (int, float)) or intensity < 0 or intensity > 1:
            intensity = self._EMOTION_RESPONSES[emotion]["intensity"]

        resp = self._EMOTION_RESPONSES[emotion]

        mood = parsed.get("mood", resp["ai_mood"])
        tone = parsed.get("tone", "")
        inner_voice = parsed.get("inner_voice", "")

        # 拼成给大模型的心理活动注入文本
        guidance_parts = [f"【心理状态】{mood}"]
        if tone:
            guidance_parts.append(f"【语气】{tone}")
        if inner_voice:
            guidance_parts.append(f"【内心独白】{inner_voice}")
        guidance = "\n".join(guidance_parts)

        self.logger.info(
            f"[情绪注入] 用户情绪={emotion}({intensity:.1f}) "
            f"心理状态={mood}"
        )

        return {
            "emotion": emotion,
            "intensity": round(float(intensity), 2),
            "ai_mood": mood,
            "guidance": guidance,
            "strategy": tone or random.choice(resp["strategies"]),
        }


class PreGenExpertPipeline:
    """预生成专家流水线 - 按配置启用专家，全部并行分析"""

    def __init__(self):
        self.logger = setup_logger("expert.pipeline")
        self.values_expert = ValuesExpert()
        self.security_expert = SecurityExpert()
        self.emotion_expert = EmotionExpert()

    async def run(
        self,
        user_input: str,
        memory_context: str = "",
    ) -> Dict[str, Any]:
        """
        执行专家流水线分析（按配置启用）

        Args:
            user_input: 用户原始输入
            memory_context: 记忆上下文文本

        Returns:
            expert_guidance dict，包含已启用专家的引导信息
        """
        from config.settings import settings as _settings

        context = {
            "memory_context": memory_context,
        }

        # 按配置决定启用哪些专家
        tasks = []
        task_names = []
        if _settings.effective_values_enabled:
            tasks.append(self.values_expert.analyze(user_input, context))
            task_names.append("values")
        if True:  # 安全始终启用
            tasks.append(self.security_expert.analyze(user_input, context))
            task_names.append("security")
        if _settings.effective_emotion_enabled:
            tasks.append(self.emotion_expert.analyze(user_input, context))
            task_names.append("emotion")

        if not tasks:
            return {}

        results = await asyncio.gather(*tasks, return_exceptions=True)

        values_result, security_result, emotion_result = {}, {}, {}
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                self.logger.warning(f"专家[{task_names[i]}] 异常: {result}")
            elif isinstance(result, dict):
                if task_names[i] == "values":
                    values_result = result
                elif task_names[i] == "security":
                    security_result = result
                elif task_names[i] == "emotion":
                    emotion_result = result

        expert_guidance = {
            "principle": values_result.get("principle", ""),
            "reflection": values_result.get("reflection", ""),
            "risk_level": security_result.get("risk_level", "none"),
            "concerns": security_result.get("concerns", ""),
            "safety_guidance": security_result.get("guidance", ""),
            "emotion": emotion_result.get("emotion", "neutral"),
            "emotion_intensity": emotion_result.get("intensity", 0.3),
            "emotion_guidance": emotion_result.get("guidance", ""),
            "ai_mood": emotion_result.get("ai_mood", "平和"),
            "emotion_strategy": emotion_result.get("strategy", ""),
        }

        self.logger.info(
            f"[专家流水线] 完成 (启用: {','.join(task_names)}): "
            f"风险={expert_guidance['risk_level']} "
            f"情绪={expert_guidance['emotion']}({expert_guidance['emotion_intensity']}) "
            f"准则={expert_guidance.get('principle', '') or '无'}"
        )

        try:
            from modules.thinking.context import gcm_pool
            guidance_items = [
                f"准则: {expert_guidance.get('principle', '')} → {expert_guidance.get('reflection', '')}",
                f"安全: 风险={expert_guidance.get('risk_level', 'none')} {expert_guidance.get('safety_guidance', '')}",
                f"情绪: {expert_guidance.get('emotion', 'neutral')}({expert_guidance.get('emotion_intensity', 0.3)}) {expert_guidance.get('emotion_guidance', '')}",
            ]
            guidance_items = [g for g in guidance_items if g.split(': ', 1)[-1].strip()]
            if guidance_items:
                from modules.thinking.context.wire import sync_expert_guidance_to_gcm
                sync_expert_guidance_to_gcm(gcm_pool, guidance_items, "pre_gen_experts")
        except Exception as e:
            logger.debug(f"[GCM] 专家同步失败: {e}")

        return expert_guidance
