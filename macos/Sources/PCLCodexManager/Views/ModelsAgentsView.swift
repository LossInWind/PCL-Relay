import AppKit
import BridgeCore
import SwiftUI

struct ModelsAgentsView: View {
    @EnvironmentObject private var model: AppModel
    @State private var query = ""
    @State private var agentsOnly = true
    @State private var selectedModel: DiscoveredModel?
    private var visibleModels: [DiscoveredModel] {
        model.allDiscoveredModels.filter {
            (!agentsOnly || $0.agentEligible) && (query.isEmpty || "\($0.id) \($0.family) \($0.alias)".localizedCaseInsensitiveContains(query))
        }
    }
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("模型与 Agent").font(.title2.weight(.semibold))
                        Text("选择可供 Codex 使用的 PCL 模型；任务分工仍由主 Agent 决定。")
                            .font(.subheadline).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button(model.isDiscovering ? "读取中…" : "刷新模型目录", action: model.discoverModels)
                        .disabled(model.isDiscovering || model.isSavingAgents || model.isDetecting)
                    Button(model.isDetecting ? "停止实测" : "能力实测…") {
                        if model.isDetecting { model.cancelDetection() } else { model.showDetectionConfirmation = true }
                    }.disabled(model.isSavingAgents || model.isDiscovering)
                }
                HStack {
                    TextField("搜索模型、家族或 Agent 名称", text: $query)
                        .textFieldStyle(.roundedBorder).frame(maxWidth: 370)
                    Toggle("仅显示 Agent 模型", isOn: $agentsOnly).toggleStyle(.checkbox)
                    Spacer()
                    Text("已启用 \(model.selectedAgents.count) 个").font(.subheadline).foregroundStyle(.secondary)
                }
                Text("目录读取不发送生成请求。能力结果为历史实测，不代表此刻所有模型均可调用。")
                    .font(.caption).foregroundStyle(.secondary)
                if let check = model.checks["models"] {
                    Text(check.summary).font(.caption).foregroundStyle(check.phase == .failed ? Color.orange : Color.secondary)
                }
                if let date = model.registry?.catalogCheckedAt {
                    Text("目录来源时间：\(date)").font(.caption).foregroundStyle(.secondary)
                }
                HStack {
                    if model.isSavingAgents { ProgressView().controlSize(.small) }
                    Text(model.agentSaveMessage).font(.caption)
                        .foregroundStyle(model.agentSelection.failed == nil ? Color.secondary : Color.orange)
                    if model.agentSelection.failed != nil { Button("重试保存", action: model.retryAgentSave) }
                }
                VStack(spacing: 0) {
                    ForEach(visibleModels) { item in
                        modelRow(item)
                        if item.id != visibleModels.last?.id { Divider().padding(.leading, 48) }
                    }
                    if visibleModels.isEmpty {
                        Text(model.allDiscoveredModels.isEmpty ? "尚未读取模型目录，请刷新模型目录。" : "没有匹配的模型")
                            .foregroundStyle(.secondary).padding(28)
                    }
                }
                .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 12))
                .overlay(RoundedRectangle(cornerRadius: 12).stroke(Color.primary.opacity(0.08)))
                if model.isDetecting { ConsolePanel(text: model.commandLog, title: "能力实测结果") }
                DisclosureGroup("Codex 接入与使用说明") {
                    VStack(alignment: .leading, spacing: 12) {
                        Text("模型 ID 用于选择模型，Agent 名称用于指定角色。子 Agent 沿用任务工作区，无需在这里填写目录。")
                        Text(model.checks["integration"]?.summary ?? "接入状态尚未检查").foregroundStyle(.secondary)
                        Button(model.isInstallingIntegration ? "处理中…" : "安装 / 修复 Codex 注册") {
                            model.installCodexIntegration()
                        }.disabled(model.isInstallingIntegration || model.isSavingAgents || model.isDetecting || model.isDiscovering)
                        Text("修复会修改接入配置；正常使用无需重复执行。").foregroundStyle(.secondary)
                    }.font(.caption).padding(.top, 10)
                }
            }.padding(24)
        }
        .sheet(item: $selectedModel) { item in
            ModelDetailSheet(item: item, status: model.registry?.models[item.alias], checkedAt: model.registry?.checkedAt)
        }
    }
    private func modelRow(_ item: DiscoveredModel) -> some View {
        let agent = AgentDefinition(model: item)
        let status = model.registry?.models[item.alias]
        return HStack(spacing: 14) {
            Image(systemName: agent.symbol).font(.title3).foregroundStyle(agent.tint).frame(width: 26)
            VStack(alignment: .leading, spacing: 5) {
                Text(item.id).font(.subheadline.weight(.semibold)).textSelection(.enabled)
                Text("\(item.family) · \(categoryName(item.category))").font(.caption).foregroundStyle(.secondary)
                if item.agentEligible {
                    Text(capabilityTitle(status) + (status == nil ? "" : " · " + (model.registry?.checkedAt ?? "检测时间未知")))
                        .font(.caption).foregroundStyle(.secondary)
                }
                if let error = status?.error, !error.isEmpty {
                    Text(error).font(.caption).foregroundStyle(.orange).lineLimit(2)
                }
            }.frame(maxWidth: .infinity, alignment: .leading)
            Button("详情") { selectedModel = item }
            if item.agentEligible {
                Button("复制调用示例") {
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(agentExample(agent), forType: .string)
                    model.show("已复制 \(agent.title) 调用示例", .info)
                }
                Toggle("启用 \(item.id)", isOn: Binding(
                    get: { model.selectedAgents.contains(item.alias) },
                    set: { model.setAgent(item.alias, enabled: $0) }))
                    .labelsHidden().toggleStyle(.switch)
                    .disabled(model.isDiscovering || model.isDetecting || model.isInstallingIntegration)
            } else {
                Text("非 Agent").font(.caption).foregroundStyle(.secondary).frame(width: 60)
            }
        }.padding(14)
    }
}

