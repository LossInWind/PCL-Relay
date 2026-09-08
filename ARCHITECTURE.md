# PCL Relay 架构

PCL Relay 是跨平台的模型路由产品，不是网络管理产品。macOS 与 Linux 使用同一套 Python 控制面和同一份固定 OpenCodex 数据面；macOS 额外提供 SwiftUI 外壳，Linux 提供可移植命令行包。

## 应用边界

```text
外部基础设施层（独立应用或用户配置）
  ├── 本机与服务器连通性、可达性
  ├── Clash / VPN / 订阅 / 节点选择
  ├── Tailscale / SSH 隧道 / 端口映射
  └── 文件映射、远程挂载

PCL Relay（模型路由层）
  ├── Codex ↔ OpenCodex 兼容接入
  ├── provider / model catalog / pcl 子 Agent
  ├── official 与 pcl/* 的请求分流
  ├── PCL gateway ↔ PCL API 的标准协议边界
  └── Relay 实例心跳与模型拓扑同步
```

- PCL Relay 接收已经可用的 endpoint 或监听地址，不发现、不建立、不修复底层网络。
- PCL Relay 与任何网络管理 App 之间没有状态文件、发布/订阅、进程调用或配置读取协议；二者独立安装、独立升级、独立失败。
- 外部基础设施不解析模型协议、不修改 Codex provider，也不承担 PCL 请求转换。
- 服务器部署只要求明确 endpoint 已可达，不要求任何特定网络管理 App 与其同进程或同平台。

## 数据面

```text
Codex Desktop / VS Code
        │
        ▼
vendor/opencodex（固定完整上游，127.0.0.1）
        │
        ├── 官方 GPT ──────► ChatGPT Codex
        │
        └── pcl/<model> ───► gateway.py ───► PCL 内网 API
```

- `vendor/opencodex` 是本机唯一数据面；版本、commit、tree 和许可由 provenance manifest 固定。
- 官方透传、provider 路由、模型目录、Responses SSE、取消、重试、压缩、工具调用转换和 Codex journal/restore 全部使用 OpenCodex 上游实现。
- macOS 数据面使用 OpenCodex 已实现的 Bun 1.3.14 HTTP/SSE 上游回退；客户端 Responses WebSocket 保持开启。PCL Relay 不实现 WebSocket/SSE 互转，也不在失败后重复提交。
- PCL provider 使用 OpenCodex `openai-chat` adapter；gateway 返回 Chat Completions，不能再承担 Chat→Responses 转换。
- `gateway.py` 只拥有 PCL API 调用、受限门户代理和自身管理接口。监听地址由部署者显式提供；它不调用 Tailscale、SSH 或 VPN 工具。
- `native_router.py`、`official_transport.py`、`responses_protocol.py` 和 `responses_stream.py` 仅保留为迁移期旧会话回滚路径，不得承接新功能。
- PCL API Key 只由中转站读取，不能进入客户端配置、拓扑或日志。

## 控制面

```text
macOS UI
   │
   ▼
AppModel + feature extensions
   │
   ▼
cli.py
   ├── opencodex_sidecar.py  provenance、暂存及上游 CLI 编排
   ├── client_config.py      旧标记块交接和失败回滚
   ├── model_detection.py    模型目录及能力检测
   ├── models.py             endpoint、provider 和模型选择
   ├── routes.py             gateway 目录与事务切换
   ├── topology_sync.py      Relay 心跳、版本与多端模型拓扑同步
   ├── gateway.py            PCL 上游协议边界
   └── release_updater.py    GitHub Release 更新
```

