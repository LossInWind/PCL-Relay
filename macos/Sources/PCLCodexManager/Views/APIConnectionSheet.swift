import AppKit
import BridgeCore
import SwiftUI

struct APIConnectionSheet: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var selected = ""
    @State private var feedback = ""
    private var models: [DiscoveredModel] {
        model.allDiscoveredModels.filter {
            $0.agentEligible && !$0.id.lowercased().contains("embedding") && !$0.id.lowercased().contains("rerank")
        }
    }
    private var info: APIConnectionInfo? {
        guard let gateway = model.registry?.gateway else { return nil }
        return APIConnectionInfo(gateway: gateway, modelID: selected)
    }
    var body: some View {
        ScrollView { VStack(alignment: .leading, spacing: 16) {
            Text("连接其他 Agent").font(.title2.weight(.semibold))
            Text("一个中转站配置，使用全部 PCL 文本模型。默认设备已加入 Tailnet，不需要逐个模型填写地址或 Key。")
                .foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            if let info {
                field("Base URL", info.baseURL)
                field("模型目录", info.baseURL + "/models")
                field("API Key 占位值", "pcl-tailnet")
                Text(info.credentialNotice).font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                Text(info.localOnly ? "此地址只在当前设备有效，不要复制到另一台电脑。" : "其他设备须已加入可访问该地址的 Tailnet；复制配置并不代表网络已验证。")
                    .font(.caption).foregroundStyle(.orange).fixedSize(horizontal: false, vertical: true)
                HStack {
                    Button("复制全部接入信息") { copy(info.summary) }
                    Button("复制 OpenCode 配置") { copy(info.clientConfiguration("opencode", modelIDs: models.map(\.id))) }
                    Button("复制 Pi 配置") { copy(info.clientConfiguration("pi", modelIDs: models.map(\.id))) }
                }
                Text("配置包含目录中的 \(models.count) 个文本模型，与 Codex 中是否勾选无关。OpenCode 合并到 opencode.json 的 provider；Pi 合并到 ~/.pi/agent/models.json 的 providers。不要覆盖其他提供商配置。")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                Text("支持自动读取目录的客户端使用 /models。上述静态配置在新增模型后需重新复制；目录存在不代表每个模型已通过工具调用验收。")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                DisclosureGroup("测试示例（只选择本次测试模型，不限制接入范围）") {
                    Picker("测试模型", selection: $selected) {
                        ForEach(models) { item in Text(item.id).tag(item.id) }
                    }
                    Button("复制 cURL 示例") { copy(info.curlExample) }
                }
                Text("仅复制不发送请求。运行示例将消耗少量模型额度；使用原始模型 ID，不添加 pcl/ 前缀。")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            } else {
                Text("请先刷新模型目录并选择模型；没有确认的中转地址时不提供猜测配置。")
                    .foregroundStyle(.secondary)
            }
            Text(feedback).font(.caption).foregroundStyle(.secondary)
            HStack { Spacer(); Button("完成") { dismiss() }.keyboardShortcut(.cancelAction) }
        }.padding(24) }.frame(width: 660, height: 600)
        .onAppear { selected = models.first?.id ?? "" }
    }
    private func field(_ label: String, _ value: String) -> some View {
        HStack(alignment: .top) {
            Text(label).frame(width: 120, alignment: .leading)
            Text(value).font(.system(.body, design: .monospaced)).textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
            Button("复制 \(label)") { copy(value) }
        }
    }
    private func copy(_ value: String) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(value, forType: .string)
        feedback = "已复制到剪贴板"
    }
}
