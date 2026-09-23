import AppKit
import BridgeCore
import SwiftUI

struct APIConnectionSheet: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var selected = ""
    @State private var feedback = ""
    private var info: APIConnectionInfo? {
        guard let gateway = model.registry?.gateway else { return nil }
        return APIConnectionInfo(gateway: gateway, modelID: selected)
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("连接其他 Agent").font(.title2.weight(.semibold))
            Text("在 OpenCode、Pi 等客户端选择 OpenAI-compatible / Chat Completions，填写以下信息。不同客户端的配置文件格式不同。")
                .foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            Picker("模型", selection: $selected) {
                Text("请选择模型").tag("")
                ForEach(model.allDiscoveredModels.filter { $0.agentEligible }) { item in
                    Text(item.id).tag(item.id)
                }
            }
            if let info {
                field("Base URL", info.baseURL)
                field("Model ID", info.modelID)
                field("API Key 占位值", "pcl-tailnet")
                Text(info.credentialNotice).font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                Text(info.localOnly ? "此地址只在当前设备有效，不要复制到另一台电脑。" : "其他设备须已加入可访问该地址的 Tailnet；复制配置并不代表网络已验证。")
                    .font(.caption).foregroundStyle(.orange).fixedSize(horizontal: false, vertical: true)
                HStack {
                    Button("复制全部接入信息") { copy(info.summary) }
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
        }.padding(24).frame(width: 620)
        .onAppear { selected = model.allDiscoveredModels.first(where: { $0.agentEligible })?.id ?? "" }
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
