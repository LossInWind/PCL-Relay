# OpenCode 接入 PCL Relay：配置、验证与排错

适用范围：OpenCode 当前 V2 配置格式；本教程在 2.0.16 上验证。
核验日期：2026-09-25。以下使用 OpenAI-compatible Chat Completions，
不是 OpenAI 官方账号登录，也不是 Codex 专用 Responses 地址。

## 1. 先确认地址与使用范围

打开 **PCL Relay → 模型与 Agent → API 接入信息**：

| 项目 | 怎么填 |
| --- | --- |
| 提供商类型 | OpenAI 兼容 / Chat Completions |
| 提供商 ID | `pcl-relay` |
| 显示名称 | `PCL Relay` |
| Base URL | 从 App 复制，通常以 `:15722/v1` 结尾 |
| API Key | 当前 Relay 无客户端密钥校验；客户端要求时填 `pcl-tailnet` |
| 请求头 | 当前配置不需要额外请求头 |
| 模型 ID | 从目录复制原始 ID，例如 `DeepSeek-V4-Pro`；不加 `pcl/` |

`pcl-tailnet` 是占位文本，不是真实密钥。不要填写 Codex 登录凭据，也不要
把中转站保存的 PCL 上游密钥复制到客户端。如果将来网关启用独立认证，应以
当时 App 显示的信息为准。

使用者必须已加入有权访问中转站的 Tailnet。`127.0.0.1` 仅代表运行
OpenCode 后端的那台机器，不能把它当成另一台电脑的地址。
地址和 Key 只配置一次，同一提供商可以登记多个模型。

## 2. 找到真正生效的配置文件，先备份

macOS 和 Linux 常见全局位置：

```text
~/.config/opencode/opencode.json
```

也支持 `opencode.jsonc`。已有哪个就修改哪个，不要额外创建相互冲突的文件。
Mac 可以在访达按 `⌘⇧G`，输入 `~/.config/opencode/`，使用代码编辑器打开。
修改前复制一份带日期的备份；备份可能含其他提供商的密钥，不要上传公共仓库。

项目中的 `opencode.json(c)` 或 `.opencode/opencode.json(c)` 可能覆盖全局配置。
如果某个项目不生效，先检查该项目和父目录的覆盖项，不要反复改全局文件。
使用远程 OpenCode 后端时，修改后端所在机器、相应用户的配置；只改 Mac
文件不一定影响远程后端。

不要删除或覆盖已有的其他提供商、`skills`、插件、权限、默认模型等设置。
不需要改会话数据库、不需要删除历史、不需要退出 Codex 账号。

## 3. 已接入 PCL，只修图片历史导致的 400

这是已有用户最小且推荐的修改。在
`providers → pcl-relay → models` 中找到下面两个模型，补充 `capabilities`：

```json
"DeepSeek-V4-Pro": {
  "name": "DeepSeek V4 Pro",
  "capabilities": {
    "tools": true,
    "input": ["text"],
    "output": ["text"]
  }
},
"GLM-5.2": {
  "name": "GLM 5.2",
  "capabilities": {
    "tools": true,
    "input": ["text"],
    "output": ["text"]
  }
}
```

上面是 **models 内部片段**，不是完整文件。已有 `settings`、`variants` 等
字段要保留；已有 `capabilities` 时修改该对象，不要创建重复字段。
如果你的提供商 ID 不是 `pcl-relay`，修改实际对应的那一项，不要再建重复提供商。

原因：本次 PCL 实测确认这两个部署拒绝图片输入。OpenCode 对未知自定义模型
可能默认支持图片，仅填写名称会把旧会话中的图片继续发给文本模型。
声明输入能力后由 OpenCode 自己处理；Relay 不偷偷删除输入，也不修改原始历史。
**图片没有传给文本模型，不等于模型理解了图片。** 必要时先提供文字摘要或使用
经过验证的视觉模型。

## 4. 新用户：添加一个提供商

优先使用 App 的“复制 OpenCode 配置补充（当前版）”。它是需合并的配置片段，
不是让你替换整个旧文件。当前模板启用上述两个已验证文本模型；其他目录项列出
但暂禁用，避免客户端把默认能力当成事实。这不限制网关本身可调用的模型。

下面给出只有两个已验证模型的完整最小示例。把 `RELAY_HOST` 替换为 App
显示的实际主机，或者直接用 App 中的完整 Base URL：

