# 价值观自动进化系统 — 配置参考

本文档描述与价值观检测、修改、演进相关的所有配置项。

---

## 运行模式配置

### COMPANION_MODE（陪伴模式）

```env
COMPANION_MODE=False
```

**说明**：
- `True`（陪伴模式）：
  - ✅ ValuesExpert 启用（价值观指导）
  - ✅ EmotionExpert 启用（情绪反馈）
  - ✅ 感知系统规范检测启用
  - ❌ 完整工具委托关闭

- `False`（工作模式）：
  - ❌ ValuesExpert 禁用（仅安全检测）
  - ❌ EmotionExpert 禁用
  - ✅ SecurityExpert 启用（项目规范硬编码）
  - ✅ 完整工具委托启用

---

## 价值观系统配置

### VALUE_ALIGNMENT_HANDLER_ENABLED

```env
VALUE_ALIGNMENT_HANDLER_ENABLED=True
```

**说明**：
- `True`：启用后台被动监测（DifferenceDetector 统计对齐度趋势）
- `False`：禁用后台监测
- **实时检测不受此项影响**（RuleCompliancePerception 总是启用）

**职责划分**：
- **实时检测**（RuleCompliancePerception）：
  - 读取最新输出
  - 与 core_values.txt 规则对比
  - 生成感知事件到系统提示词
  - 大模型同轮看到并调整

- **被动监测**（ValueAlignmentHandler）：
  - 计算对齐度评分（0-1）
  - 统计严重程度分布
  - 发现持续性违反模式
  - 供大模型定期查询，自主决定修改规则

---

## 感知系统配置

### PERCEPTION_ENABLED

```env
PERCEPTION_ENABLED=True
```

**说明**：
- `True`：启用感知系统（文件/对话/屏幕监控 + 规范违反检测）
- `False`：禁用感知系统

### 感知系统中的规范检测

**自动启用**（不需配置）：当 PERCEPTION_ENABLED=True 时，RuleCompliancePerception 自动执行：

```python
from modules.perception import get_perception_integrator

integrator = get_perception_integrator()

# 在大模型生成输出后调用
integrator.check_output_compliance(output_content)
```

**检测规则来源**：`modules/thinking/evolution/prompts/core_values.txt`

---

## 差异检测器配置

### DIFFERENCE_DETECTOR_ENABLED

```env
DIFFERENCE_DETECTOR_ENABLED=True
```

**说明**：
- `True`：启用差异检测器（包括价值观对齐差异源）
- `False`：禁用差异检测器

**价值观对齐差异源**（ValueAlignmentDifferenceSource）：
- 自动加载规则（动态）
- 每 30 秒计算一次对齐度
- 生成强度评分（intensity >= 50 时为高强度）
- 注册到 DifferenceDetector

---

## 大模型工具配置

### modify_value_system（价值观修改工具）

**权限**：`admin`（仅大模型 "large" 角色可调用）

**可修改内容**：
- ✅ 基本原则
- ✅ 行为准则
- ✅ 进化记录

**质量控制**：
- 最少 8 字符
- 避免通用词汇（"无需修改"、"可以保持"）
- 禁止规则必须 >= 15 字符
- 新规则相似度 > 60% 时被过滤

**使用示例**：
```python
await tool_modify_value_system(
    action="add_rule",
    section="行为准则",
    rule="输出要简洁有力，避免冗长叙述",
    reason="检测到过长回复模式"
)
```

### get_current_values（查询当前规则）

**权限**：`query`（任何角色可调用）

**格式选项**：
- `full`：完整文本
- `compact`：精简版本（推荐）
- `sections`：按分类列出

### get_evolution_log（查询修改历史）

**权限**：`query`（任何角色可调用）

**用途**：审计追踪，查看规则演进历史

---

## 项目操作规范（AI 无法修改，用户可配置化修改）

项目规范定义在 `config/project_guidelines.yaml` 中，**AI 无法通过工具修改**：

**配置文件** (`config/project_guidelines.yaml`)：
```yaml
# 代码变更规范
代码变更: 提交前必须通过本地测试和 linting，遵循 git commit 规范

# 数据库修改规范
数据库修改: 数据库变更必须附带迁移脚本，不可直接修改生产数据

# API 接口变更规范
API 变更: API 接口变更必须更新文档，确保向后兼容或明确指出破坏性变更

# ... 其他规范

# 【可选】自定义规范（取消注释以启用）
# 性能优化: 性能敏感代码必须进行基准测试
```

**权限隔离**：
- ❌ **AI 无法修改**：无 modify_project_guidelines 工具，确保安全约束不被绕过
- ✅ **用户/管理员可修改**：
  - 直接编辑 `config/project_guidelines.yaml` 文件
  - **无需重启应用**，下一轮大模型处理时自动生效
  - 无需代码修改，仅需文件编辑权限

**修改流程**：
```bash
# 1. 编辑配置文件
vi config/project_guidelines.yaml

# 2. 保存文件（无需重启应用）

# 3. SecurityExpert 在下一次初始化时自动加载新规范
```

**加载机制**：
- SecurityExpert 在初始化时调用 `_load_project_guidelines()`
- 从 YAML 文件动态读取规范
- 加载失败时自动降级到内置默认规范
- 所有加载过程记录到日志

**为什么不硬编码**：
- 项目规范是系统安全边界，防止 AI 自我修改约束
- 价值观可以动态修改（支持 AI 自适应）
- **规范配置化**让用户无需修改代码即可调整
- 降低维护成本，提高灵活性

