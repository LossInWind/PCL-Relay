import BridgeCore
import SwiftUI

struct ModelsAgentsView: View {
    @EnvironmentObject private var model: AppModel
    @State private var selectedAgent = "pcl_deepseek_pro"
    @State private var selectedModel: DiscoveredModel?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                AgentFlowCard()

                SectionHeader(
                    title: "PCL 模型目录",
                    subtitle: "\(model.readyAgentCount) 个执行可用 · \(model.partialAgentCount) 个部分兼容；点开模型可查看能力详情",
                    actionTitle: model.isDiscovering ? "正在检查" : "检查更新",
                    actionSymbol: "arrow.triangle.2.circlepath",
                    action: model.discoverModels
                )

                LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 12) {
                    ForEach(model.allDiscoveredModels) { discovered in
                        RelayModelCard(model: discovered, status: model.registry?.models[discovered.alias])
                            .contentShape(Rectangle())
                            .onTapGesture { selectedModel = discovered }
                    }
                }

                HStack(spacing: 12) {
                    Button {
                        model.isDetecting ? model.cancelDetection() : model.detectModels()
                    } label: {
                        Label(model.isDetecting ? "停止检测" : "检测已选模型", systemImage: model.isDetecting ? "stop.fill" : "waveform.path.ecg")
                    }
                    .buttonStyle(PrimaryButtonStyle())

                    Spacer()
                    if let checked = model.registry?.catalogCheckedAt {
                        Text("目录更新：\(checked)")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }

                if model.isDetecting {
                    ConsolePanel(text: model.commandLog, title: "模型能力检测")
                }

                Divider().opacity(0.45)

                HStack(alignment: .top, spacing: 16) {
                    VStack(alignment: .leading, spacing: 12) {
                        SectionHeader(title: "启用为 Codex 子 Agent", subtitle: "同一份模型状态直接用于角色启用，不再在另一页重复选择")
                        ForEach(model.agentOptions) { agent in
                            AgentToggleCard(
                                agent: agent,
                                status: model.registry?.models[agent.id],
                                enabled: model.selectedAgents.contains(agent.id),
                                selected: selectedAgent == agent.id,
                                onSelect: { selectedAgent = agent.id },
                                onToggle: { model.setAgent(agent.id, enabled: $0) }
                            )
                        }
                    }
                    .frame(maxWidth: 430)

                    NativeAgentUsageCard(selectedAgent: selectedAgent)
                }
            }
            .padding(22)
        }
        .sheet(item: $selectedModel) { item in
            ModelDetailSheet(model: item, status: model.registry?.models[item.alias], checkedAt: model.registry?.checkedAt)
        }
        .onChange(of: model.selectedAgents) { _, agents in
            if !agents.contains(selectedAgent), let fallback = model.agentOptions.first(where: { agents.contains($0.id) }) {
                selectedAgent = fallback.id
            }
        }
        .onChange(of: model.agentOptions) { _, options in
            if !options.contains(where: { $0.id == selectedAgent }), let fallback = options.first(where: { model.selectedAgents.contains($0.id) }) {
                selectedAgent = fallback.id
            }
        }
    }
}

private struct AgentFlowCard: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        GlassCard {
            HStack(spacing: 14) {
                FlowNode(symbol: "sparkles", title: "官方 GPT", detail: "登录与模型选择保留", color: .green)
                FlowArrow()
                FlowNode(symbol: "arrow.triangle.branch", title: "PCL Relay 路由", detail: "一个 App 内置", color: .blue)
                FlowArrow()
                FlowNode(symbol: "person.3.sequence.fill", title: "原生子 Agent", detail: "过程显示在 Codex", color: .purple)
                Spacer()
                VStack(alignment: .trailing, spacing: 8) {
                    StatusPill(title: model.codexIntegrationReady ? "原生角色已就绪" : "需要安装", active: model.codexIntegrationReady, symbol: "arrow.triangle.branch")
                    Button(model.codexIntegrationReady ? "修复注册" : "安装到 Codex") {
                        model.installCodexIntegration()
                    }
                    .buttonStyle(SecondaryButtonStyle())
                }
            }
        }
    }
}

private struct NativeAgentUsageCard: View {
    @EnvironmentObject private var model: AppModel
    let selectedAgent: String

    private var selected: AgentDefinition? {
        model.agentOptions.first { $0.id == selectedAgent }
    }

    private var nativeRoleName: String {
        selected?.nativeRoleName ?? selectedAgent.replacingOccurrences(of: "_", with: "-")
    }