- 本机 Codex 注入、目录同步、状态判定和恢复由 OpenCodex CLI/API 唯一负责。
- `client_config.py` 只能在一次性交接时删除自己拥有的旧标记块并原子回滚，不能生成新的 Codex 路由。
- `relay_discovery.py`、`remote_clients.py`、`direct_clients.py` 和 `bridges.py` 是迁移期旧代码，不得由正式 CLI、App 或发布流程导入；网络与文件能力迁出后再单独移除。
- `model_detection.py` 不写 Codex 配置。
- `topology_sync.py` 只同步白名单中的 gateway 目录、当前 gateway、Agent 模型选择和逻辑版本；peer 地址与 token 文件只保存在本机，绝不进入同步文档。
- OpenCodex 的显式 `proxy/noProxy` 是可选的设备本地高级设置：PCL Relay 只调用上游 CLI 的 show/set/unset/validate/memory/restart 并在配置失败时回滚，不实现解析、发现或容灾，也不把它同步到其他设备。由于该设置由 OpenCodex 在启动时读取，空闲进程通过上游进程绑定的 drain/restart 契约应用；活动请求存在时仅保留设备本地待应用标记。
- Relay 同步默认使用显式登记的 Tailscale IPv4/MagicDNS `http://host:15726`，不调用 Tailscale CLI、不扫描节点。监听端只接受 loopback/Tailnet 来源或正确 bearer token。`pcl-relay-topology/1` 心跳和同步控制面与模型请求数据面分离；离线 peer 不阻塞本机或官方会话。
- 拓扑更新推送只经 Relay/Tailscale 控制面发送并持久化版本化 Release offer 和三个正式平台资产的不可变清单。在线节点立即领取，离线节点在恢复心跳后补领，并用 offer digest 记录已接受状态以避免重启前重复安装。接收节点先从 GitHub 下载与本平台匹配的资产；GitHub 失败后才依次向本机已登记的 Relay peers 请求同版本、同平台的 verified cache。peer 只流式提供已重新核验 SHA-256 的缓存，接收端仍按 offer 的大小和 SHA-256 校验，并继续执行 macOS 签名或 Linux OpenCodex provenance/Bun 校验。控制面更新不使用 SSH，也不重启模型数据面。
- 裸节点可由用户在本机明确登记一个 SSH alias 后执行一次引导；15726 endpoint 默认从该 alias 的有效 `HostName` 自动推导，也允许用户覆盖。用户还可显式点击“从 SSH 配置导入”，此时只读取字面的 `Host` 条目、用 `ssh -G` 解析且按 HostName 去重，不建立远端连接、不读取密钥；除此之外不得枚举设备。引导只允许识别 macOS/Linux 平台、获取对应签名 Release、校验、暂存 PCL Relay 并启动同步接收端；它不扫描 Tailnet、不创建或修复 SSH/Tailscale，也不复制凭据。目标节点仍先直接下载 GitHub，失败后才由发起节点传输其 verified cache。接收端心跳通过后立即转为普通 Relay peer，后续更新不再依赖 SSH。
- `cli.py` 只做命令编排，不复制底层业务逻辑。

## macOS 应用

```text
PCLCodexManagerApp
   ├── MenuBarPanel
   └── AppShellView
       ├── RoutingView
       ├── ModelsAgentsView
       └── PortalView
```

- `AppModel.swift` 保存共享状态、启动流程和 CLI 边界。
- `State/AppModel+*.swift` 按更新、模型、网关、拓扑划分用户动作。
- `Views/` 按产品页面拆分；页面内部组件留在对应功能文件，跨页面组件才进入 `SharedComponents.swift`。
- `Services/` 隔离 macOS 系统能力，例如登录项；界面不得直接调用 `launchctl`。
- UI 展示 provider/endpoint/request flow、Relay 同步边和明确登记的软件部署目标，不展示或操作 Tailnet 设备图、VPN、SSH 隧道、桥接和文件映射。

## 修改规则

