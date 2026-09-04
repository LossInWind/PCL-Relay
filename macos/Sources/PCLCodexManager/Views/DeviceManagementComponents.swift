import AppKit
import BridgeCore
import SwiftUI

struct RelayOverviewCard: View {
    @EnvironmentObject private var model: AppModel

    private var ready: Bool { model.relayReady && model.currentRelay != nil }
    private var currentName: String {
        shortDeviceName(model.currentRelay?.nodeName ?? model.relayNodeName)
    }

    var body: some View {
        HStack(spacing: 20) {
            ZStack {
                RoundedRectangle(cornerRadius: 18, style: .continuous)
                    .fill(ready ? Color.green.opacity(0.13) : Color.orange.opacity(0.13))
                Image(systemName: ready ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                    .font(.system(size: 28, weight: .semibold))
                    .foregroundStyle(ready ? .green : .orange)
            }
            .frame(width: 62, height: 62)

            VStack(alignment: .leading, spacing: 7) {
                Text(ready ? "PCL 网络已就绪" : "PCL 网络需要检查")
                    .font(.system(size: 22, weight: .semibold, design: .rounded))
                Text("当前中转站：\(currentName)")
                    .font(.subheadline.weight(.medium))
                Text("官方 GPT 保持不变；PCL 模型通过 Tailnet 中转或服务器本机直连接入。")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Spacer()

            HStack(spacing: 18) {
                OverviewValue(value: "\(model.tailnetNodes.filter(\.online).count)", label: "在线设备")
                Divider().frame(height: 32)
                OverviewValue(value: "\(model.relayDiscovery?.readyCount ?? 0)", label: "已接入")
                Divider().frame(height: 32)
                OverviewValue(value: "\(model.currentRelay?.modelCount ?? 0)", label: "模型")
            }
        }
        .padding(20)
        .background(Color(nsColor: .controlBackgroundColor).opacity(0.74), in: RoundedRectangle(cornerRadius: 20, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 20).stroke(Color.white.opacity(0.08)))
    }
}

private struct OverviewValue: View {
    let value: String
    let label: String
    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(value).font(.title2.weight(.semibold).monospacedDigit())
            Text(label).font(.caption).foregroundStyle(.secondary)
        }
        .frame(minWidth: 58, alignment: .leading)
    }
}

struct SoftwareUpdateStrip: View {
    @EnvironmentObject private var model: AppModel

    private var localVersion: String {
        model.releaseUpdate?.currentVersion
            ?? (Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String)
            ?? "未知"
    }

    private var latestVersion: String {
        guard let value = model.releaseUpdate?.latestVersion, !value.isEmpty else { return "尚未检查" }
        return value
    }

    private var localSummary: String {
        if model.appRestartRequired { return "新版本已安装，重新打开后生效" }
        if model.isCheckingAppUpdate { return "正在检查 GitHub Release" }
        if model.releaseUpdate?.updateAvailable == true { return "可升级到 \(latestVersion)" }
        if model.releaseUpdate?.available == true { return "已是最新版" }
        if let error = model.releaseUpdate?.error, !error.isEmpty { return "暂时无法检查更新" }
        return "从 GitHub Release 获取正式版本"
    }

    private var remoteSummary: String {
        if model.manageableRemoteClients.isEmpty { return "尚未发现可管理的远端客户端" }
        if model.remoteUpdateCandidates.isEmpty { return "\(model.manageableRemoteClients.count) 台远端设备已同步" }
        return "\(model.remoteUpdateCandidates.count) 台远端设备待同步"
    }

    var body: some View {
        HStack(spacing: 14) {
            Image(systemName: "shippingbox.and.arrow.backward.fill")
                .font(.system(size: 19, weight: .medium))
                .foregroundStyle(.blue)
                .frame(width: 40, height: 40)
                .background(Color.blue.opacity(0.11), in: RoundedRectangle(cornerRadius: 11))

            VStack(alignment: .leading, spacing: 3) {
                Text("版本更新").font(.subheadline.weight(.semibold))
                Text("本机 \(localVersion) · \(localSummary)")
                    .font(.caption)
                    .foregroundStyle(model.releaseUpdate?.updateAvailable == true ? .orange : .secondary)
            }

            Divider().frame(height: 34)

            VStack(alignment: .leading, spacing: 3) {
                Text("远端客户端").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                Text(remoteSummary).font(.caption)
            }

            Spacer(minLength: 12)

            Button { model.refreshAppUpdate() } label: {
                Label(model.isCheckingAppUpdate ? "检查中" : "检查更新", systemImage: "arrow.clockwise")
            }
            .buttonStyle(QuietButtonStyle())
            .disabled(model.isCheckingAppUpdate || model.isInstallingAppUpdate)

            if model.appRestartRequired {
                Button("重新打开应用") { model.restartApplication() }
                    .buttonStyle(PrimaryButtonStyle())
            } else if model.releaseUpdate?.updateAvailable == true {
                Button(model.isInstallingAppUpdate ? "正在安装" : "升级本机") { model.installAppUpdate() }
                    .buttonStyle(PrimaryButtonStyle())
                    .disabled(model.isInstallingAppUpdate)
            }

            if !model.remoteUpdateCandidates.isEmpty {
                Button(model.isUpdatingAllClients ? "正在同步" : remoteActionTitle) {
                    model.updateAllRemoteClients()
                }
                .buttonStyle(SecondaryButtonStyle())
                .disabled(remoteActionDisabled)
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 13)
        .background(Color(nsColor: .controlBackgroundColor).opacity(0.62), in: RoundedRectangle(cornerRadius: 15, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 15).stroke(Color.white.opacity(0.065)))
    }

    private var remoteActionTitle: String {
        if model.appRestartRequired { return "重启后同步远端" }
        if model.releaseUpdate?.updateAvailable == true { return "先升级本机" }
        if model.remoteUpdateCandidates.isEmpty { return "远端已同步" }
        return "同步远端 \(model.remoteUpdateCandidates.count) 台"
    }

    private var remoteActionDisabled: Bool {
        model.isUpdatingAllClients
            || model.installingClientTarget != nil
            || model.appRestartRequired
            || model.releaseUpdate?.updateAvailable == true
            || model.remoteUpdateCandidates.isEmpty
    }
}

struct UnifiedDeviceRow: View {
    @EnvironmentObject private var model: AppModel
    let node: RelayCandidate

