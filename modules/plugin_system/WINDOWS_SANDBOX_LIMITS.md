# Windows 沙箱限制

本模块对 Windows 支持采取保守策略。

## Windows Job Object 的含义

Windows Job Object 可以施加资源限制。
在本插件系统中，它不被视为第三方生产插件所需的强文件系统、网络、namespace 或 syscall 沙箱。

它不能被用作证据来证明任意第三方插件代码已经被强隔离，尤其不能证明隔离了：

- 宿主文件系统读取
- 宿主文件系统写入
- 直接网络访问
- 进程/syscall 滥用
- 环境变量泄露
- 宿主临时目录泄露

## 生产规则

第三方生产插件需要来自受认可目标环境的强沙箱证据。
在 Windows 上，除非外部或目标环境的沙箱证据能独立于 Windows Job Object 证明隔离能力，否则生产策略仍然会阻断。

可接受的强沙箱证据必须满足 `PRODUCTION_EVIDENCE.md` 中的生产沙箱证据契约。

## 诊断命令

下面这些命令可以报告当前本地平台状态：

```bash
python -m modules.plugin_system.status --json
python -m modules.plugin_system.doctor --json
python -m modules.plugin_system.production_policy_check --json
```

Windows 下预期措辞应该是 warning/blocking，而不是 production-ready。
模块应该继续说明 Job Object 只提供资源限制。

## 推荐部署形态

生产第三方插件建议使用下面任一形态：

- 目标 Linux 环境，并提供已强制执行的 bubblewrap 证据
- self-hosted Linux 生产环境，并通过必要验证检查
- 外部容器、VM 或隔离服务，并能产出可接受的沙箱证据

`modules/plugin_system` 不实现这类外部隔离服务。
