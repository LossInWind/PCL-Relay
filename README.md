# PCL Relay

一个面向个人 Tailnet 的 PCL 内网模型中转站与 Codex 原生子 Agent 管理器。

PCL Relay 是一个软件、一个安装包：它同时负责中转站、模型检测、设备拓扑、PCL 门户转发，以及 Codex Desktop / VS Code Remote-SSH 的原生子 Agent 接入。无需安装 OpenCodex、CCSwitch 或另一个代理应用。

源码仓库：[`LossInWind/PCL-Relay`](https://github.com/LossInWind/PCL-Relay)

## 工作方式

```text
Codex Desktop / VS Code Codex
       |
       | openai provider + 现有 ChatGPT 登录
       v
PCL Relay 本机回环路由（仅 127.0.0.1）
       |                         |
       | 官方 GPT               | pcl/<模型>
       v                         v
ChatGPT Codex 后端       Tailnet 中转站:15722 -> PCL API
                                 |
                                 v
                      Codex 原生 custom role + spawn_agent
```

- 官方 GPT 仍使用原来的 `openai` provider、登录状态和模型名称；请求由应用内置路由安全透传。
- PCL 模型使用 `pcl/DeepSeek-V4-Pro` 等带命名空间的模型 ID，避免误发到官方服务。
- PCL 模型写入 Codex 原生 multi-agent v2 目录，并同步为 `~/.codex/agents/*.toml` custom roles。主 GPT 通过原生 `spawn_agent`/角色调用，创建、进度和结果显示在 Codex 自己的任务界面中。
- 混合 provider 使用非保留的 `agents` V2 协作命名空间；路由只取消任务正文的 OpenAI 私有加密标记，保留官方 reasoning 密文与登录边界，因此 PCL 子 Agent 能读懂父任务。
- 子 Agent 自动继承当前 Codex / VS Code 工作区，无需在 PCL Relay 中再选择目录。
- PCL 子 Agent 支持 Codex 原生远程上下文压缩：旧版 `/responses/compact` 与新版 `compaction_trigger` 均由所选 PCL 模型生成检查点摘要，并使用与 OpenCodex / CCSwitchMulti 兼容的 `ocx1` 信封安全续跑；压缩不会退回 GPT，也不会把 PCL Key 发到客户端。
- MCP 只保留模型发现和健康状态，不执行任务，也不再启动外部 `codex exec`。

PCL API Key 默认只保存在所选中转站的 `~/.config/pcl-codex-bridge/api-key`（权限 `600`）。普通 Tailnet 客户端、Codex 配置和子 Agent 上下文均不保存该 Key。

## 可视化应用

macOS 的 **PCL Relay.app** 按用户任务收成三个联动页面：

- **网络**：只分为连接拓扑和设备管理。拓扑回答“怎么连、经过谁”；设备行集中显示可用状态、当前路径、客户端版本和一个主操作。
- **模型与 Agent**：统一模型目录、对话/流式/工具能力检测、启用角色和 Codex 使用提示。
- **PCL 门户**：只负责通过当前中转站打开 API 广场、用量和 Key 页面；使用隔离浏览器资料，不修改 macOS 全局代理。

Embedding、重排序、语音、OCR 和图像模型可以在模型目录中查看，但不会被误注册成代码子 Agent。

当前 Mac 使用健康中转站不依赖 SSH；SSH 只决定能否从设备管理页面远程安装、修复和升级其他电脑。

自动拓扑始终按“设备直连 PCL 内网 API → Tailnet 直连中转站 → 经其他设备二次转发”的顺序选择，并在每次扫描后用实测结果替换失效或低优先级的旧规划。

构建并安装：

```bash
./scripts/build_macos_app.sh
open -a "PCL Relay"
```

新 Mac 只需安装 PCL Relay、登录同一个 Tailnet，并在 App 中安装 Codex 集成。应用内包含自包含客户端，不要求单独安装本项目源码。

设备管理顶部的“版本更新”栏统一处理升级：

- 本机从 [`LossInWind/PCL-Relay` GitHub Releases](https://github.com/LossInWind/PCL-Relay/releases) 检查、下载并校验正式安装包。
- 本机升级并重新打开后，可把同一版本一键同步到所有可管理的 macOS/Linux 远端客户端。
- 远端设备通过当前 Mac 接收客户端，不要求直接访问 GitHub；不能访问公网的 A6000 Pod 也可以升级。
- 单台设备仍可在自己的设备行中刷新、测试连通性或执行接入/修复/升级。

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
./bin/pcl-codex install client
~/.local/bin/pcl-codex doctor
~/.local/bin/pcl-codex models discover
~/.local/bin/pcl-codex models detect
~/.local/bin/pcl-codex models select
pcl-codex updates status
pcl-codex updates install
```

常用管理命令：

- `pcl-codex relays discover` / `relays select <url>`：发现并选择 Tailnet 中转站。
- `pcl-codex clients discover` / `clients install <ssh-alias>`：发现其他 macOS/Linux 设备并安装完整客户端。
- `pcl-codex portal status` / `portal open --path /wallet`：检查或打开 PCL 门户转发。
- `pcl-codex direct install <ssh-alias>`：让可直接访问 PCL API 的远端主机使用本地回环适配器。
- `pcl-codex bridges install <ssh-alias>`：仅在直连不可用时建立 Mac 回环桥接。
- `pcl-codex updates status` / `updates install`：检查并安装最新 GitHub Release；安装前校验 SHA-256 和应用签名完整性。
- `pcl-codex uninstall`：停止本机路由并只撤销本工具管理的 Codex 配置；保留时间戳备份。
- `pcl-codex uninstall --gateway`：停止中转站服务并保留 API Key。

## 安全边界

- 中转网关只绑定 Tailscale IPv4；本机 Codex 路由只绑定 `127.0.0.1`。
- 路由根据模型命名空间选择上游：PCL 请求不会携带 OpenAI 登录头，官方请求不会发往 PCL 网关。
- 官方透传只允许必要的 Codex 身份与任务元数据头，Cookie 和任意入站头不会跨信任边界。
- 安装器只管理带标记的 `~/.codex/config.toml` 区块，并在修改前创建备份；检测到冲突的用户自定义根路由时会停止。
- 远端安装使用普通用户权限，不重启 SSH、VS Code、Tailscale 或服务器上的其他任务，也不占用已有的 17731/17890 反向代理端口。
- 每台客户端动态选择空闲的本机回环端口，因此共享服务器上的其他用户不会复用本用户的路由进程。
- 显式启用的全局 `multi_agent_v2=true` 会阻止跨 provider v1 路由，`doctor` 会将其报告为未就绪。

## 开发测试与致谢

```bash
python3 -m pytest -q
swift test
./scripts/package_release.sh
```

Responses 转换参考了 MIT 许可的 [`codex-deepseek-proxy`](https://github.com/himmetozcan/codex-deepseek-proxy)。本应用的主要结构是“PCL Tailnet 中转站 + 内嵌 OpenCodex 能力”：统一模型目录、官方透传、`/alpha/search` 官方旁路和原生子 Agent 路由主要参考并迁移自 MIT 许可的 [`OpenCodex`](https://github.com/lidge-jun/opencodex)。[`BigStrongSun/ccswitchmulti`](https://github.com/BigStrongSun/ccswitchmulti) 用于交叉检查 zstd、WebSocket 回退、custom role 和 spawn-agent 模型优先级等兼容细节。运行时不依赖这些项目，也不会安装第二个应用。详见 `NOTICE`。