---

## 文件对应关系

| 配置项 | 文件 | 说明 |
|--------|------|------|
| COMPANION_MODE | config/settings.py | 运行模式开关 |
| VALUE_ALIGNMENT_HANDLER_ENABLED | config/settings.py | 被动监测开关 |
| PERCEPTION_ENABLED | config/settings.py | 感知系统开关 |
| DIFFERENCE_DETECTOR_ENABLED | config/settings.py | 差异检测开关 |
| 价值观规则 | modules/thinking/evolution/prompts/core_values.txt | 动态规则定义 |
| PROJECT_GUIDELINES | modules/thinking/experts/pre_gen_experts.py | 项目规范硬编码 |

---

## 完整工作流

```
【用户请求】
    ↓
【SecurityExpert（总是启用）】
  ✅ 检查安全风险
  ✅ 返回项目操作规范要求

【RuleCompliancePerception（实时，基于PERCEPTION_ENABLED）】
  ✅ 检测规范违反
  ✅ 生成感知事件
  
【大模型（同轮）】
  ✅ 看到安全检测 + 项目规范 + 规范违反
  ✅ 调整输出

【ValuesExpert（可选，基于COMPANION_MODE）】
  ✅ 仅陪伴模式下启用
  ✅ 从 core_values.txt 动态加载规则

【DifferenceDetector（被动，基于两个开关）】
  ✅ 统计对齐度趋势
  ✅ 大模型定期查询
  ✅ 决定是否修改规则（modify_value_system）
```

---

## 常见配置场景

### 场景 1：生产环境（工作模式）

```env
COMPANION_MODE=False
VALUE_ALIGNMENT_HANDLER_ENABLED=True
PERCEPTION_ENABLED=True
DIFFERENCE_DETECTOR_ENABLED=True
```

**特点**：
- 完整工具委托，不受价值观约束
- 安全检测 + 项目规范始终启用
- 规范违反实时提示
- 后台监测趋势用于未来改进

### 场景 2：陪伴模式（AI助手）

```env
COMPANION_MODE=True
VALUE_ALIGNMENT_HANDLER_ENABLED=True
PERCEPTION_ENABLED=True
DIFFERENCE_DETECTOR_ENABLED=True
```

**特点**：
- 启用价值观指导和情绪反馈
- 安全检测 + 规范违反检测
- 完整专家流水线
- 支持自我修正和规则演进

### 场景 3：仅安全检测

```env
COMPANION_MODE=False
VALUE_ALIGNMENT_HANDLER_ENABLED=False
PERCEPTION_ENABLED=False
DIFFERENCE_DETECTOR_ENABLED=False
```

**特点**：
- 最小化模式，仅进行安全审查
- 项目规范始终生效
- 无感知、无差异检测、无后台监测

---

## 环境变量示例

完整的 `.env` 配置文件：

```env
# 运行模式
COMPANION_MODE=False
APP_ENV=production
LOG_LEVEL=INFO

# 模型配置
LARGE_MODEL_API_KEY=sk-xxx
LARGE_MODEL_NAME=deepseek-v4-flash
SMALL_MODEL_API_KEY=xxx
EXPERT_MODEL_NAME=qwen2.5-7b-instruct

# 感知与检测
PERCEPTION_ENABLED=True
DIFFERENCE_DETECTOR_ENABLED=True
VALUE_ALIGNMENT_HANDLER_ENABLED=True

# 主动搭话
PROACTIVE_OUTREACH_ENABLED=True
PROACTIVE_OUTREACH_COOLDOWN_MINUTES=15
```

---

## 调试与监控

### 查看当前配置

```python
from config.settings import settings

print(f"运行模式: {'陪伴' if settings.COMPANION_MODE else '工作'}")
print(f"感知系统: {settings.PERCEPTION_ENABLED}")
print(f"价值观监测: {settings.VALUE_ALIGNMENT_HANDLER_ENABLED}")
```

### 查看当前规则

```python
from infra.tool_manager.tools.value_tools import get_current_values

rules = get_current_values(format="compact")
print(rules)
```

### 查看修改历史

```python
from infra.tool_manager.tools.value_tools import get_evolution_log

log = get_evolution_log(limit=20)
print(log)
```

### 查看对齐度统计

```python
from modules.difference_detector import get_detector

detector = get_detector()
differences = detector.get_recent_differences(source_type="value_alignment", limit=10)
for d in differences:
    print(f"对齐度: {d.payload.get('alignment_score')}, 强度: {d.intensity}")
```

---

## 故障排除

### 规范违反没有被检测到

- ✅ 检查 `PERCEPTION_ENABLED=True`
- ✅ 检查 core_values.txt 中是否有相关规则
- ✅ 查看日志：`logs/perception.log` 或 `logs/rule_compliance_perception.log`

### 大模型无法调用 modify_value_system

- ✅ 检查权限：工具权限为 `admin`，仅大模型可调用
- ✅ 检查规则质量：可能被质量门控拦截
- ✅ 查看日志：`logs/value_tools.log`

### 后台监测没有更新

- ✅ 检查 `VALUE_ALIGNMENT_HANDLER_ENABLED=True`
- ✅ 检查 `DIFFERENCE_DETECTOR_ENABLED=True`
- ✅ 等待差异检测器的 30 秒扫描周期

---

**更新于**: 2026-06-06  
**系统版本**: Phase 4 完整  
**负责人**: AI System
