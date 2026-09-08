# PCL Relay

一个同时支持 macOS 与 Linux 的 PCL 模型路由器和 Codex 原生子 Agent 管理器。

PCL Relay 只负责 endpoint/provider/model 路由、PCL gateway、Relay 心跳与模型拓扑同步、模型检测、PCL 门户和 Codex 接入，并内嵌完整、固定版本的 OpenCodex 数据面。网络连通、VPN/Clash/Tailscale/SSH 隧道和文件映射属于独立的外部基础设施；PCL Relay 默认收到的网络 endpoint 已经可用，且不与其他 App 交换状态。

源码仓库：[`LossInWind/PCL-Relay`](https://github.com/LossInWind/PCL-Relay)

模块职责、依赖方向和新增功能落点见 [`ARCHITECTURE.md`](ARCHITECTURE.md)。

## 工作方式

### 从旧 Relay 迁移的历史记录

旧版本将会话标记为 `pcl_relay_official`；OpenCodex 恢复官方 `openai` 身份后，Codex 会按 provider 过滤旧记录。启用成功后，Relay 同步迁移自己旧 provider 的 SQLite 索引与 rollout 元数据，先保留数据库及原始 rollout 备份。其他 provider、对话正文和时间顺序不变；正在打开或变动的会话延后处理，不中断任务。重新加载 Codex 窗口可刷新其旧列表缓存。

### 无 systemd 的 Pod

Pod 使用 OpenCodex 自己的 detached `ensure` 启动，不安装宿主机服务，也不承诺容器重建后自动恢复。`.codex` 指向持久卷时，也会在该卷对应的 VS Code 扩展目录发现 Codex 可执行文件。

部分镜像的 `systemctl` 是返回格式不完整的 Python 兼容脚本。此时必须保留 OpenCodex 的配置归属检查，不能伪造成功结果。可从该 Ubuntu 版本的已验证 APT 源仅下载并解包 `systemd` 包，将其中原生 `bin/systemctl` 放在 `~/.local/share/pcl-codex-bridge/systemd-client/bin/systemctl`。Relay 仅为自己的 OpenCodex 子进程优先使用这个客户端，不安装 systemd、不替换 `/usr/bin/systemctl`、不修改全局 PATH。原生客户端报告总线不可达后，由未修改的 OpenCodex 检查磁盘上的服务归属。软件升级保留此独立客户端。

```text
Codex Desktop / VS Code Codex
              |
              v
完整固定版 OpenCodex sidecar（仅 127.0.0.1）
       |                         |
       | 官方 GPT               | pcl/<模型>
       v                         v
ChatGPT Codex 后端       PCL gateway endpoint -> PCL API
```

- 本机路由、官方 ChatGPT Codex 透传、模型目录、Responses SSE、取消、重试、工具调用转换、Codex 注入、journal 和 restore 全部直接运行固定上游 OpenCodex 2.46.0 的完整源码。
- App 固定使用 OpenCodex 文档列出的 Bun 1.3.14 HTTP/SSE 上游回退路径，避免 macOS HTTP 代理可用但 WSS 被出口重置时触发双层重试；Codex→sidecar 的 Responses WebSocket 仍由 OpenCodex 原生实现承接。
- 官方 GPT 保留现有 ChatGPT 登录、模型名称和 Codex 行为；PCL Relay 不解析、重写或自行重试官方请求。
- PCL gateway 通过 OpenCodex 成熟的 `openai-chat` adapter 接入，`pcl/<模型>` 才会发往已配置 gateway endpoint，官方请求不会进入 PCL gateway。
- PCL 模型由 OpenCodex 写入 Codex 主模型目录，并通过它的 `multiAgentMode=v2` + `keepNativeChatGptOnV1` 混合模式成为原生 `spawn_agent` 子 Agent；官方父会话保留可读任务，PCL 子 Agent 继承当前工作区。
- App 只把现有 Codex 可执行文件的发现结果交给 OpenCodex 标准 `CODEX_CLI_PATH` 入口；Codex 版本探测、runtime 持久化、目录抽取和兼容裁剪继续由 OpenCodex 完成。
- App 只负责 UI、请求路由拓扑、PCL gateway 和显式 provider 配置，不实现 HTTP/SSE/重试/取消/协议转换。
- App 打开、刷新或升级只暂存经过 provenance 校验的 sidecar，不重启健康数据面，也不修改 `~/.codex/config.toml`。
- 启用时先健康检查，再由 OpenCodex 原生集成完成注入；失败先调用上游 restore，再原子恢复交接前配置。停用只恢复 Codex 原生配置，sidecar 保持运行。
- MCP 只保留模型发现和健康状态，不执行任务，也不启动外部 `codex exec`。

PCL API Key 默认只保存在所选 gateway 的 `~/.config/pcl-codex-bridge/api-key`（权限 `600`）。普通客户端、Codex 配置和子 Agent 上下文均不保存该 Key。

## 可视化应用

macOS 的 **PCL Relay.app** 按用户任务收成三个联动页面；Linux 使用包含同一数据面的 `pcl-codex` 命令行应用：

- **路由**：以拓扑图显示 Codex → OpenCodex → official/PCL gateway → PCL API 的分流，并管理中转站目录、Relay 心跳和多端模型拓扑同步；不管理设备网络。
- **模型与 Agent**：统一模型目录、对话/流式/工具能力检测、启用角色和 Codex 使用提示。
- **PCL 门户**：只负责通过当前中转站打开 API 广场、用量和 Key 页面；使用隔离浏览器资料，不修改 macOS 全局代理。

PCL Relay 作为菜单栏应用运行：关闭完整设置不会退出，右上角图标可刷新路由状态、检测模型、打开门户或进入 Agent 设置。应用使用 macOS 登录项自动启动，不使用 KeepAlive；用户选择“退出应用”后不会在当前登录会话被强行拉起。

菜单栏和完整设置均提供“Codex PCL 子 Agent”总开关。启用会先准备健康 sidecar，再显式交给 OpenCodex 原生集成；关闭仅让 OpenCodex 恢复 Codex 官方配置，不停止 sidecar，不影响其他正在运行的客户端。

菜单栏状态只表达 PCL Relay 自己的 gateway/sidecar/provider/同步协议状态。网络可达性和网络恢复状态由外部基础设施独立展示，PCL Relay 不读取或重复计算第二份状态。

Embedding、重排序、语音、OCR 和图像模型可以在模型目录中查看，但不会被误注册成代码子 Agent。

PCL Relay 不枚举 Tailnet 设备、不建立桥接、不选择 VPN/代理节点，也不挂载远程文件。用户先提供可用 gateway/control URL；PCL Relay 只对这些 URL 做自身协议级验证。对于尚未安装的节点，用户可以额外逐项登记一个已经可用的 SSH alias，Relay 只用它执行固定的首次安装流程，不读取 SSH inventory，也不修复网络。

多端同步使用独立的 `pcl-relay-topology/1` 控制协议，默认经用户已建立的 Tailscale 地址传输。同步白名单仅包含 gateway 目录、当前 gateway、PCL Agent 模型选择和逻辑版本；本机 peer 地址、token 文件、网络状态及任何凭据都不进入同步文档。节点默认监听 `0.0.0.0:15726`，无 token 时只接受 loopback/Tailnet 来源；PCL Relay 不调用 Tailscale CLI，也不负责其登录、节点或连通性。

构建并安装：

```bash
./scripts/build_macos_app.sh
open -a "PCL Relay"
```

新 Mac 安装 PCL Relay 后，在 App 中填写/选择已可达的 gateway 并安装 Codex 集成。应用内包含自包含客户端，不要求单独安装本项目源码。

Linux 发布包按架构提供：

```bash
tar -xzf PCL-Relay-linux-x86_64.tar.gz
cd PCL-Relay-linux-x86_64
./install.sh
~/.local/bin/pcl-codex --gateway-url http://127.0.0.1:15722/v1 integration enable
```

`aarch64` 使用同名的 `PCL-Relay-linux-aarch64.tar.gz`。两个包都包含完整固定 OpenCodex 和目标架构 Bun runtime；`install.sh` 只暂存并校验，不自动注册 systemd、不修改活动 Codex 配置。显式执行 `integration enable` 才会由 OpenCodex 准备服务并接管 Codex 集成。

路由页的“版本更新”栏处理当前设备升级：

- 本机从 [`LossInWind/PCL-Relay` GitHub Releases](https://github.com/LossInWind/PCL-Relay/releases) 检查、下载并校验正式安装包。
- macOS 只选择 `PCL-Relay-macOS.zip`；Linux 按运行架构选择 `PCL-Relay-linux-x86_64.tar.gz` 或 `PCL-Relay-linux-aarch64.tar.gz`。
- 已接入节点不通过 SSH 升级。“更新已接入节点”通过鉴权 Relay/Tailscale 控制面持久化版本和不可变资产清单：在线节点立即领取，离线节点恢复心跳后自动补领，同一 offer 不会重复安装。每个节点先从 GitHub 下载自己的平台包；只有 GitHub 失败时，节点才从已登记的拓扑 peers 流式拉取同版本、同平台的 verified cache。两种来源均使用清单中的大小和 SHA-256，并继续执行 macOS 签名或 Linux provenance/Bun 校验，过程中不重启模型数据面。
- “从 SSH 配置导入”只在用户点击后读取字面的 `Host` alias，用 `ssh -G` 本地解析并按 HostName 去重，不连接远端或读取密钥。“一键安装/接入全部”仅处理这批明确登记的裸节点。通常每台设备只需已有 SSH alias，目标 `http://HostName:15726` 会自动推导；特殊拓扑才需要手动覆盖。凭据不保存、不同步。目标先从 GitHub 取自己平台的包，失败才从发起节点的 verified cache 传输。安装完成并通过心跳后成为普通 peer，以后只走上述控制面更新。若本机版本尚未发布齐三个平台资产，部署会明确阻止，绝不静默安装旧版。
- `pcl_codex_bridge/VERSION` 是唯一版本源；App 安装包、Python 客户端、中转站健康检查、设备心跳和远端期望版本在构建与安装时都从它生成并核对。

## 使用原生子 Agent

安装后新建 Codex 任务或重新加载 VS Code 窗口，然后直接说：

```text
让 pcl-deepseek-pro 在当前项目实现这个功能并运行测试，完成后由你复核。
让 pcl-glm 和 pcl-kimi 分别审查这个方案，再由你整合结论。
启动多个 pcl-deepseek-flash 子 Agent，并行处理这些边界清晰的修改。
```

默认别名：

- `pcl-deepseek-pro` → `pcl/DeepSeek-V4-Pro`
- `pcl-deepseek-flash` → `pcl/DeepSeek-V4-Flash-0731`
- `pcl-glm` → `pcl/GLM-5.2`
- `pcl-kimi` → `pcl/Kimi-K3`

模型可通过“检查更新”发现并按需启用。只有通过文本 Agent 资格检查的模型才会写入 Codex 子 Agent 目录。

## 命令行接口

图形应用覆盖日常操作；下列命令用于自动化和诊断：

```bash
./bin/pcl-codex install gateway --key-file ~/.config/pcl-llm/api-key
./bin/pcl-codex sidecar stage
./bin/pcl-codex sidecar prepare
./bin/pcl-codex sidecar activate
~/.local/bin/pcl-codex doctor
~/.local/bin/pcl-codex models discover
~/.local/bin/pcl-codex models detect
~/.local/bin/pcl-codex models select
pcl-codex updates status
pcl-codex updates install
pcl-codex updates push
pcl-codex routes list --probe
pcl-codex routes add http://relay:15722/v1 --name "PCL relay"
pcl-codex routes select <gateway-id-or-url>
pcl-codex sync serve --host 0.0.0.0 --port 15726
pcl-codex sync peers add http://peer:15726 --token-file ~/.config/pcl-relay/sync-token
pcl-codex sync now
```

常用管理命令：

- `pcl-codex routes list|add|select|remove`：维护已可达 PCL gateway 目录并只切换 `pcl` provider；活动 OpenCodex 切换失败会恢复旧 provider，未运行时只保存下次启用所需选择。
- `pcl-codex routes proxy show|set|clear|apply`：显式查看或修改 OpenCodex 原生 `proxy/noProxy`；默认不设置、不自动发现，配置按设备本地保存且不参加多端同步。该设置是 OpenCodex 的启动配置：PCL Relay 通过上游 `memory` 与 `restart` 契约应用，空闲时安全排空并重启；有活动请求时只标记待应用，不中断会话。
- `pcl-codex sync status|now|peers|serve`：运行 Relay 心跳和多端模型拓扑同步；不发现或修改底层网络。
- `pcl-codex portal status` / `portal open --path /wallet`：检查或打开 PCL 门户转发。
- `pcl-codex updates status` / `updates install`：检查并安装最新 GitHub Release；安装前校验 SHA-256 和应用签名完整性。
- `pcl-codex updates push`：经 Relay/Tailscale 控制面持久化同一版本升级请求；在线节点立即执行，离线节点恢复同步后补领。节点优先从 GitHub 获取本平台包，失败时从其他 Relay 节点拉取相同摘要的已验证缓存，再校验、安装并报告结果。节点间不传凭据。
- `pcl-codex uninstall`：停止本机路由并只撤销本工具管理的 Codex 配置；保留时间戳备份。
- `pcl-codex uninstall --gateway`：停止中转站服务并保留 API Key。

## 安全边界

- 中转网关默认绑定 `127.0.0.1`，远端监听地址必须通过 `PCL_CODEX_GATEWAY_HOST` 或安装参数显式提供；完整 OpenCodex sidecar 只绑定 `127.0.0.1`。
- provider 隔离、官方凭据边界、请求头过滤、模型路由和 Codex 文件 journal/restore 均沿用固定上游 OpenCodex 实现。
- PCL API Key 只存在中转站；客户端给 PCL provider 使用无权限占位值，真实 Key 不进入 Codex 或 App 配置。
- 一次性交接只移除 PCL Relay 自己的旧标记块并先创建备份；检测到用户自定义根路由时由 OpenCodex 拒绝接管。
- PCL Relay 不管理 Clash、VPN、订阅、节点、系统代理、Tailscale、SSH 隧道或文件映射，也不读取其他 App 发布的 endpoint 或状态文件；它只使用用户在自身配置中明确登记的 endpoint。
- OpenCodex 依赖仍由上游固定的 Bun 1.4.0 安装，App 运行时则固定为 Bun 1.3.14；两者在构建时分别校验，runtime release id 也包含 Bun 版本，升级不会误复用旧 WSS 数据面。
- 版本升级先完整下载、校验并暂存；已有健康 sidecar 和当前 Codex 配置不会因 App 启动或刷新而切换。
- 完整上游版本、commit、tree 和 MIT License 记录在 `vendor/opencodex.UPSTREAM.json` 并随 App 分发。

## 开发测试与致谢

```bash
python3 -m pytest -q
swift test
./scripts/package_release.sh
```

本安装包直接内嵌 MIT 许可的完整固定版 [`OpenCodex`](https://github.com/lidge-jun/opencodex) 2.46.0（commit `bba63222d3eeb5c8e397edae35798225e4fa1a6f`），而不是选择性重写其传输层。其他历史参考与完整许可见 `NOTICE`。