    private var test: DeviceConnectivityTest? { model.deviceTests[node.tailscaleIP] }
    private var status: RemoteClientStatus? { node.clientStatus }
    private var refreshing: Bool { model.refreshingDeviceIDs.contains(node.tailscaleIP) }
    private var testing: Bool { model.testingDeviceIDs.contains(node.tailscaleIP) }
    private var available: Bool { test?.status == "ready" || (test == nil && node.online) }
    private var role: String {
        if node.selected { return "当前中转站" }
        if node.isSelf { return "当前 Mac" }
        if node.feasibility?.localPCLDirectActive == true { return "PCL 本机直连" }
        if node.feasibility?.relayCapable == true { return "可作为中转站" }
        if !node.online { return "离线" }
        return routeDisplayName(node.feasibility?.recommendedRoute ?? "unavailable")
    }
    private var summary: String {
        if let test { return test.summary }
        if !node.online {
            return node.nodeName.contains("bupt")
                ? "Tailscale 已离线；先通过北邮 VPN 应急入口登录并恢复 Tailscale"
                : "Tailnet 中未在线，无法从当前 Mac 访问"
        }
        if status?.updateAvailable == true {
            return "PCL Relay \(status?.clientVersion ?? "旧版") → \(status?.expectedClientVersion ?? "最新版")；可一键同步客户端与原生角色"
        }
        if status?.clientInstalled == true && status?.nativeRoles == false {
            return "检测到旧式委派配置；需要升级为 Codex 原生 custom roles"
        }
        if node.isSelf { return "本地 Codex 桌面版与 VS Code 使用当前中转站" }
        if node.feasibility?.localPCLDirectActive == true { return "服务器通过 127.0.0.1 本地适配器直接访问 PCL API" }
        return node.feasibility?.recommendationReason ?? "尚未完成连通性检查"
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 14) {
                Image(systemName: deviceSymbol(node))
                    .font(.system(size: 19, weight: .medium))
                    .foregroundStyle(available ? roleColor : .secondary)
                    .frame(width: 40, height: 40)
                    .background((available ? roleColor : Color.secondary).opacity(0.10), in: RoundedRectangle(cornerRadius: 11))

                VStack(alignment: .leading, spacing: 4) {
                    HStack(spacing: 8) {
                        Text(shortDeviceName(node.nodeName)).font(.headline)
                        Text(role)
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(roleColor)
                            .padding(.horizontal, 7).padding(.vertical, 3)
                            .background(roleColor.opacity(0.11), in: Capsule())
                    }
                    Text(summary)
                        .font(.caption)
                        .foregroundStyle(available ? Color.secondary : Color.orange)
                        .lineLimit(2)
                    Text("\(node.tailscaleIP)\(node.sshTarget.map { "  ·  SSH \($0)" } ?? "")")
                        .font(.system(.caption2, design: .monospaced))
                        .foregroundStyle(.tertiary)
                }

                Spacer(minLength: 12)

                if let latency = test?.latencyMS ?? node.latencyMS, available {
                    Text("\(latency) ms")
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(.secondary)
                }

                Button { model.refreshDevice(node) } label: {
                    Label(refreshing ? "刷新中" : "刷新", systemImage: "arrow.clockwise")
                }
                .buttonStyle(QuietButtonStyle())
                .disabled(refreshing || testing)

                Button { model.testDeviceConnectivity(node) } label: {
                    Label(testing ? "测试中" : "测试连通性", systemImage: "stethoscope")
                }
                .buttonStyle(QuietButtonStyle())
                .disabled(refreshing || testing)

                if node.feasibility?.relayCapable == true && node.gateway && node.pclAuth == "valid" && !node.selected {
                    Button("设为中转站") { model.selectRelay(node) }
                        .buttonStyle(SecondaryButtonStyle())
                        .disabled(model.isSelectingRelay)
                } else if !node.isSelf && node.online && node.clientStatus?.ssh == true && node.feasibility?.recommendedRoute != "unavailable" {
                    Button(remoteActionTitle) {
                        if status?.updateAvailable == true {
                            model.updateRemoteClient(node)
                        } else {
                            model.configureNode(node)
                        }
                    }
                    .buttonStyle(SecondaryButtonStyle())
                    .disabled(model.installingClientTarget != nil)
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 14)

            if let test {
                Divider().opacity(0.45).padding(.leading, 70)
                HStack(spacing: 16) {
                    ForEach(test.checks) { check in
                        HStack(spacing: 5) {
                            Image(systemName: check.passed ? "checkmark.circle.fill" : "xmark.circle.fill")
                            Text(check.name)
                            Text(check.detail).foregroundStyle(.secondary).lineLimit(1)
                        }
                        .font(.caption2)
                        .foregroundStyle(check.passed ? .green : .orange)
                    }
                    Spacer()
                    Text("刚刚检查").font(.caption2).foregroundStyle(.tertiary)
                }
                .padding(.leading, 70).padding(.trailing, 16).padding(.vertical, 9)
            }
        }
        .background(Color(nsColor: .controlBackgroundColor).opacity(0.62), in: RoundedRectangle(cornerRadius: 15, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 15).stroke(node.selected ? Color.green.opacity(0.32) : Color.white.opacity(0.06)))
    }

