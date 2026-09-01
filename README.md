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
                      Codex 原生 v1 spawn_agent
```

- 官方 GPT 仍使用原来的 `openai` provider、登录状态和模型名称；请求由应用内置路由安全透传。
- PCL 模型使用 `pcl/DeepSeek-V4-Pro` 等带命名空间的模型 ID，避免误发到官方服务。
- 每个模型目录项固定使用 Codex 原生 multi-agent v1。主 GPT 调用 PCL 子 Agent 时，创建、进度和结果显示在 Codex 自己的任务界面中。
- 子 Agent 自动继承当前 Codex / VS Code 工作区，无需在 PCL Relay 中再选择目录。
- MCP 只保留模型发现和健康状态，不执行任务，也不再启动外部 `codex exec`。

PCL API Key 默认只保存在所选中转站的 `~/.config/pcl-codex-bridge/api-key`（权限 `600`）。普通 Tailnet 客户端、Codex 配置和子 Agent 上下文均不保存该 Key。

## 可视化应用

macOS 的 **PCL Relay.app** 包含四个联动页面：

- **拓扑配置**：发现 Tailnet 设备、检测直连/桥接/PCL 本地直连能力，推荐并应用最简单的可用拓扑。
- **中转站**：选择当前 PCL 中转站，查看网关状态、服务日志、模型目录，并给其他 macOS/Linux 设备一键安装同一客户端。
- **PCL 门户**：通过当前中转站在隔离的浏览器资料中访问 API 广场、模型、钱包和 Key 页面；不修改 macOS 全局代理。
- **子 Agent**：选择允许 Codex 调用的 PCL 文本模型，查看对话、流式与工具能力，并安装/修复原生 v1 集成。

Embedding、重排序、语音、OCR 和图像模型可以在模型目录中查看，但不会被误注册成代码子 Agent。

构建并安装：

```bash
./scripts/build_macos_app.sh
open -a "PCL Relay"
```

新 Mac 只需安装 PCL Relay、登录同一个 Tailnet，并在 App 中安装 Codex 集成。应用内包含自包含客户端，不要求单独安装本项目源码。

## 使用原生子 Agent

安装后新建 Codex 任务或重新加载 VS Code 窗口，然后直接说：

```text
让 pcl_deepseek_pro 在当前项目实现这个功能并运行测试，完成后由你复核。
让 pcl_glm 和 pcl_kimi 分别审查这个方案，再由你整合结论。
启动多个 pcl_deepseek_flash 子 Agent，并行处理这些边界清晰的修改。
```

默认别名：

- `pcl_deepseek_pro` → `pcl/DeepSeek-V4-Pro`
- `pcl_deepseek_flash` → `pcl/DeepSeek-V4-Flash-0731`
- `pcl_glm` → `pcl/GLM-5.2`
- `pcl_kimi` → `pcl/Kimi-K3`

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
```

常用管理命令：

- `pcl-codex relays discover` / `relays select <url>`：发现并选择 Tailnet 中转站。
- `pcl-codex clients discover` / `clients install <ssh-alias>`：发现其他 macOS/Linux 设备并安装完整客户端。
- `pcl-codex portal status` / `portal open --path /wallet`：检查或打开 PCL 门户转发。
- `pcl-codex direct install <ssh-alias>`：让可直接访问 PCL API 的远端主机使用本地回环适配器。
- `pcl-codex bridges install <ssh-alias>`：仅在直连不可用时建立 Mac 回环桥接。
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
swift test --package-path macos
```

Responses 转换参考了 MIT 许可的 [`codex-deepseek-proxy`](https://github.com/himmetozcan/codex-deepseek-proxy)。本应用的主要结构是“PCL Tailnet 中转站 + 内嵌 OpenCodex 能力”：统一模型目录、官方透传和原生 v1 子 Agent 路由主要参考并迁移自 MIT 许可的 [`OpenCodex`](https://github.com/lidge-jun/opencodex)。[`BigStrongSun/ccswitchmulti`](https://github.com/BigStrongSun/ccswitchmulti) 用于交叉检查 zstd、WebSocket 回退和 spawn-agent 模型优先级等兼容细节。运行时不依赖这些项目，也不会安装第二个应用。详见 `NOTICE`。
