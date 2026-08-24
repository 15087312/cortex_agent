# Value System Auto-Evolution — Configuration Reference

**Language**: [English](./CONFIG_VALUE_EVOLUTION.en.md) | [简体中文](./CONFIG_VALUE_EVOLUTION.md)

> **Deprecation Notice (2026)**: The components described in this document — `VALUE_ALIGNMENT_HANDLER_ENABLED`/`ValueAlignmentHandler`/
> `RuleCompliancePerception`/`SecurityExpert`, etc. — **no longer exist**, and `COMPANION_MODE` has been removed.
> The current value system consists of:
> - **`ValueSystem` in `config/values_store.py`**: stored in `data/values/core_values.txt`;
>   add_rule/remove_rule/update_rule/cleanup/reset, rule quality gating (length/generality) and similarity-based deduplication (0.75)
> - **Conscience system `modules/thinking/conscience.py`**: inner monologue / past-experience injection (the carrier for rule perception and self-correction)
> - **Tool `modify_value_system`** (`infra/tool_manager/tools/value_tools.py`, category=admin / risk=CRITICAL)
>
> The configuration details below are provided for historical reference only.

This document describes all configuration items related to value detection, modification, and evolution.

---

## Run Mode Configuration

> Note: `COMPANION_MODE` (Companion Mode) has been removed; this configuration item no longer exists. Run mode is controlled by `EXECUTION_MODE` (plan/edit/yolo/control) and `CORTEX_MODE` (agent/chatonly).

---

## Value System Configuration

### VALUE_ALIGNMENT_HANDLER_ENABLED

```env
VALUE_ALIGNMENT_HANDLER_ENABLED=True
```

**Description**:
- `True`: enable background passive monitoring (DifferenceDetector tracks alignment trends)
- `False`: disable background monitoring
- **Real-time detection is NOT affected by this setting** (RuleCompliancePerception is always enabled)

**Division of responsibilities**:
- **Real-time detection** (RuleCompliancePerception):
  - Reads the latest output
  - Compares it against the core_values.txt rules
  - Generates perception events injected into the system prompt
  - The LLM sees them within the same turn and adjusts accordingly

- **Passive monitoring** (ValueAlignmentHandler):
  - Computes alignment scores (0–1)
  - Tracks severity distribution
  - Detects persistent violation patterns
  - Available for the LLM to query periodically and autonomously decide whether to modify rules

---

## Perception System Configuration

### PERCEPTION_ENABLED

```env
PERCEPTION_ENABLED=True
```

**Description**:
- `True`: enable the perception system (file/conversation/screen monitoring + norm violation detection)
- `False`: disable the perception system

### Norm Detection Within the Perception System

**Automatically enabled** (no configuration needed): when PERCEPTION_ENABLED=True, RuleCompliancePerception runs automatically:

```python
from modules.perception import get_perception_integrator

integrator = get_perception_integrator()

# Call after the LLM generates its output
integrator.check_output_compliance(output_content)
```

**Source of detection rules**: `modules/thinking/evolution/prompts/core_values.txt`

---

## Difference Detector Configuration

### DIFFERENCE_DETECTOR_ENABLED

```env
DIFFERENCE_DETECTOR_ENABLED=True
```

**Description**:
- `True`: enable the difference detector (including the value-alignment difference source)
- `False`: disable the difference detector

**Value Alignment Difference Source** (ValueAlignmentDifferenceSource):
- Automatically loads rules (dynamic)
- Computes alignment every 30 seconds
- Generates intensity scores (intensity >= 50 counts as high intensity)
- Registers itself with the DifferenceDetector

---

## LLM Tool Configuration

### modify_value_system (Value Modification Tool)

**Permission**: `admin` (only the LLM "large" role may invoke it)

**Modifiable sections**:
- ✅ Basic principles (基本原则)
- ✅ Code of conduct (行为准则)
- ✅ Evolution records (进化记录)

**Quality control**:
- Minimum 8 characters
- Avoid generic phrases ("no modification needed", "can stay the same")
- Prohibition rules must be >= 15 characters
- New rules with > 60% similarity to existing rules are filtered out

**Usage example**:
```python
await tool_modify_value_system(
    action="add_rule",
    section="行为准则",
    rule="输出要简洁有力，避免冗长叙述",
    reason="检测到过长回复模式"
)
```

### get_current_values (Query Current Rules)

**Permission**: `query` (any role may invoke it)

**Format options**:
- `full`: full text
- `compact`: condensed version (recommended)
- `sections`: listed by category

### get_evolution_log (Query Modification History)

**Permission**: `query` (any role may invoke it)

**Purpose**: audit trail for reviewing the rule evolution history

---

## Project Operating Guidelines (Not Modifiable by AI; User-Configurable)

Project guidelines are defined in `config/project_guidelines.yaml`; **the AI cannot modify them via tools**:

**Configuration file** (`config/project_guidelines.yaml`):
```yaml
# Code change guideline
代码变更: 提交前必须通过本地测试和 linting，遵循 git commit 规范

# Database modification guideline
数据库修改: 数据库变更必须附带迁移脚本，不可直接修改生产数据

# API interface change guideline
API 变更: API 接口变更必须更新文档，确保向后兼容或明确指出破坏性变更

# ... other guidelines

# [Optional] Custom guideline (uncomment to enable)
# 性能优化: 性能敏感代码必须进行基准测试
```

**Permission isolation**:
- ❌ **Cannot be modified by AI**: there is no modify_project_guidelines tool, ensuring security constraints cannot be bypassed
- ✅ **Can be modified by users/administrators**:
  - Directly edit the `config/project_guidelines.yaml` file
  - **No application restart required** — takes effect automatically at the next LLM processing round
  - No code changes needed, only file editing permission