    private var roleColor: Color {
        if !node.online { return .orange }
        if node.selected { return .green }
        if node.feasibility?.localPCLDirectActive == true { return .cyan }
        return .blue
    }

    private var remoteActionTitle: String {
        if model.installingClientTarget == node.sshTarget { return "正在更新" }
        if status?.updateAvailable == true { return "升级到 \(status?.expectedClientVersion ?? "最新版")" }
        if status?.ready == true || node.feasibility?.localPCLDirectActive == true { return "重新配置" }
        return "接入"
    }
}

struct ServerControlCard: View {
    @EnvironmentObject private var model: AppModel
    @Binding var showLogs: Bool

    var body: some View {
        GlassCard {
            VStack(alignment: .leading, spacing: 14) {
                HStack(alignment: .top, spacing: 14) {
                    Image(systemName: "server.rack")
                        .font(.system(size: 23, weight: .medium))
                        .foregroundStyle(.green)
                        .frame(width: 48, height: 48)
                        .background(Color.green.opacity(0.12), in: RoundedRectangle(cornerRadius: 12))
                    VStack(alignment: .leading, spacing: 4) {
                        HStack(spacing: 8) {
                            Text("当前中转站设备").font(.headline)
                            StatusPill(title: model.doctor?.gateway == true ? "API 在线" : "API 离线", active: model.doctor?.gateway == true, symbol: "circle.fill")
                            StatusPill(title: model.remoteServiceActive ? "Tailnet 可运维" : "运维不可达", active: model.remoteServiceActive, symbol: "wrench.and.screwdriver.fill")
                        }
                        Text(model.serverStatus?.nodeName ?? model.relayNodeName).font(.title3.weight(.semibold)).textSelection(.enabled)
                        Text("MagicDNS  \(model.serverStatus?.magicDNS ?? model.relayMagicDNS)   ·   Tailscale IPv4  \(model.serverStatus?.tailscaleIP ?? model.relayTailscaleIP)")
                            .font(.system(.caption, design: .monospaced))
                            .foregroundStyle(.secondary)
                            .textSelection(.enabled)
                    }
                    Spacer()
                    VStack(alignment: .trailing, spacing: 3) {
                        Text("运维范围").font(.caption).foregroundStyle(.secondary)
                        Text("仅 pcl-codex-gateway 用户服务")
                            .font(.caption.weight(.medium)).foregroundStyle(.green)
                    }
                }

                HStack(spacing: 10) {
                    Button { model.refreshServerStatus() } label: {
                        Label("检查服务器", systemImage: "stethoscope")
                    }
                    .buttonStyle(SecondaryButtonStyle())

                    Button { model.restartGateway() } label: {
                        Label(model.isRestartingGateway ? "正在重启" : "重启网关服务", systemImage: "arrow.triangle.2.circlepath")
                    }
                    .buttonStyle(SecondaryButtonStyle())
                    .disabled(model.isRestartingGateway || !model.remoteServiceActive)

                    Button {
                        showLogs.toggle()
                        if showLogs { model.loadGatewayLogs() }
                    } label: {
                        Label(showLogs ? "收起服务日志" : "查看服务日志", systemImage: "doc.text.magnifyingglass")
                    }
                    .buttonStyle(SecondaryButtonStyle())
                    .disabled(!model.remoteServiceActive)

                    Spacer()
                    Text("Tailnet Admin API · 无任意命令执行")
                        .font(.system(.caption, design: .monospaced))
                        .foregroundStyle(.secondary)
                }

                if !model.remoteStatusText.isEmpty {
                    Text(model.remoteStatusText)
                        .font(.system(.caption2, design: .monospaced))
                        .foregroundStyle(.secondary)
                        .lineLimit(4)
                        .textSelection(.enabled)
                }
            }
        }
    }
}