    var body: some View {
        GlassCard {
            VStack(alignment: .leading, spacing: 15) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("在 Codex 中直接使用").font(.headline)
                    Text("不再启动外部 codex exec，也不需要手动填写工作区。子 Agent 自动继承主任务的当前工作区和权限。")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                HStack(spacing: 10) {
                    Image(systemName: selected?.symbol ?? "person.3.fill")
                        .foregroundStyle(selected?.tint ?? .purple)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(selected?.title ?? selectedAgent).font(.subheadline.weight(.semibold))
                        Text("原生模型 ID：pcl/\(selected?.model ?? "")")
                            .font(.caption.monospaced())
                            .foregroundStyle(.secondary)
                    }
                }

                VStack(alignment: .leading, spacing: 8) {
                    Text("示例").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                    NativePromptExample(text: "让 \(nativeRoleName) 实现这个功能，并运行测试；完成后由你复核。")
                    NativePromptExample(text: "启动多个 \(nativeRoleName) 子 Agent，并行处理边界清晰的子任务。")
                }

                Divider().opacity(0.35)
                Label("运行时会像 Codex 自带子 Agent 一样显示创建、进度和结果。", systemImage: "eye.fill")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .frame(maxWidth: .infinity)
    }
}

private struct NativePromptExample: View {
    let text: String

    var body: some View {
        Text(text)
            .font(.system(.caption, design: .monospaced))
            .textSelection(.enabled)
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.black.opacity(0.16), in: RoundedRectangle(cornerRadius: 9))
    }
}

private struct RelayModelCard: View {
    let model: DiscoveredModel
    let status: RelayModelStatus?

    private var agent: AgentDefinition { AgentDefinition(model: model) }

    var body: some View {
        GlassCard {
            HStack(alignment: .top, spacing: 13) {
                Image(systemName: agent.symbol)
                    .font(.system(size: 20, weight: .medium))
                    .foregroundStyle(agent.tint)
                    .frame(width: 42, height: 42)
                    .background(agent.tint.opacity(0.12), in: RoundedRectangle(cornerRadius: 10))
                VStack(alignment: .leading, spacing: 6) {
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(agent.title).font(.headline)
                            Text("\(model.family) · \(categoryName)").font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        if model.agentEligible {
                            AvailabilityPill(status: status)
                        } else {
                            Text("非 Agent")
                                .font(.caption2.weight(.semibold))
                                .foregroundStyle(.secondary)
                                .padding(.horizontal, 8).padding(.vertical, 4)
                                .background(Color.white.opacity(0.06), in: Capsule())
                        }
                    }
                    Text(model.description)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                    if model.agentEligible {
                        HStack(spacing: 6) {
                            MiniBadge("对话", active: status?.chat == true)
                            MiniBadge("流式", active: status?.stream == true)
                            MiniBadge("工具", active: status?.toolCompatible == true)
                            if model.recommended { Text("推荐").font(.caption2).foregroundStyle(.green) }
                            if let mode = status?.toolCallMode, mode != "unavailable" {
                                Text(mode == "native" ? "原生工具" : "兼容工具")
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                            }
                        }
                    } else {
                        Text("不适用于文本执行 Agent")
                            .font(.caption2.weight(.medium))
                            .foregroundStyle(.secondary)
                    }
                    if let error = status?.error, !error.isEmpty {
                        Text(error).font(.caption2).foregroundStyle(.orange).lineLimit(2)
                    }
                }
            }
        }
    }

    private var categoryName: String {
        switch model.category {
        case "chat": return "文本生成"
        case "embedding": return "向量"
        case "reranker": return "重排序"
        case "speech": return "语音"
        case "vision-ocr": return "视觉 OCR"
        case "image": return "图像"
        default: return model.category
        }
    }
}

private struct AvailabilityPill: View {
    let status: RelayModelStatus?

    private var value: (String, Color, String) {
        guard let status else { return ("未检测", .secondary, "questionmark.circle.fill") }
        if status.executionReady { return ("可用", .green, "checkmark.circle.fill") }
        if status.chat || status.stream == true || status.toolCompatible == true {
            return ("部分兼容", .orange, "exclamationmark.circle.fill")
        }
        return ("不可用", .red, "xmark.circle.fill")
    }

    var body: some View {
        Label(value.0, systemImage: value.2)
            .font(.caption2.weight(.semibold))
            .foregroundStyle(value.1)
            .padding(.horizontal, 8).padding(.vertical, 4)
            .background(value.1.opacity(0.11), in: Capsule())
    }
}

