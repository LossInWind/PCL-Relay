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
            Text("OpenAI 兼容提供商").font(.title2.weight(.semibold))
            Text("一个中转站配置，使用全部 PCL 文本模型。默认设备已加入 Tailnet，不需要逐个模型填写地址或 Key。")
                .foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            if let info {
                Text("1. 填写 OpenAI 兼容提供商").font(.headline)
                field("提供商类型", "OpenAI 兼容（Chat Completions）")
                field("提供商 ID", "pcl-relay")
                field("显示名称", "PCL Relay")
                field("Base URL", info.baseURL)
                field("模型目录", info.baseURL + "/models")
                field("API Key 占位值", "pcl-tailnet")
                Text(info.credentialNotice).font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                Text(info.localOnly ? "此地址只在当前设备有效，不要复制到另一台电脑。" : "其他设备须已加入可访问该地址的 Tailnet；复制配置并不代表网络已验证。")
                    .font(.caption).foregroundStyle(.orange).fixedSize(horizontal: false, vertical: true)
                Text("请求头留空。地址与 Key 只填一次。支持自动获取模型的客户端使用模型目录；需要手动添加时，模型 ID 和显示名称均可填写下列原始 ID。")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                DisclosureGroup("2. 添加模型（\(models.count) 个，点击复制 ID）") {
                    VStack(alignment: .leading, spacing: 8) {
                        ForEach(models) { item in
                            field("模型 ID / 名称", item.id)
                            Text(APIConnectionInfo.capabilityNotice(item.id))
                                .font(.caption).foregroundStyle(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }.padding(.top, 8)
                }
                HStack {
                    Button("复制全部接入信息") { copy(info.summary) }
                    Button("复制填写指引") { copy(info.setupGuide(modelIDs: models.map(\.id))) }
                }
                Text("模型能力与客户端配置").font(.headline)
                Text("GLM-5.2、DeepSeek-V4-Pro 当前仅接受文本。带图片的历史切换模型时，由客户端按能力处理，Relay 不删除输入。通用字段不会自动声明这些限制。")
                    .font(.caption).fixedSize(horizontal: false, vertical: true)
                Button("复制 OpenCode 配置补充（当前版）") {
                    copy(info.openCodeConfiguration(modelIDs: models.map(\.id)))
                }
                Text("将 providers.pcl-relay 合并到现有配置，勿覆盖其他提供商、skills 或插件。补充配置启用上述两个已验证文本模型；其他模型列出但暂禁用，逐项确认能力后再启用。已有配置只合并需要的模型字段，保留推理设置。图片可能不再发给文本模型，必要时先提供文字摘要。")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                Text("Kimi 回复执行意图后停止：不等于断网。保留原会话，整理已完成内容、实际文件及剩余任务，在独立会话先验证小任务；不要反复强迫一次生成整个脚本。")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                Text("当前目录包含 \(models.count) 个文本模型，与 Codex 中是否勾选无关。以上是通用接入字段，不是某个客户端的配置文件；请填入所用客户端的对应设置。")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                Text("新增模型后刷新客户端目录，或手动补充模型 ID。目录存在不代表每个模型已通过工具调用验收。")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                Text("3. 保存并验证").font(.headline)
                Text("保存后刷新客户端，并检查模型是否已启用显示。选中一个 PCL 模型发送短消息，收到回复才算接入成功；仅复制或保存配置不算。")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                DisclosureGroup("遇到错误怎么办？") {
                    Text("保存时提示不支持自定义提供商：检查客户端当前版本支持的接入方式；这不等于中转站故障。\n超时 / 域名无法解析：检查该设备的 Tailnet 网络。\n401 / 403：确认错误来源和认证方式，不要使用 Codex 登录凭据。\n模型不存在：刷新模型目录并核对原始 ID。")
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
