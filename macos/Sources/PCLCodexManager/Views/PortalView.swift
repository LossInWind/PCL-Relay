import AppKit
import SwiftUI

struct PortalView: View {
    @EnvironmentObject private var model: AppModel

    private var available: Bool { model.portalStatus?.available == true }
    private var relayName: String {
        shortDeviceName(model.currentRelay?.nodeName ?? model.relayNodeName)
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                HStack(spacing: 20) {
                    ZStack {
                        RoundedRectangle(cornerRadius: 18, style: .continuous)
                            .fill((available ? Color.green : Color.blue).opacity(0.13))
                        Image(systemName: available ? "globe.badge.chevron.backward" : "network.badge.shield.half.filled")
                            .font(.system(size: 28, weight: .semibold))
                            .foregroundStyle(available ? .green : .blue)
                    }
                    .frame(width: 62, height: 62)

                    VStack(alignment: .leading, spacing: 7) {
                        Text("PCL 内网页面")
                            .font(.system(size: 22, weight: .semibold, design: .rounded))
                        Text(available ? "已验证可通过当前中转站访问" : "通过 Tailnet 中转打开 PCL API 广场")
                            .font(.subheadline.weight(.medium))
                        Text("登录、查看用量和管理 API Key 都在 PCL 官方网页中完成。")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }

                    Spacer()

                    VStack(alignment: .trailing, spacing: 8) {
                        StatusPill(
                            title: available ? "门户可用" : "尚未验证",
                            active: available,
                            symbol: available ? "checkmark.circle.fill" : "questionmark.circle.fill"
                        )
                        if let latency = model.portalStatus?.latencyMS {
                            Text("\(latency) ms")
                                .font(.caption.monospacedDigit())
                                .foregroundStyle(.secondary)
                        }
                    }
                }
                .padding(20)
                .background(Color(nsColor: .controlBackgroundColor).opacity(0.74), in: RoundedRectangle(cornerRadius: 20, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 20).stroke(Color.white.opacity(0.08)))

                SectionHeader(
                    title: "打开页面",
                    subtitle: "浏览器使用独立登录资料；只有 PCL 域名经过中转站",
                    actionTitle: model.isCheckingPortal ? "正在检测" : "测试访问",
                    actionSymbol: "arrow.clockwise",
                    action: model.refreshPortal
                )

                HStack(spacing: 12) {
                    PortalDestinationCard(
                        title: "API 广场",
                        detail: "查看模型、服务状态和调用入口",
                        symbol: "square.grid.2x2.fill",
                        tint: .blue,
                        actionTitle: "打开广场",
                        disabled: model.isOpeningPortal,
                        action: { model.openPortal(path: "/") }
                    )
                    PortalDestinationCard(
                        title: "用量与钱包",
                        detail: "查看余额、额度和历史用量",
                        symbol: "chart.bar.xaxis",
                        tint: .green,
                        actionTitle: "查看用量",
                        disabled: model.isOpeningPortal,
                        action: { model.openPortal(path: "/wallet") }
                    )
                    PortalDestinationCard(
                        title: "API Key",
                        detail: "创建、撤销或更换访问密钥",
                        symbol: "key.fill",
                        tint: .orange,
                        actionTitle: "管理 Key",
                        disabled: model.isOpeningPortal,
                        action: { model.openPortal(path: "/keys") }
                    )
                }

                VStack(alignment: .leading, spacing: 14) {
                    HStack {
                        Label("当前访问路径", systemImage: "point.3.connected.trianglepath.dotted")
                            .font(.headline)
                        Spacer()
                        Text("不修改系统代理")
                            .font(.caption.weight(.medium))
                            .foregroundStyle(.green)
                    }

                    HStack(spacing: 10) {
                        PortalRouteNode(symbol: "laptopcomputer", title: "专用浏览器", detail: "当前 Mac")
                        FlowArrow()
                        PortalRouteNode(symbol: "lock.shield.fill", title: "Tailscale", detail: "个人 Tailnet")
                        FlowArrow()
                        PortalRouteNode(symbol: "server.rack", title: relayName, detail: "受限转发")
                        FlowArrow()
                        PortalRouteNode(symbol: "globe.asia.australia.fill", title: "PCL 门户", detail: "llmapi.pcl.ac.cn")
                    }

                    Divider().opacity(0.5)

                    VStack(alignment: .leading, spacing: 7) {
                        Label("使用独立浏览器资料保存 PCL 登录状态，不读取你的日常浏览器 Cookie。", systemImage: "person.crop.circle.badge.checkmark")
                        Label("中转站只允许 pcl.ac.cn 域名，不能用于访问其他网站。", systemImage: "checkmark.shield.fill")
                        Label("应用不会显示、下载或记录现有 API Key。", systemImage: "eye.slash.fill")
                    }
                    .font(.caption)
                    .foregroundStyle(.secondary)
                }
                .padding(18)
                .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 16).stroke(Color.white.opacity(0.075)))

                if let error = model.portalStatus?.error, !error.isEmpty {
                    Label(error, systemImage: "exclamationmark.triangle.fill")
                        .font(.caption)
                        .foregroundStyle(.orange)
                        .padding(12)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Color.orange.opacity(0.08), in: RoundedRectangle(cornerRadius: 10))
                }
            }
            .padding(22)
        }
    }
}

private struct PortalDestinationCard: View {
    let title: String
    let detail: String
    let symbol: String
    let tint: Color
    let actionTitle: String
    let disabled: Bool
    let action: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Image(systemName: symbol)
                .font(.system(size: 21, weight: .semibold))
                .foregroundStyle(tint)
                .frame(width: 42, height: 42)
                .background(tint.opacity(0.12), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
            VStack(alignment: .leading, spacing: 4) {
                Text(title).font(.headline)
                Text(detail).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            Button(actionTitle, action: action)
                .buttonStyle(SecondaryButtonStyle())
                .disabled(disabled)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16)
        .background(Color(nsColor: .controlBackgroundColor).opacity(0.58), in: RoundedRectangle(cornerRadius: 15, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 15).stroke(Color.white.opacity(0.075)))
    }
}

private struct PortalRouteNode: View {
    let symbol: String
    let title: String
    let detail: String

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: symbol).foregroundStyle(.blue)
            VStack(alignment: .leading, spacing: 1) {
                Text(title).font(.caption.weight(.semibold))
                Text(detail).font(.caption2).foregroundStyle(.secondary)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(10)
        .background(Color.white.opacity(0.045), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
    }
}
