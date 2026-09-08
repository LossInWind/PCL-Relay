import BridgeCore
import SwiftUI

struct RoutingOverview: View {
    @EnvironmentObject private var model: AppModel
    var onUpgrade: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 6) {
                    Text("设备与路由").font(.title2.weight(.semibold))
                    Text("查看谁在线、请求经过哪里，以及哪些设备需要处理。")
                        .font(.subheadline).foregroundStyle(.secondary)
                }
                Spacer()
                Button { Task { await model.checkRoutingDashboard() } } label: {
                    Label(model.isCheckingRoutingDashboard ? "检查中…" : "检查全部", systemImage: "arrow.clockwise")
                }.disabled(model.isCheckingRoutingDashboard)
                Button("统一升级", action: onUpgrade).buttonStyle(.borderedProminent)
            }
            HStack(spacing: 24) {
                Label("\(model.routingDevices.filter { $0.online == true }.count) 台在线", systemImage: "desktopcomputer")
                Label("\((model.gatewayRoutes?.gateways ?? []).filter { $0.healthy == false }.count) 个接入点检查失败", systemImage: "arrow.triangle.branch")
                Label("\(model.routingDevices.filter(\.needsRestart).count) 台待完成升级", systemImage: "shippingbox")
                Spacer()
            }.font(.subheadline)
            if let error = model.routingCheckError {
                Label(error, systemImage: "exclamationmark.triangle").font(.caption).foregroundStyle(.orange)
            } else if let date = model.routingCheckedAt {
                Text("最近检查 \(date.formatted(date: .omitted, time: .standard)) · 在线不等于模型可用")
                    .font(.caption).foregroundStyle(.secondary)
            } else {
                Text("尚未完成全部检查 · 远端模型路径未验证时不会显示为正常")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
    }
}

struct RoutingDeviceList: View {
    @EnvironmentObject private var model: AppModel
    var onAdd: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("设备").font(.headline)
                Text("\(model.routingDevices.count)").foregroundStyle(.secondary)
                Spacer()
                Menu {
                    Button("登记 SSH 设备", action: onAdd)
                    Button("从 SSH 配置导入") { Task { await model.importDeploymentTargetsFromSSH() } }
                } label: { Label("添加设备", systemImage: "plus") }
            }
            VStack(spacing: 0) {
                ForEach(model.routingDevices) { device in
                    deviceRow(device)
                    if device.id != model.routingDevices.last?.id { Divider().padding(.leading, 48) }
                }
            }
            .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(Color.primary.opacity(0.08)))
        }
    }

    private func deviceRow(_ device: RoutingDevice) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 14) {
                Image(systemName: device.isLocal ? "laptopcomputer" : "server.rack")
                    .font(.title3).foregroundStyle(.secondary).frame(width: 24)
                Button {
                    model.selectedRoutingDeviceID = model.selectedRoutingDeviceID == device.id ? nil : device.id
                } label: {
                    VStack(alignment: .leading, spacing: 5) {
                        Text(device.name).font(.subheadline.weight(.semibold)).foregroundStyle(.primary)
                        Text(device.connectionTitle).font(.caption).foregroundStyle(.secondary)
                    }.frame(maxWidth: .infinity, alignment: .leading).contentShape(Rectangle())
                }.buttonStyle(.plain).accessibilityLabel("\(device.name)，展开设备详情")
                Label(device.versionTitle, systemImage: device.needsRestart ? "arrow.clockwise.circle" : "circle.fill")
                    .font(.caption).foregroundStyle(device.needsRestart ? Color.orange : Color.secondary)
                    .frame(width: 150, alignment: .leading)
                Button(model.checkingRoutingDevices.contains(device.id) ? "检测中…" : "检测") {
                    Task { await model.checkRoutingDevice(device) }
                }.disabled(model.checkingRoutingDevices.contains(device.id) || model.isCheckingRoutingDashboard)
                Menu {
                    Button("查看详情") { model.selectedRoutingDeviceID = device.id }
                    ForEach(device.targets) { target in
                        Button("移除登记：\(target.sshTarget)") { Task { await model.removeDeploymentTarget(target) } }
                    }
                    ForEach(device.peers) { peer in
                        Button("移除同步关系：\(peer.name)") { Task { await model.removeRelaySyncPeer(peer) } }
                    }
                } label: { Image(systemName: "ellipsis") }.menuStyle(.borderlessButton).frame(width: 28)
            }
            if model.selectedRoutingDeviceID == device.id {
                VStack(alignment: .leading, spacing: 8) {
                    Text(device.isLocal || device.runtime?.configuredEndpoint != nil ? "模型路径见上方；配置与接口检测不等同于模型调用验收。" : "模型路径：未确认。当前心跳只证明控制服务可达，不推测该设备经过哪个中转站。")
                        .font(.callout)
                    LabeledContent(device.isLocal ? "当前应用版本" : "后台同步版本", value: device.runningVersion ?? "未知")
                    LabeledContent("磁盘安装版本", value: device.isLocal ? model.routingRuntime?.installedVersion ?? "未知" : device.installedVersion ?? device.runtime?.installedVersion ?? "未知")
                    LabeledContent("OpenCodex 运行版本", value: (device.isLocal ? model.routingRuntime : device.runtime)?.opencodexVersion ?? "尚未报告")
                    if let runtime = device.isLocal ? model.routingRuntime : device.runtime {
                        LabeledContent("已配置模型接入点", value: runtime.configuredEndpoint ?? "未报告")
                        LabeledContent("状态采集时间", value: runtime.checkedAt ?? "未知")
                    }
                    if device.needsRestart {
                        Label("已安装新版，后台仍运行旧版；尚未完成升级验收。", systemImage: "exclamationmark.triangle")
                            .foregroundStyle(.orange)
                    }
                    ForEach(device.targets) { target in
                        LabeledContent("SSH", value: target.sshTarget)
                        LabeledContent("控制地址", value: target.controlURL)
                        if let error = target.error, !error.isEmpty { Text(error).foregroundStyle(.orange).textSelection(.enabled) }
                    }
                    ForEach(device.peers) { peer in
                        LabeledContent("同步地址", value: peer.url)
                        if !peer.error.isEmpty { Text(peer.error).foregroundStyle(.orange).textSelection(.enabled) }
                    }
                }.font(.caption).foregroundStyle(.secondary).padding(.leading, 38)
            }
        }.padding(16)
    }
}