```json
{
  "providers": {
    "pcl-relay": {
      "name": "PCL Relay",
      "package": "@opencode/ai/providers/openai-compatible",
      "settings": {
        "baseURL": "http://RELAY_HOST:15722/v1",
        "apiKey": "pcl-tailnet"
      },
      "models": {
        "DeepSeek-V4-Pro": {
          "name": "DeepSeek V4 Pro",
          "capabilities": {
            "tools": true,
            "input": ["text"],
            "output": ["text"]
          }
        },
        "GLM-5.2": {
          "name": "GLM 5.2",
          "capabilities": {
            "tools": true,
            "input": ["text"],
            "output": ["text"]
          }
        }
      }
    }
  }
}
```

注意使用复数 `providers` 和当前包名；不要混用旧版教程字段。
没有配置文件时可用完整示例；已有配置时只合并 `providers.pcl-relay`。
JSON 要使用英文双引号，普通 `.json` 不要加注释或尾逗号。

## 5. 推理强度和其他模型

能力声明不会关闭工具调用，也不会替换已有推理设置。优先保留客户端里已有的
High 等选项。若需为某模型指定默认 High，可在那个模型对象中合并：

```json
"settings": {
  "reasoningEffort": "high"
}
```

本次已验证 GLM-5.2 和 DeepSeek-V4-Pro 携带 High 的普通及流式工具请求可以完成。
这只证明参数可提交，不证明上游一定改变了推理预算或质量。
不要照搬其他模型的强度名称、上下文长度或价格。

新增其他模型时，先刷新 Relay 模型目录，使用精确 ID；能力按该部署的实际测试
补充，不能从模型名字中的“VL”等字样直接推断。Kimi-K3 文本与工具调用已通过
独立测试，图片能力尚未在本轮独立验证；不要把未知写成已支持。

## 6. 保存后的验证：从便宜、无副作用开始

1. 确认编辑器没有 JSON 错误，保存配置。
2. 等当前任务结束后重新打开相应 OpenCode 窗口/后端，使配置重新加载。
   不要强制结束正在工作的进程；原会话保留。
3. 在模型选择器启用/选择 PCL Relay 下的具体模型。若看不到，检查提供商 ID、
   模型是否被禁用、项目覆盖配置以及该项目的提供商访问策略。
4. 新建一个测试会话，让它“只回复 OK”。这会消耗少量模型额度。
5. 在临时空目录测试“只查看当前目录，不写文件、不安装软件”，确认能发起并
   完成工具调用。工具权限仍以用户设置为准，不需要关闭权限保护。
6. 最后再回到原项目。带图片历史切换文本模型时，确认不再报图片不兼容，
   并检查回答是否依赖已省略的图片信息。

可选的无生成检查（把地址替换成实际地址）：

```bash
curl --noproxy '*' --connect-timeout 5 --max-time 15 \
  'http://RELAY_HOST:15722/v1/models'
```

这是一次临时的直连接口检查，不修改系统代理。目录能打开只证明接口可达，
不能替代真实生成和工具调用验收。

## 7. 常见错误分别处理

| 现象 | 优先检查 |
| --- | --- |
| `is not a multimodal model` / 不支持图片输入 | 实际生效的模型是否声明 `input: ["text"]`；项目是否覆盖了它 |
| 只有 HTTP 400 | 记录时间、模型、请求编号；运行中的网关可能仍是旧版，安装新版不代表服务已重启 |
| 401 / 403 | 确认是客户端策略、网关还是 PCL 上游拒绝，不要盲目换 Codex 登录文件 |
| 429 | 上游限流或额度限制；检查并发与用量，避免反复重试放大流量 |
| 502 / 503 / 超时 | 检查对应时间的 Relay 和上游记录，不能仅凭状态码认定 Mac 网络坏了 |
| “此服务器上无法使用自定义提供商” | 检查实际 OpenCode 后端版本、当前配置格式与项目策略；不是图片能力错误 |
| Kimi 说“现在开始写”就结束 | 区分正常 `stop`、缺失结束标记与连接断开；不能都归为断流 |

Kimi 停滞时：保留原会话，核实已完成内容与真实文件，整理剩余任务，在独立会话
验证一个小任务，再逐步接续。不要靠无限自动“继续”、强制工具调用或反复要求
一次输出巨型脚本来掩盖问题。正常结束但没执行，与流损坏是两类问题。

## 8. 回退与安全

需要回退时恢复备份中的对应字段；如修改后还有其他设置变化，不要整文件覆盖，
只撤销本次增加的字段。重新加载配置即可，不清理会话数据库。
不要向公共网络开放当前无客户端认证的 Relay，不分享真实 API Key，排错时
只分享脱敏错误、模型 ID、时间和请求编号，不分享完整会话或认证文件。

## 官方参考

- [配置位置、格式与覆盖顺序](https://opencode.ai/v2/docs/config)
- [模型能力、变体与默认假设](https://opencode.ai/v2/docs/models)
- [提供商接入](https://opencode.ai/v2/docs/providers)