private struct ModelDetailSheet: View {
    @Environment(\.dismiss) private var dismiss
    let model: DiscoveredModel
    let status: RelayModelStatus?
    let checkedAt: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack(spacing: 13) {
                Image(systemName: AgentDefinition(model: model).symbol)
                    .font(.system(size: 24, weight: .medium))
                    .foregroundStyle(AgentDefinition(model: model).tint)
                    .frame(width: 52, height: 52)
                    .background(AgentDefinition(model: model).tint.opacity(0.13), in: RoundedRectangle(cornerRadius: 13))
                VStack(alignment: .leading, spacing: 3) {
                    Text(model.id).font(.title2.weight(.semibold))
                    Text(model.description).foregroundStyle(.secondary)
                }
                Spacer()
            }

            GlassCard {
                VStack(spacing: 10) {
                    DetailRow(label: "Agent 别名", value: model.alias)
                    DetailRow(label: "模型家族", value: model.family)
                    DetailRow(label: "模型类型", value: categoryName)
                    DetailRow(label: "输入模态", value: model.inputModalities.joined(separator: "、"))
                    DetailRow(label: "Codex 子 Agent", value: model.agentEligible ? "可以选择" : "不适用")
                    if model.agentEligible {
                        DetailRow(label: "可用性", value: availabilityText)
                        DetailRow(label: "最近检测", value: checkedAt ?? "尚未检测")
                    }
                    DetailRow(label: "网关标记", value: model.ownedBy)
                }
            }

            if model.agentEligible {
                HStack(spacing: 8) {
                    MiniBadge("普通对话", active: status?.chat == true)
                    MiniBadge("SSE 流式", active: status?.stream == true)
                    MiniBadge("工具调用", active: status?.toolCompatible == true)
                    if let mode = status?.toolCallMode, mode != "unavailable" {
                        Text(mode == "native" ? "原生 function calling" : "JSON 兼容层")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
            }


            if let error = status?.error, !error.isEmpty {
                Text("检测详情：\(error)")
                    .font(.caption)
                    .foregroundStyle(.orange)
                    .textSelection(.enabled)
            }

            Text("PCL 的 /v1/models 当前未返回上下文窗口、速率限制或计费信息，因此应用不会猜测这些字段。")
                .font(.caption)
                .foregroundStyle(.secondary)

            HStack {
                Spacer()
                Button("完成") { dismiss() }
                    .keyboardShortcut(.defaultAction)
                    .buttonStyle(PrimaryButtonStyle())
            }
        }
        .padding(24)
        .frame(width: 620)
        .preferredColorScheme(.dark)
    }

    private var categoryName: String {
        switch model.category {
        case "chat": return "文本生成"
        case "embedding": return "向量"
        case "reranker": return "重排序"
        case "speech": return "语音识别"
        case "vision-ocr": return "视觉 OCR"
        case "image": return "图像生成/编辑"
        default: return model.category
        }
    }

    private var availabilityText: String {
        guard let status else { return "未检测" }
        if status.executionReady { return "可用" }
        if status.chat || status.stream == true || status.toolCompatible == true { return "部分兼容" }
        return "不可用"
    }
}

private struct DetailRow: View {
    let label: String
    let value: String
    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            Text(label).foregroundStyle(.secondary)
            Spacer()
            Text(value).font(.system(.body, design: label == "Agent 别名" ? .monospaced : .default))
                .textSelection(.enabled)
        }
    }
}

private struct AgentToggleCard: View {
    let agent: AgentDefinition
    let status: RelayModelStatus?
    let enabled: Bool
    let selected: Bool
    let onSelect: () -> Void
    let onToggle: (Bool) -> Void

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: agent.symbol)
                .foregroundStyle(agent.tint)
                .frame(width: 34, height: 34)
                .background(agent.tint.opacity(0.12), in: RoundedRectangle(cornerRadius: 9))
            VStack(alignment: .leading, spacing: 3) {
                HStack {
                    Text(agent.title).font(.subheadline.weight(.semibold))
                    StatusDot(active: status?.executionReady == true)
                }
                Text(agent.detail).font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            Toggle("", isOn: Binding(get: { enabled }, set: onToggle))
                .labelsHidden()
                .toggleStyle(.switch)
        }
        .padding(12)
        .background(selected ? Color.accentColor.opacity(0.12) : Color.white.opacity(0.035), in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(selected ? Color.accentColor.opacity(0.55) : Color.white.opacity(0.07)))
        .contentShape(Rectangle())
        .onTapGesture(perform: onSelect)
    }
}
