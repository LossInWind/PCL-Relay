import SwiftUI

struct PortalView: View {
    @EnvironmentObject private var model: AppModel
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("PCL 门户").font(.title2.weight(.semibold))
                        Text("在专用浏览器中登录、查看用量和管理 API Key。")
                            .font(.subheadline).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button(model.isCheckingPortal ? "检查中…" : "测试访问", action: model.refreshPortal)
                        .disabled(model.isCheckingPortal)
                }
                VStack(alignment: .leading, spacing: 8) {
                    Label(model.checks["portal"]?.summary ?? "尚未检查门户访问", systemImage: "globe")
                        .foregroundStyle(model.checks["portal"]?.phase == .failed ? Color.orange : Color.secondary)
                    if model.checks["portal"]?.phase == .succeeded, let status = model.portalStatus {
                        Text("最近检查可达 · \(status.latencyMS) ms").font(.caption)
                    }
                }.font(.subheadline)
                VStack(spacing: 0) {
                    destination("API 广场", detail: "查看模型和调用入口", symbol: "square.grid.2x2", path: "/")
                    Divider()
                    destination("用量与钱包", detail: "查看余额和历史用量", symbol: "chart.bar", path: "/wallet")
                    Divider()
                    destination("API Key", detail: "在官方网站创建或更换密钥", symbol: "key", path: "/keys")
                }
                .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 12))
                .overlay(RoundedRectangle(cornerRadius: 12).stroke(Color.primary.opacity(0.08)))
                if !model.portalOpenMessage.isEmpty {
                    HStack(alignment: .top) {
                        if model.isOpeningPortal { ProgressView().controlSize(.small) }
                        Text(model.portalOpenMessage).textSelection(.enabled)
                            .foregroundStyle(model.portalOpenFailed ? Color.orange : Color.secondary)
                        Spacer()
                        if model.portalOpenFailed {
                            Button("重试") { model.openPortal(path: model.lastPortalPath) }.disabled(model.isOpeningPortal)
                        }
                    }.font(.callout)
                }
                LabeledContent("已配置访问接入点") {
                    Text(model.gatewayDisplayName).textSelection(.enabled)
                    Button("复制地址", action: model.copyGatewayURL)
                }.font(.caption)
                DisclosureGroup("访问方式与隐私") {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("专用浏览器 → 已配置中转站 → PCL 官方网站。网络状态以检测结果为准。")
                        Text("使用独立浏览器资料，不读取日常浏览器 Cookie；不会修改系统代理。")
                        Text("应用不读取或显示现有 API Key；登录和密钥操作在官方网页完成。")
                    }.font(.caption).foregroundStyle(.secondary).padding(.top, 10)
                }
            }.padding(24)
        }
    }
    private func destination(_ name: String, detail: String, symbol: String, path: String) -> some View {
        HStack(spacing: 14) {
            Image(systemName: symbol).font(.title3).foregroundStyle(.blue).frame(width: 28)
            VStack(alignment: .leading, spacing: 4) {
                Text(name).font(.subheadline.weight(.semibold))
                Text(detail).font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            Button("打开\(name)") { model.openPortal(path: path) }.disabled(model.isOpeningPortal)
        }.padding(18)
    }
}
