# 感知系统配置修改说明

## 修改内容总结

**实现**：感知系统主系统默认关闭，子系统全部默认启用（但主系统关闭时不生效）

---

## 配置层级

### 第一层：全局配置（settings.py）

```python
# config/settings.py
PERCEPTION_ENABLED: bool = False  # ← 改为默认 False（关闭主系统）

# 子系统开关（始终保持 True，由主系统控制其是否生效）
PERCEPTION_FILE_ENABLED: bool = True
PERCEPTION_DIALOG_ENABLED: bool = True
PERCEPTION_SCREEN_ENABLED: bool = True
PERCEPTION_VOICE_ENABLED: bool = False
PERCEPTION_INTERNAL_ENABLED: bool = True
```

### 第二层：感知管理器（manager.py）

```python
# modules/perception/manager.py

# 主系统（PerceptionManager）
# - 默认 enabled=False（不自动启动）
# - 初始化时从配置读取子系统状态
# - 启动时重新加载配置并应用

class PerceptionManager:
    def __init__(self, enabled: bool = False):  # ← 默认关闭
        self.enabled = enabled
        self.subsystems_config = self._load_subsystem_config()
        # 三个子系统按配置启用

    def start_monitoring(self):
        """启动时，加载配置中的子系统设置"""
        self.enabled = True
        self.subsystems_config = self._load_subsystem_config()
        # 应用子系统状态

    def stop_monitoring(self):
        """停止时，禁用所有子系统"""
        self.enabled = False
        self.file_perception.enabled = False
        self.dialog_perception.enabled = False
        self.screen_perception.enabled = False
```

### 三个子系统

```python
class FilePerception:
    def __init__(self, enabled: bool = True):
        self.enabled = enabled  # ← 子系统可独立启用/禁用

class DialogPerception:
    def __init__(self, enabled: bool = True):
        self.enabled = enabled

class ScreenPerception:
    def __init__(self, enabled: bool = True):
        self.enabled = enabled
```

---

## 行为规则

### 关键原则

1. **主系统默认关闭**
   - 启动应用时，感知系统不自动运行
   - 需要显式调用 `start_monitoring()` 才启动

2. **子系统全部默认启用**
   - 所有子系统配置都默认 `True`
   - 保证启动主系统后所有能用的都启用

3. **主系统控制子系统生效**
   - 主系统关闭时，子系统启用状态无效（不处理事件）
   - 主系统启动时，子系统按配置启用
   - 主系统停止时，所有子系统都禁用

### 状态矩阵

```
主系统状态     文件感知    对话感知    屏幕感知
─────────────────────────────────────────────
关闭（默认）   启用但不生效  启用但不生效  启用但不生效
启动           启用        启用        启用
停止           禁用        禁用        禁用
```

---

## API 使用

### 查看状态

```python
from modules.perception import perception_manager

status = perception_manager.get_status()
print(f"主系统: {status['main_system_enabled']}")
print(f"运行中: {status['running']}")
print(f"子系统: {status['subsystems']}")
# 输出：
# 主系统: False
# 运行中: False
# 子系统: {'file_perception': True, 'dialog_perception': True, 'screen_perception': True}
```

### 启动感知系统

```python
perception_manager.start_monitoring()
# 日志: 开始后台监控（启用的子系统: file, dialog, screen）
```

### 停止感知系统

```python
perception_manager.stop_monitoring()
# 日志: 停止后台监控（所有子系统已禁用）
```

### 个别禁用子系统（可选）

```python
# 如果需要禁用某个子系统
perception_manager.set_subsystem_enabled("screen", False)
perception_manager.start_monitoring()  # 启动时只有 file/dialog
```

---

## 系统启动流程

### API 启动（api/main.py）

```python
# 启动时的行为
if settings.PERCEPTION_ENABLED:  # 现在默认 False
    perception_manager.start_monitoring()
else:
    logger.info("感知系统已禁用（默认）")

# 要启用感知系统，修改配置为 True 或显式调用：
# perception_manager.start_monitoring()
```

### 优先级

1. **全局配置** (`PERCEPTION_ENABLED`)
   - 最优先的控制
   - 修改这里会影响 API 启动

2. **代码调用**
   - `start_monitoring()` / `stop_monitoring()`
   - 可覆盖配置

3. **子系统配置**
   - `PERCEPTION_FILE_ENABLED` 等
   - 仅在主系统启动时生效

---

## 文件修改清单

```diff
config/settings.py
- PERCEPTION_ENABLED: bool = True
+ PERCEPTION_ENABLED: bool = False  # 默认关闭

modules/perception/manager.py
+ PerceptionManager.__init__: 添加 enabled=False, subsystems_enabled 参数
+ PerceptionManager._load_subsystem_config(): 从配置读取子系统状态
+ PerceptionManager.start_monitoring(): 应用子系统配置
+ PerceptionManager.stop_monitoring(): 禁用所有子系统
+ PerceptionManager.get_status(): 获取完整状态
+ PerceptionManager.set_subsystem_enabled(): 个别启用/禁用子系统

+ FilePerception.__init__: 添加 enabled=True 参数
+ DialogPerception.__init__: 添加 enabled=True 参数
+ ScreenPerception.__init__: 添加 enabled=True 参数
```

---

## 验证

### 初始状态验证

```python
pm = PerceptionManager()
assert pm.enabled == False  # 主系统关闭
assert pm.file_perception.enabled == True  # 子系统启用
assert pm.dialog_perception.enabled == True
assert pm.screen_perception.enabled == True
```

### 启动流程验证

```python
pm.start_monitoring()
assert pm.enabled == True  # 主系统启动
assert pm._running == True  # 监控线程运行
assert pm.file_perception.enabled == True  # 子系统生效
```

### 停止流程验证

```python
pm.stop_monitoring()
assert pm.enabled == False  # 主系统关闭
assert pm._running == False  # 监控线程停止
assert pm.file_perception.enabled == False  # 子系统禁用
assert pm.dialog_perception.enabled == False
assert pm.screen_perception.enabled == False
```

---

## 常见问题

### Q: 为什么主系统默认关闭？
A: 感知系统有性能开销（文件监控、屏幕截图等），应该由用户明确启用。

### Q: 为什么子系统默认启用？
A: 一旦启动主系统，所有配置的子系统都应工作，避免需要逐个启用。

### Q: 如何永久禁用某个子系统？
A: 修改 `config/settings.py` 中的对应开关（如 `PERCEPTION_SCREEN_ENABLED = False`）。

### Q: 启动后修改子系统配置需要重启吗？
A: 不需要，调用 `stop_monitoring()` 后重新 `start_monitoring()` 就会重新加载配置。

### Q: 主系统关闭时子系统还会监控吗？
A: 子系统的 `enabled` 标志存在，但监控循环不运行（因为主系统的 `_running` 是 False）。

---

## 时间线

- **修改日期**: 2026-06-09
- **影响范围**: 感知系统启动逻辑
- **向后兼容**: ✓ 旧代码仍然可用，只是默认行为改变

---

**总结**: 感知系统现在**开箱即用但默认关闭**，启动快速可控，子系统配置灵活。