1. 改模型去向、Responses、SSE、重试、取消、工具或压缩协议：先升级并验证固定 OpenCodex 上游；禁止在 PCL Relay 重新实现。
2. PCL 适配只允许通过 OpenCodex provider/adapter 配置和 gateway 的标准 Chat Completions 边界完成。
3. 改设备是否可达、VPN、代理节点、隧道或文件映射：属于外部基础设施，不得改 PCL Relay。
4. 改 Codex 文件或用户服务：调用 OpenCodex 原生命令；PCL Relay 只做交接前备份和失败回滚。
5. 新增 UI 操作：View → 对应 AppModel 扩展 → CLI；不绕过任何一层。
6. 同一事实只保留一个来源。注册表、实时健康检查和完整心跳轮次的优先级必须明确，不能由多个页面各算一遍。
7. App 启动、刷新和升级只能暂存已校验的新 runtime；健康数据面和 Codex 配置不得自动切换。
8. Codex 集成开关必须先通过 OpenCodex readiness；停用只 restore，不停止 sidecar。切换失败必须恢复交接前配置。
9. 官方路由行为、超时和重试预算完全服从 OpenCodex，不由 PCL Relay 添加第二套策略。
10. PCL Relay 不管理 Clash、订阅、代理节点、系统代理、Tailscale、SSH 隧道、端口映射、文件映射或普通网络修复，也不读取任何其他 App 发布的状态。不得扫描端口、枚举网络节点或自行切换出口；唯一允许的 SSH 行为是对用户逐项明确登记的 alias 执行固定、不可扩展的 PCL Relay 首次安装流程。
11. OpenCodex runtime release id 必须包含传输运行时版本。当前固定 Bun 1.3.14，使 canonical ChatGPT 上游使用 OpenCodex 官方 HTTP/SSE fallback；不得在 PCL Relay 内复制或修改上游传输实现。
12. macOS App 发现到的 Codex CLI 只能作为 OpenCodex `CODEX_CLI_PATH` 输入；runtime 校验、版本选择、持久化和 catalog 生成均由 OpenCodex 负责。
13. 官方父会话调度 PCL 子 Agent 使用 OpenCodex 的 v2 hybrid 配置：`multiAgentMode=v2` 且 `keepNativeChatGptOnV1=true`；PCL Relay 不解密或改写子任务。
14. macOS 与 Linux 发布物必须包含同一固定 OpenCodex commit、完整源码、目标平台 Bun 1.3.14 runtime 和 provenance；不得把只有 Python 文件的压缩包称为 Linux 客户端。
15. 网关默认监听 `127.0.0.1`。远端可达地址和允许访问管理面的 CIDR 必须由部署配置显式传入，PCL Relay 不从 Tailscale 自动推断。
16. Relay 同步默认监听 `0.0.0.0:15726`，但无 token 时只接受 loopback 和 Tailscale 地址空间来源；其他网络必须配置 token。节点冲突以 `(counter, origin)` 形成确定的逻辑版本顺序，摘要不一致或运行时切换失败时拒绝提交并回滚 PCL provider。

## 网络事实、默认值与责任

| 事项 | 是否会变化 | 唯一负责人 | PCL Relay 的行为 |
|---|---|---|---|
| Clash/VPN、订阅、出口节点、本机代理端口 | 会随网络与订阅变化 | 外部基础设施 | 不读取状态、不探测、不切换 |
| 本机/服务器可达性、Tailnet、SSH 隧道、端口与文件映射 | 会随设备与网络变化 | 外部基础设施 | 不枚举、不创建、不修复；只使用用户登记的 endpoint，首次安装时验证指定 alias |
| 官方 Codex provider、GPT 模型目录、登录凭据 | 默认不变 | Codex/OpenCodex | PCL Relay 不解析或重写；OpenCodex 负责兼容注入和原生恢复 |
| gateway endpoint、启用模型、API Key | 用户选择；可更新 | 用户通过 PCL Relay | 保存 endpoint、检测模型能力、配置 provider；API Key 只留在 gateway |
| Relay gateway 目录、当前选择、Agent 模型拓扑 | 用户选择；可同步 | PCL Relay | 版本化同步，仅切换 `pcl` provider，不重启数据面 |
| 网络账号、订阅和系统级权限 | 用户动作 | 用户/外部基础设施 | PCL Relay 不提示自动修复或代替用户操作 |

运行时不得把“端口正在监听”当成链路可用。OpenCodex readiness、官方真实请求和 PCL 的目录/普通响应/流式/工具调用测试必须分别验收；默认不变的官方配置不得被网络恢复逻辑重写。

## 验收门槛

每次结构或路由变更至少通过：

```bash
ruff check pcl_codex_bridge tests
python3 -m unittest discover -s tests
swift test
```

发布前还需分别在目标系统验证。macOS 验证应用签名、完整上游测试、sidecar provenance/readiness、官方与 PCL 真实 Codex CLI、工具调用、取消、流中断和并发；Linux 在隔离目录验证对应架构 bundle、同一 provenance、systemd 上游服务生成、官方/PCL/子 Agent/并发/取消。不得用一个平台的结果代替另一个平台。
