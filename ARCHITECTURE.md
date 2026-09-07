# PCL Relay 架构

PCL Relay 只有两条主线：转发模型请求的数据面，以及配置设备与客户端的控制面。新增功能应先判断属于哪条主线，不跨层写“临时补丁”。

## 数据面

```text
Codex Desktop / VS Code
        │
        ▼
native_router.py            本机回环路由，决定官方 GPT 或 pcl/<model>
        │
        ├── 官方 GPT ──────► ChatGPT Codex
        │
        └── PCL ───────────► gateway.py
                                  │
                                  ├── responses_protocol.py  请求、压缩与响应语义
                                  ├── responses_stream.py    SSE 增量事件
                                  └── PCL 内网 API
```

- `native_router.py` 是唯一的模型路由决策点。
- `gateway.py` 只处理 Tailnet HTTP 边界、门户代理、拓扑心跳和请求编排。
- `responses_protocol.py` 负责 Responses 与 Chat Completions 的语义转换。
- `responses_stream.py` 只负责流式事件状态机，不管理网络或配置。
- 工具调用跨越 Chat Completions/Responses 边界前必须完整缓冲并按声明校验；任何路径都不得把未验证参数写入 Codex 会话历史。
- PCL API Key 只由中转站的数据面读取，不能进入客户端配置、拓扑或日志。

## 控制面

```text
macOS UI
   │
   ▼
AppModel + feature extensions
   │
   ▼
cli.py
   ├── client_config.py      安装、Codex 配置、用户级路由服务、诊断
   ├── relay_discovery.py    只读发现 Tailnet 与候选中转站
   ├── model_detection.py    模型目录及能力检测
   ├── remote_clients.py     远端状态、共识与客户端部署
   ├── direct_clients.py     PCL 本地直连接入
   ├── bridges.py            无法直连时的二次桥接
   └── release_updater.py    GitHub Release 更新
```

- `http_client.py` 是无业务状态的最小 HTTP 工具，不依赖其他功能模块。
- 只有 `client_config.py` 可以修改 `~/.codex` 和本机路由服务。
- `relay_discovery.py` 与 `model_detection.py` 不写 Codex 配置。
- `cli.py` 只做命令编排，不复制底层业务逻辑。

## macOS 应用

```text
PCLCodexManagerApp
   ├── MenuBarPanel
   └── AppShellView
       ├── NetworkView
       │   ├── TopologyComponents
       │   └── DeviceManagementComponents
       ├── ModelsAgentsView
       └── PortalView
```

- `AppModel.swift` 保存共享状态、启动流程和 CLI 边界。
- `State/AppModel+*.swift` 按更新、模型、网关、拓扑划分用户动作。
- `Views/` 按产品页面拆分；页面内部组件留在对应功能文件，跨页面组件才进入 `SharedComponents.swift`。
- `Services/` 隔离 macOS 系统能力，例如登录项；界面不得直接调用 `launchctl`。
- UI 不直接执行 shell，也不自行推断网络拓扑；它只展示 CLI 返回的事实。

## 修改规则

1. 改模型去向：只改 `native_router.py`，并补路由测试。
2. 改 Responses、推理、工具或压缩协议：改 `responses_protocol.py`；涉及增量事件时再改 `responses_stream.py`。
3. 改设备是否可达：改发现或远端客户端模块，不在 Swift 中写设备名称特例。
4. 改 Codex 文件或用户服务：集中在 `client_config.py`，写入必须可重复、可回滚、原子化。
5. 新增 UI 操作：View → 对应 AppModel 扩展 → CLI；不绕过任何一层。
6. 同一事实只保留一个来源。注册表、实时健康检查和完整心跳轮次的优先级必须明确，不能由多个页面各算一遍。
7. 发布版本只读取 `pcl_codex_bridge/VERSION`；源码 plist 和其他配置不得保存第二份手工版本号。远端更新先尝试校验过的 GitHub Release，失败后才接收当前 Mac 的同版本归档。
8. Codex 集成总开关只管理 PCL Relay 自己的路由、配置和角色；关闭状态必须持久化，且不得修改官方登录凭据或用户自有配置。
9. 官方路由在响应头之前必须有短硬截止时间并支持调用方取消；响应头发送后不得再走 JSON/HTTP 错误分支。代理健康只能由真实官方 HTTPS 响应证明，不能用端口监听代替。
10. PCL Relay 不管理 Clash、订阅、代理节点、系统代理或普通网络修复。macOS 上只读取 Haichen Services 发布的 `official-proxy.json`；其他系统只使用显式环境变量或已保存配置。任何情况下都不得扫描常见代理端口或自行切换出口。

## 网络事实、默认值与责任

| 事项 | 是否会变化 | 唯一负责人 | PCL Relay 的行为 |
|---|---|---|---|
| Clash/VPN、订阅、出口节点、本机代理端口 | 会随网络与订阅变化 | Haichen Services | 只读消费发布端点并做官方 HTTPS 实测；不猜端口、不切节点 |
| PCL API 可直连性、Tailnet 节点、时延、桥接可用性 | 会随设备与网络变化 | PCL Relay | 心跳实测并按“本机直连 PCL → 直连中转站 → 二次桥接”选最短已验证路径 |
| 官方 Codex provider、GPT 模型目录、登录凭据 | 默认不变 | Codex/用户 | 开关 PCL 集成时保留；只增加或撤销 Relay 自己的配置 |
| 中转站、启用模型、API Key | 用户选择；可更新 | 用户通过 PCL Relay | 保存选择、检测能力、最小权限部署；API Key 只留在中转站 |
| Tailscale 入网与系统级网络权限 | 用户动作 | 用户/系统 | 给出明确诊断，不擅自更改系统级网络 |

运行时不得把“端口正在监听”当成链路可用。官方 GPT 以真实 HTTPS 响应为准，PCL 模型以目录、普通响应、流式响应和工具调用测试为准。可变事实必须重新探测；默认不变的官方配置不得被网络恢复逻辑重写。失败在首包截止时间内返回明确错误，由所属应用恢复自己的那一层，禁止跨层无限重试。

## 验收门槛

每次结构或路由变更至少通过：

```bash
ruff check pcl_codex_bridge tests
python3 -m unittest discover -s tests
swift test
```

发布前还需验证应用签名、本机回环端口与 Codex 配置一致、中转站健康，以及官方 GPT 路由未被改变。
