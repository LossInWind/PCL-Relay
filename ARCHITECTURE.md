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

## 验收门槛

每次结构或路由变更至少通过：

```bash
ruff check pcl_codex_bridge tests
python3 -m unittest discover -s tests
swift test
```

发布前还需验证应用签名、本机回环端口与 Codex 配置一致、中转站健康，以及官方 GPT 路由未被改变。
