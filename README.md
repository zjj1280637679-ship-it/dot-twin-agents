# dot-twin-agents · dot 同文分身

面向支持原生完整历史分叉的 dot 宿主。它是 **协作协议插件**，不是另一个模型后台。
共享当前可用语境，任务只传差异；分身异步执行，把有依据的新发现交回来。
重点是减少重复交代背景的输出 token、减少语义转述损失，不是突破额度。

## 三层边界

- **宿主**：真正复制当前可用上下文、执行并发、管理权限和计费。
- **Skill**：选择何时分身、差异式任务目标、回传与完成验收。
- **可选纯 helper**：验证输入并生成原生 spawn 参数；不执行调用、不读历史或密钥。

没有原生 full-history fork 时，本插件明确不可用。不得把压缩简报冒充同文。
对话状态独立不等于磁盘隔离；共享目录仍需单写者或独立工作树。
平台并发上限、权限及收费照旧，插件不会增加配额或保证缓存节省。

## 最简使用

直接读取 `plugin/skills/dot-twin/SKILL.md`，在宿主支持时原生调用：

```javascript
collaboration.spawn_agent({
  task_name: "verify_delivery",
  message: "核对交付是否满足根目标；只报告新发现和证据。",
  fork_turns: "all"
})
```

继续主线，通过宿主消息及最终结果回收。无需执行仓库代码。
可选 helper（Python 3.10+，无运行时第三方依赖）：

```sh
printf '%s' '{"task_name":"verify_delivery","delta":"核对最后一公里，保留依据与分歧。","mode":"verify"}' | python -m dot_twin prepare
```

输出只有 `task_name/message/fork_turns`；人工或可信宿主决定是否真正调用。
`mode` 可选 `open`（默认）、`directed`、`verify`、`consolidate`。
没有历史文本、API key、workspace、权限开关或并发覆写参数。

## 插件包与验证

```sh
python -m unittest discover -s tests -v
python scripts/build_plugin.py
```

生成 `dist/dot-twin-agents-0.1.0.zip`。包只含 manifest、一个自包含 Skill 和 MIT
许可证；无 hooks、MCP、安装脚本或后台服务。导入需使用宿主官方自定义插件入口，
上传/安装本身不证明原生 fork 可用。发布说明应区分包验证、实际安装和真实执行。

- [接口与组合契约](docs/contract.md)
- [验证与局限](docs/verification.md)
- [上游来源](NOTICE.md)

MIT。上游 `codex-twin-agents` 保留版权，固定参考 commit
`8974bb67582be0d28309df4a47090a53157a3ee9`。
