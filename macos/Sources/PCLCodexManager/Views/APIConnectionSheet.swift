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
                Text("1. 填写 OpenAI 兼容提供商").font(.headline)
                field("提供商 ID", "pcl-relay")
                field("显示名称", "PCL Relay")
                field("Base URL", info.baseURL)
                field("模型目录", info.baseURL + "/models")
                field("API Key 占位值", "pcl-tailnet")
                Text(info.credentialNotice).font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                Text(info.localOnly ? "此地址只在当前设备有效，不要复制到另一台电脑。" : "其他设备须已加入可访问该地址的 Tailnet；复制配置并不代表网络已验证。")
                    .font(.caption).foregroundStyle(.orange).fixedSize(horizontal: false, vertical: true)
                Text("请求头留空。地址与 Key 只填一次；每个模型仍须添加一行，模型 ID 和显示名称均可使用下列原始 ID。")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                DisclosureGroup("2. 添加模型（\(models.count) 个，点击复制 ID）") {
                    VStack(alignment: .leading, spacing: 8) {
                        ForEach(models) { item in field("模型 ID / 名称", item.id) }
                    }.padding(.top, 8)
                }
                Text(APIConnectionInfo.openCodeCompatibilityNotice)
                    .font(.caption).foregroundStyle(.orange).fixedSize(horizontal: false, vertical: true)
                Link("查看 OpenCode 官方接入说明", destination: URL(string: "https://opencode.ai/docs/providers/#custom-provider")!)
                HStack {
                    Button("复制全部接入信息") { copy(info.summary) }
                    Button("复制填写指引") { copy(info.setupGuide(modelIDs: models.map(\.id))) }
                }
                HStack {
                    Button("复制 OpenCode 配置") { copy(info.clientConfiguration("opencode2", modelIDs: models.map(\.id))) }
                    Button("复制 Pi 配置") { copy(info.clientConfiguration("pi", modelIDs: models.map(\.id))) }
                }
                DisclosureGroup("高级：旧版 OpenCode 1.x") {
                    Button("复制 OpenCode 1.x 配置") { copy(info.clientConfiguration("opencode", modelIDs: models.map(\.id))) }
                }
                Text("配置包含目录中的 \(models.count) 个文本模型，与 Codex 中是否勾选无关。OpenCode 合并到 ~/.config/opencode/opencode.json：2.x 用 providers，1.x 用 provider。Pi 合并到 ~/.pi/agent/models.json 的 providers。不要覆盖其他提供商配置。")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                Text("支持自动读取目录的客户端使用 /models。上述静态配置在新增模型后需重新复制；目录存在不代表每个模型已通过工具调用验收。")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                Text("3. 保存并验证").font(.headline)
                Text("OpenCode 刷新界面后，在‘管理模型’打开 PCL Relay 分组；新模型可能默认隐藏。确认模型可选后发送短消息。只有得到回复才算接入成功；仅复制或保存配置不算。")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                DisclosureGroup("遇到错误怎么办？") {
                    Text("‘此服务器上无法使用自定义提供商’：本机 2.0.15 可改用上方 2.x 配置文件接入。\n超时 / 域名无法解析：检查该设备的 Tailnet 网络。\n401 / 403：确认错误来源和认证方式，不要使用 Codex 登录凭据。\n模型不存在：刷新模型目录并核对原始 ID。")
                        .font(.caption).fixedSize(horizontal: false, vertical: true)
                }
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