struct ModelDetectionConfirmation: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("确认能力实测").font(.title2.weight(.semibold))
            Text("将对以下已启用模型发送普通对话、流式输出和工具调用测试。会消耗少量 API 额度；不会修改项目文件。")
            ScrollView {
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(model.agentOptions.filter { model.selectedAgents.contains($0.id) }) { agent in
                        Label(agent.model, systemImage: "checkmark.circle")
                    }
                }.frame(maxWidth: .infinity, alignment: .leading)
            }.frame(maxHeight: 220)
            Text("仅查看目录无需实测。价格由上游决定，应用不估算未知费用。")
                .font(.caption).foregroundStyle(.secondary)
            HStack {
                Spacer()
                Button("取消") { dismiss() }.keyboardShortcut(.cancelAction)
                Button("开始实测") { dismiss(); model.detectModels() }
                    .buttonStyle(.borderedProminent)
                    .disabled(model.isDetecting || model.isSavingAgents || model.isDiscovering || model.selectedAgents.isEmpty)
            }
        }.padding(24).frame(width: 520)
    }
}

private func categoryName(_ value: String) -> String {
    ["chat": "文本生成", "embedding": "向量", "reranker": "重排序", "speech": "语音", "vision-ocr": "视觉 OCR", "image": "图像"][value] ?? value
}
private func capabilityTitle(_ status: RelayModelStatus?) -> String {
    guard let status else { return "尚未实测" }
    if status.executionReady { return "最近实测通过" }
    return status.chat || status.stream == true || status.toolCompatible == true ? "最近实测部分通过" : "最近实测失败"
}
private func agentExample(_ agent: AgentDefinition) -> String {
    "让 \(agent.nativeRoleName)（模型 pcl/\(agent.model)）处理一个边界清晰的子任务，沿用当前工作区，完成后汇报修改和测试结果，由你最终复核。"
}

private struct ModelDetailSheet: View {
    @Environment(\.dismiss) private var dismiss
    let item: DiscoveredModel
    let status: RelayModelStatus?
    let checkedAt: String?
    @State private var copied = false
    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text(item.id).font(.title2.weight(.semibold))
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    Text(item.description).foregroundStyle(.secondary)
                    LabeledContent("模型 ID", value: "pcl/\(item.id)")
                    LabeledContent("Agent 名称", value: item.agentEligible ? AgentDefinition(model: item).nativeRoleName : "不适用")
                    LabeledContent("模型家族", value: item.family)
                    LabeledContent("模型类型", value: categoryName(item.category))
                    LabeledContent("输入模态", value: item.inputModalities.joined(separator: "、"))
                    if item.agentEligible {
                        LabeledContent("能力记录", value: capabilityTitle(status))
                        LabeledContent("实测时间", value: checkedAt ?? "未知")
                        HStack {
                            MiniBadge("对话", active: status?.chat == true)
                            MiniBadge("流式", active: status?.stream == true)
                            MiniBadge("工具", active: status?.toolCompatible == true)
                        }
                        Text(agentExample(AgentDefinition(model: item))).font(.callout)
                    }
                    if let error = status?.error, !error.isEmpty { Text(error).foregroundStyle(.orange) }
                    Text("上游未提供的上下文窗口、价格和速率限制显示为未知，不根据模型名称猜测。")
                        .font(.caption).foregroundStyle(.secondary)
                }.textSelection(.enabled)
            }.frame(maxHeight: 460)
            HStack {
                if item.agentEligible {
                    Button(copied ? "已复制" : "复制调用示例") {
                        NSPasteboard.general.clearContents()
                        NSPasteboard.general.setString(agentExample(AgentDefinition(model: item)), forType: .string)
                        copied = true
                    }
                }
                Spacer()
                Button("完成") { dismiss() }.keyboardShortcut(.defaultAction)
            }
        }.padding(24).frame(width: 600)
    }
}
