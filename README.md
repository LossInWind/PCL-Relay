# PCL Codex Bridge

让官方 GPT 保持为 Codex 主模型，同时把 PCL 内网的 DeepSeek、GLM、Kimi、Qwen
等文本模型注册为可执行的 MCP 子代理。官方 OpenAI 登录、provider 和模型下拉框不会被替换。

源码仓库：[`LossInWind/PCL-Relay`](https://github.com/LossInWind/PCL-Relay)

## 架构

```text
Codex Desktop / VS Code Codex
        | official GPT (unchanged)
        | MCP delegation
        v
local pcl-codex MCP server
        | launches: codex exec --profile pcl-agent
        v
        +--> selected Tailnet-only gateway:15722 --> PCL API
        +--> local loopback adapter (PCL-network host) --> PCL API
```

PCL API Key 默认只保存在当前选中的中转站的
`~/.config/pcl-codex-bridge/api-key`。普通 Tailnet 客户端不保存密钥；只有明确选择
“PCL 本地直连”的内网主机会在自身权限 `600` 文件中保存一份，并仅通过
`127.0.0.1:15722` 提供给本机 Codex。
PCL 门户登录只用于创建和管理该 Key；日常调用不需要客户端重复登录。

## 可视化应用

macOS 提供原生的 **PCL Relay** 应用，包含四个核心页面：

- **拓扑配置**：拖动 Tailnet 设备、点击连线，查看每台设备能否作为中转站、直连当前中转站、直接访问 PCL API 或经 Mac 桥接；可载入最简单稳定的自动推荐，一键应用后重新检测。
- **中转站**：自动枚举 Tailnet 节点，识别已部署网关、验证 PCL API Key 与模型目录，并手动选择当前中转站。选择是粘性的，任务执行中不会自动故障切换。
- **中转站页中的远端 Codex**：识别 `~/.ssh/config` 中与 Tailnet 节点对应的 macOS/Linux 主机，检查远端系统、Codex 配置和中转站可达性，并一键选择 Tailnet 直连、PCL 本地直连或桥接。Windows 不在支持范围内。
- **PCL 门户**：在独立的本机浏览器资料中打开 PCL API 广场、用量/钱包和 API Key 页面。只有 `*.pcl.ac.cn` 经当前 Tailnet 中转站转发，不修改 macOS 全局代理，也不读取日常浏览器 Cookie。
- **子 Agent**：从可用文本模型中选择允许官方 GPT 调用的 PCL 模型，安装/修复 Codex 注册，并直接运行工作区任务。Embedding、重排序、语音、OCR 和图像模型会展示但不会误开放成代码 Agent。

中转站页和拓扑页共享同一份设备、所选中转站、规划路线和连通性结果。每台设备均可
单独“刷新”或“测试连通性”；完整测试依次验证 Tailnet、SSH、接入路径和模型目录，
不会为了刷新一台设备重新配置其他服务器。

默认中转设备为 `haichen-pcl-linux-3070ti`（`100.113.234.58`），也可切换到其他已部署且凭据有效的网关。网关提供仅在
Tailnet 可达的受控 Admin API，App 可以检查本服务状态/绑定、读取本服务脱敏日志，
以及请求本服务自重启。它不依赖 SSH、不开放任意远程命令，也不操作服务器上的其他用户进程。

构建并安装到 `/Applications/PCL Relay.app`：

```bash
./scripts/build_macos_app.sh
open -a "PCL Relay"
```

App 内已经包含自包含的 `pcl-codex` 客户端。新 Mac 只需安装 App、安装并登录
Tailscale、加入同一 Tailnet；首次启动会自动将客户端注册到当前用户的 Codex。
不需要复制项目源码或安装 Python，也不会接触本地 OpenAI 登录凭据或 PCL API Key。
当前安装包面向 Apple Silicon macOS。

## 安装

跳板机：

```bash
./bin/pcl-codex install gateway --key-file ~/.config/pcl-llm/api-key
```

Mac 或 Ubuntu Codex 主机：

```bash
./bin/pcl-codex install client
~/.local/bin/pcl-codex doctor
~/.local/bin/pcl-codex models discover
~/.local/bin/pcl-codex models detect
~/.local/bin/pcl-codex models select
```

若某台 Ubuntu/容器宿主禁用了 Bubblewrap 所需的 user namespace，先确认该账号和
工作区可信，再显式启用普通用户级无沙箱回退：

```bash
./bin/pcl-codex install client --allow-unsandboxed-fallback
```

该选项不会使用 sudo，但子代理命令不再受 Codex 文件沙箱限制；安装器会保留
工作区锁、Git 前后审计和危险工作区根目录拒绝规则。Mac 不需要也不应启用。

重启 Codex 桌面版或重新加载 VS Code 窗口后，可以直接说：

```text
让 pcl_deepseek_pro 在当前项目实现这个功能，并运行测试。
让 pcl_glm 和 pcl_kimi 分别审查这个方案，再由你整合。
```

主 GPT 也可依据 MCP 工具描述自动选择 PCL 代理。
GPT 委派时会把当前 Codex/VS Code 工作区直接传给子 Agent；App 里的目录选择器只用于脱离 GPT 会话的手动测试。
安装器仅对 `pcl_agents` MCP 服务写入 `default_tools_approval_mode = "approve"`，
使显式调用和自动委派不会被主会话的无交互审批策略拦截；其他 MCP 服务的审批配置不受影响。

## 命令

- `pcl-codex doctor`：检查 Codex、Tailscale、网关与客户端配置。
- `pcl-codex relays discover`：扫描 Tailnet 节点，验证网关、PCL 凭据与模型数量。
- `pcl-codex relays select <gateway-url>`：选择中转站并只更新本工具管理的 Codex 配置区块。
- `pcl-codex clients discover`：读取本机 SSH 配置并检查其他 macOS/Linux Codex 主机。
- `pcl-codex clients install <ssh-alias>`：通过现有 SSH 公钥在远端用户目录一键接入当前中转站。
- `pcl-codex portal status`：验证当前中转站能否转发 PCL 内网页面。
- `pcl-codex portal open --path /wallet`：使用独立浏览器资料打开 PCL 门户；支持 `/`、`/wallet`、`/keys`、`/playground` 和 `/models`。
- `pcl-codex direct install <ssh-alias>`：对能直接访问 PCL API、但不能安全充当 Tailnet 中转站的主机安装回环适配器。
- `pcl-codex bridges install <ssh-alias>`：仅在直连方案都不可用时建立独立的 Mac SSH 回环桥接。
- `pcl-codex models discover`：从网关读取最新模型目录，不发送推理请求。
- `pcl-codex models detect`：检测已选模型的普通对话、SSE 流式输出和工具调用。
- `pcl-codex models show`：显示缓存的检测结果。
- `pcl-codex models select [aliases-or-model-ids...]`：保存启用的代理并重建子 Agent 模型目录；未传参数时恢复四个推荐代理。
- `pcl-codex delegate <agent> <task> --workspace <path>`：直接做端到端测试。
- `pcl-codex uninstall`：撤销客户端配置，保留备份。
- `pcl-codex uninstall --gateway`：停止网关服务，保留 API Key。

## 安全和冲突控制

- 网关只绑定 Tailscale IPv4；无法获取 Tailscale IP 时拒绝启动。
- 浏览器代理只接受 Tailnet 来源，并仅允许到 `pcl.ac.cn` 及其子域的 HTTPS CONNECT；它不是通用代理。
- 执行代理默认使用 `workspace-write` 沙箱和 `approval_policy=never`。
- 同一工作区的写任务通过文件锁串行执行。
- 执行结果带回 Git 修改前后状态、diff、测试摘要和错误尾部。
- 安装器只管理带标记的 `config.toml` 区块，并在修改前创建时间戳备份。
- PCL 子进程禁用 `pcl_agents` MCP，避免递归委派。
- 远端管理强制 `ClearAllForwardings=yes` 和 `BatchMode=yes`，不会建立或占用 VS Code 现有反向代理端口。
- 远端安装仅写 `~/.codex`、`~/.local/share/pcl-codex-bridge` 与 `~/.local/bin/pcl-codex`；不使用 sudo，不重启 VS Code、SSH、Tailscale 或其他任务。
- 对 Pod/容器会从工作区内部实测网关连通性；即使宿主节点出现在 Tailnet 中，只要容器命名空间不可达，就拒绝误配置并显示诊断。
- 中转站资格要求工作区自身同时具备 Tailscale 地址和 PCL 网络；仅仅因宿主端口映射而能被探测到的 Pod 不会被推荐为共享中转站。
- 绿色拓扑连线表示网关/目录/配置路径已验证；实际模型推理能力仍由“检查更新/检测已选 Agent”单独验收。

## 开发测试

```bash
PYTHONPATH=. python3 -m unittest discover -s tests -v
```

协议转换设计参考了 MIT 许可的
[`codex-deepseek-proxy`](https://github.com/himmetozcan/codex-deepseek-proxy)，
统一路由与管理界面设计还参考了 MIT 许可的
[`OpenCodex`](https://github.com/lidge-jun/opencodex)。本项目保留更窄的个人 Tailnet
边界，不接管 OpenAI 凭据；详见 `NOTICE`。