**Modification workflow**:
```bash
# 1. Edit the configuration file
vi config/project_guidelines.yaml

# 2. Save the file (no application restart required)

# 3. SecurityExpert automatically loads the new guidelines at its next initialization
```

**Loading mechanism**:
- SecurityExpert calls `_load_project_guidelines()` at initialization
- Reads the guidelines dynamically from the YAML file
- Falls back automatically to built-in default guidelines if loading fails
- The entire loading process is written to logs

**Why not hardcode**:
- Project guidelines are the system's security boundary, preventing the AI from modifying its own constraints
- Values can be modified dynamically (supporting AI self-adaptation)
- **Configurable guidelines** allow users to adjust them without modifying code
- Lowers maintenance cost and improves flexibility

---

## File Mapping

| Configuration Item | File | Description |
|--------|------|------|
| VALUE_ALIGNMENT_HANDLER_ENABLED | config/settings.py | Passive monitoring switch |
| PERCEPTION_ENABLED | config/settings.py | Perception system switch |
| DIFFERENCE_DETECTOR_ENABLED | config/settings.py | Difference detection switch |
| Value rules | modules/thinking/evolution/prompts/core_values.txt | Dynamic rule definitions |
| PROJECT_GUIDELINES | modules/thinking/experts/pre_gen_experts.py | Hardcoded project guidelines |

---

## Full Workflow

```
[User request]
    ↓
[SecurityExpert (always enabled)]
  ✅ Checks security risks
  ✅ Returns project operating guideline requirements

[RuleCompliancePerception (real-time, based on PERCEPTION_ENABLED)]
  ✅ Detects norm violations
  ✅ Generates perception events

[LLM (same turn)]
  ✅ Sees security checks + project guidelines + norm violations
  ✅ Adjusts its output

[DifferenceDetector (passive, based on both switches)]
  ✅ Tracks alignment trends
  ✅ Queried periodically by the LLM
  ✅ Decides whether to modify rules (modify_value_system)
```

---

## Common Configuration Scenarios

### Scenario 1: Work Mode

```env
VALUE_ALIGNMENT_HANDLER_ENABLED=True
PERCEPTION_ENABLED=True
DIFFERENCE_DETECTOR_ENABLED=True
```

**Characteristics**:
- Full tool delegation, without value-system constraints
- Security checks + project guidelines always enabled
- Real-time alerts on norm violations
- Background trend monitoring for future improvement
- Supports self-correction and rule evolution

### Scenario 3: Security Checks Only

```env
COMPANION_MODE=False
VALUE_ALIGNMENT_HANDLER_ENABLED=False
PERCEPTION_ENABLED=False
DIFFERENCE_DETECTOR_ENABLED=False
```

**Characteristics**:
- Minimal mode; only security review is performed
- Project guidelines always in effect
- No perception, no difference detection, no background monitoring

---

## Environment Variable Example

A complete `.env` configuration file:

```env
# Run mode
COMPANION_MODE=False
APP_ENV=production
LOG_LEVEL=INFO

# Model configuration
LARGE_MODEL_API_KEY=sk-xxx
LARGE_MODEL_NAME=deepseek-v4-flash
SMALL_MODEL_API_KEY=xxx
EXPERT_MODEL_NAME=qwen2.5-7b-instruct

# Perception & detection
PERCEPTION_ENABLED=True
DIFFERENCE_DETECTOR_ENABLED=True
VALUE_ALIGNMENT_HANDLER_ENABLED=True

# Proactive outreach
PROACTIVE_OUTREACH_ENABLED=True
PROACTIVE_OUTREACH_COOLDOWN_MINUTES=15
```

---

## Debugging & Monitoring

### View Current Configuration

```python
from config.settings import settings

print(f"Run mode: {'companion' if settings.COMPANION_MODE else 'work'}")
print(f"Perception system: {settings.PERCEPTION_ENABLED}")
print(f"Value monitoring: {settings.VALUE_ALIGNMENT_HANDLER_ENABLED}")
```

### View Current Rules

```python
from infra.tool_manager.tools.value_tools import get_current_values

rules = get_current_values(format="compact")
print(rules)
```

### View Modification History

```python
from infra.tool_manager.tools.value_tools import get_evolution_log

log = get_evolution_log(limit=20)
print(log)
```

### View Alignment Statistics

```python
from modules.perception.difference import get_detector

detector = get_detector()
differences = detector.get_recent_differences(source_type="value_alignment", limit=10)
for d in differences:
    print(f"Alignment: {d.payload.get('alignment_score')}, Intensity: {d.intensity}")
```

---

## Troubleshooting

### Norm Violations Are Not Being Detected

- ✅ Check that `PERCEPTION_ENABLED=True`
- ✅ Check that a relevant rule exists in core_values.txt
- ✅ Check the logs: `logs/perception.log` or `logs/rule_compliance_perception.log`

### The LLM Cannot Invoke modify_value_system

- ✅ Check permissions: the tool requires the `admin` permission; only the large LLM may invoke it
- ✅ Check rule quality: it may have been blocked by the quality gate
- ✅ Check the logs: `logs/value_tools.log`

### Background Monitoring Is Not Updating

- ✅ Check that `VALUE_ALIGNMENT_HANDLER_ENABLED=True`
- ✅ Check that `DIFFERENCE_DETECTOR_ENABLED=True`
- ✅ Wait for the difference detector's 30-second scan cycle

---

**Updated**: 2026-06-06  
**System Version**: Phase 4 complete  
**Owner**: AI System
