import BridgeCore
import SwiftUI

struct RoutingView: View {
    @EnvironmentObject private var model: AppModel
    @State private var showAddGateway = false
    @State private var showAddPeer = false
    @State private var showSyncService = false
    @State private var showAddDeploymentTarget = false

    private var selectedRoute: GatewayRouteRecord? {
        guard let selected = model.selectedGatewayID else { return nil }
        return model.gatewayRoutes?.gateways.first { $0.id == selected }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                SectionHeader(
                    title: "模型路由拓扑",
                    subtitle: "实线表示模型数据流，虚线表示 Relay 同步与软件部署控制流；底层网络仍由外部基础设施负责"
                )
                routingGraph
                routeInspector
                OpenCodexProxyCard()
                syncGraph
                deploymentGraph
                responsibilityNote
                LocalReleaseUpdateStrip()
            }
            .padding(22)
        }
        .sheet(isPresented: $showAddGateway) {
            AddGatewaySheet(isPresented: $showAddGateway).environmentObject(model)
        }
        .sheet(isPresented: $showAddPeer) {
            AddRelayPeerSheet(isPresented: $showAddPeer).environmentObject(model)
        }
        .sheet(isPresented: $showSyncService) {
            ConfigureRelaySyncServiceSheet(isPresented: $showSyncService).environmentObject(model)
        }
        .sheet(isPresented: $showAddDeploymentTarget) {
            AddDeploymentTargetSheet(isPresented: $showAddDeploymentTarget).environmentObject(model)
        }
    }

    private var routingGraph: some View {
        GlassCard {
            VStack(alignment: .leading, spacing: 18) {
                HStack {
                    Label("节点—边逻辑拓扑", systemImage: "point.3.filled.connected.trianglepath.dotted").font(.headline)
                    Spacer()
                    Button { Task { await model.refreshRoutes() } } label: {
                        Label(model.isRefreshingRoutes ? "检查中" : "协议检查", systemImage: "arrow.clockwise")
                    }
                    .buttonStyle(QuietButtonStyle()).disabled(model.isRefreshingRoutes)
                    Button { showAddGateway = true } label: { Label("添加中转站", systemImage: "plus") }
                        .buttonStyle(SecondaryButtonStyle())
                }
                RelayTopologyCanvas().environmentObject(model)
            }
        }
    }

    @ViewBuilder
    private var routeInspector: some View {
        if let route = selectedRoute {
            HStack(spacing: 12) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(route.name).font(.headline)
                    Text(route.url).font(.caption.monospaced()).foregroundStyle(.secondary).textSelection(.enabled)
                }
                Spacer()
                if !route.selected {
                    Button("移除") { Task { await model.removeGatewayRoute(route) } }.buttonStyle(QuietButtonStyle())
                    Button(model.isSwitchingGateway ? "切换中" : "设为当前中转站") {
                        Task { await model.selectGatewayRoute(route) }
                    }
                    .buttonStyle(PrimaryButtonStyle())
                    .disabled(model.isSwitchingGateway || route.healthy == false)
                } else {
                    Label("pcl/* 当前使用", systemImage: "checkmark.circle.fill")
                        .font(.subheadline.weight(.semibold)).foregroundStyle(.green)
                }
            }
            .padding(.horizontal, 16).padding(.vertical, 13)
            .background(Color(nsColor: .controlBackgroundColor).opacity(0.62), in: RoundedRectangle(cornerRadius: 15))
            .overlay(RoundedRectangle(cornerRadius: 15).stroke(Color.white.opacity(0.065)))
        }
    }

    private var syncGraph: some View {
        GlassCard {
            VStack(alignment: .leading, spacing: 15) {
                HStack {
                    VStack(alignment: .leading, spacing: 3) {
                        Text("Relay 多端同步").font(.headline)
                        Text("pcl-relay-topology/1 · 心跳与状态同步不承载模型流量")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button(model.relaySyncService?.active == true ? "停止本机心跳" : "配置本机心跳") {
                        if model.relaySyncService?.active == true {
                            Task { _ = await model.setRelaySyncServiceEnabled(false) }
                        } else {
                            showSyncService = true
                        }
                    }
                    .buttonStyle(QuietButtonStyle()).disabled(model.isTogglingSyncService)
                    Button { Task { await model.refreshRelaySync() } } label: {
                        Label("心跳", systemImage: "waveform.path.ecg")
                    }
                    .buttonStyle(QuietButtonStyle()).disabled(model.isRefreshingSync)
                    Button { Task { await model.synchronizeRelayTopology() } } label: {
                        Label(model.isSynchronizing ? "同步中" : "立即同步", systemImage: "arrow.triangle.2.circlepath")
                    }
                    .buttonStyle(SecondaryButtonStyle()).disabled(model.isSynchronizing)
                    Button { showAddPeer = true } label: { Label("添加节点", systemImage: "plus") }
                        .buttonStyle(SecondaryButtonStyle())
                }
                HStack(spacing: 10) {
                    RelayPeerNode(
                        name: model.relaySync?.nodeName ?? "当前设备",
                        detail: "本机 · r\(model.relaySync?.revision.counter ?? 0)",
                        online: true,
                        current: true
                    )
                    if !(model.relaySync?.peers.isEmpty ?? true) { FlowArrow() }
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 10) {
                            ForEach(model.relaySync?.peers ?? []) { peer in
                                VStack(spacing: 5) {
                                    RelayPeerNode(
                                        name: peer.name,
                                        detail: peer.online == true ? "\(peer.latencyMS ?? 0) ms · r\(peer.revision?.counter ?? 0)" : "心跳不可达",
                                        online: peer.online == true,
                                        current: false
                                    )
                                    Button("移除") { Task { await model.removeRelaySyncPeer(peer) } }
                                        .buttonStyle(.plain).font(.caption2).foregroundStyle(.secondary)
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    private var deploymentGraph: some View {
        GlassCard {
            VStack(alignment: .leading, spacing: 14) {
                HStack {
                    VStack(alignment: .leading, spacing: 3) {
                        Text("首次安装与拓扑接入").font(.headline)
                        Text("只操作你明确登记的 SSH 别名；不扫描 Tailnet，不同步 SSH 配置或密钥")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button { Task { await model.refreshDeploymentTargets() } } label: {
                        Label(model.isRefreshingDeploymentTargets ? "检查中" : "检查节点", systemImage: "arrow.clockwise")
                    }
                    .buttonStyle(QuietButtonStyle()).disabled(model.isRefreshingDeploymentTargets || model.isDeployingTopology)
                    Button { Task { await model.importDeploymentTargetsFromSSH() } } label: {
                        Label(model.isAddingDeploymentTarget ? "导入中" : "从 SSH 配置导入", systemImage: "square.and.arrow.down")
                    }
                    .buttonStyle(QuietButtonStyle()).disabled(model.isAddingDeploymentTarget || model.isDeployingTopology)
                    Button { showAddDeploymentTarget = true } label: {
                        Label("登记安装节点", systemImage: "plus")
                    }
                    .buttonStyle(SecondaryButtonStyle()).disabled(model.isDeployingTopology)
                    Button(model.isDeployingTopology ? "正在部署" : "一键安装/接入全部") {
                        model.deployLatestToRegisteredTargets()
                    }
                    .buttonStyle(PrimaryButtonStyle())
                    .disabled(
                        model.isDeployingTopology
                        || (model.deploymentTargets?.count ?? 0) == 0
                        || model.releaseUpdate?.topologyDeploymentReady != true
                    )
                }

                if model.releaseUpdate?.localNewerThanPublished == true {
                    Label(
                        "本机 \(localAppVersion) 尚未发布齐三个平台资产（GitHub 当前 \(model.releaseUpdate?.latestVersion ?? "未知")），为避免安装旧版，已暂停全网部署。",
                        systemImage: "exclamationmark.shield"
                    )
                    .font(.caption).foregroundStyle(.orange)
                }

                if let targets = model.deploymentTargets?.targets, !targets.isEmpty {
                    ForEach(targets) { target in
                        HStack(spacing: 12) {
                            Circle()
                                .fill(target.receiverOnline == true ? Color.green : (target.ssh == true ? Color.blue : Color.orange))
                                .frame(width: 9, height: 9)
                            VStack(alignment: .leading, spacing: 3) {
                                HStack(spacing: 7) {
                                    Text(target.name).font(.subheadline.weight(.semibold))
                                    Text(target.system.map { "\($0) \(target.architecture ?? "")" } ?? "尚未检查")
                                        .font(.caption2.monospaced()).foregroundStyle(.secondary)
                                }
                                Text("\(target.sshTarget)  →  \(target.controlURL)")
                                    .font(.caption.monospaced()).foregroundStyle(.secondary).textSelection(.enabled)
                            }
                            Spacer()
                            Text(deploymentState(target))
                                .font(.caption.weight(.medium))
                                .foregroundStyle(target.receiverOnline == true ? .green : (target.ssh == true ? .blue : .orange))
                            Button("移除登记") { Task { await model.removeDeploymentTarget(target) } }
                                .buttonStyle(QuietButtonStyle()).disabled(model.isDeployingTopology)
                        }
                        .padding(.horizontal, 13).padding(.vertical, 10)
                        .background(Color.secondary.opacity(0.055), in: RoundedRectangle(cornerRadius: 11))
                    }
                } else {
                    HStack(spacing: 10) {
                        Image(systemName: "info.circle").foregroundStyle(.blue)
                        Text("0 个已登记节点。只需填写一个已经能登录的 SSH 别名；PCL Relay 会自动解析地址、识别 Mac/Linux 并接入 15726。")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
        }
    }

    private func deploymentState(_ target: DeploymentTarget) -> String {
        if target.receiverOnline == true {
            return "已接入 · \(target.relayVersion ?? target.version ?? "未知版本")"
        }
        if target.ssh == true {
            return target.installed == true ? "待启动接收端" : "可首次安装"
        }
        return "离线或 SSH 不可达"
    }

    private var localAppVersion: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "未知版本"
    }

    private var responsibilityNote: some View {
        GlassCard {
            HStack(alignment: .top, spacing: 14) {
                Image(systemName: "square.3.layers.3d.top.filled").font(.system(size: 22)).foregroundStyle(.blue)
                VStack(alignment: .leading, spacing: 6) {
                    Text("独立应用边界").font(.headline)
                    Text("这里只保存逻辑 endpoint、模型目录、Relay 节点身份、拓扑版本与心跳结果。首次安装仅使用用户明确登记的本机 SSH alias；不读取或发布其他 App 的状态，不扫描 Tailnet，不发现代理/VPN，也不控制隧道、端口映射或文件挂载。")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
        }
    }
}

private struct GatewayTopologyNode: View {
    let route: GatewayRouteRecord
    let focused: Bool
    private var tint: Color {
        if route.selected { return .green }
        if route.healthy == false { return .orange }
        return .purple
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Image(systemName: "server.rack").foregroundStyle(tint)
                Text(route.name).font(.subheadline.weight(.semibold)).lineLimit(1)
                if route.selected { Image(systemName: "checkmark.circle.fill").foregroundStyle(.green) }
            }
            Text(route.url).font(.caption2.monospaced()).foregroundStyle(.secondary).lineLimit(1)
            HStack(spacing: 7) {
                Text(route.healthy == true ? "协议正常" : (route.healthy == false ? "协议异常" : "未检查"))
                if let count = route.modelCount { Text("\(count) 模型") }
                if let latency = route.latencyMS { Text("\(latency) ms") }
            }
            .font(.caption2.weight(.medium)).foregroundStyle(tint)
        }
        .padding(12).frame(width: 240, alignment: .leading)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 14))
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(focused ? tint : Color.white.opacity(0.1), lineWidth: focused ? 2 : 1))
        .contentShape(RoundedRectangle(cornerRadius: 14))
    }
}

private struct RelayPeerNode: View {
    let name: String
    let detail: String
    let online: Bool
    let current: Bool
    var body: some View {
        HStack(spacing: 9) {
            Circle().fill(online ? Color.green : Color.orange).frame(width: 9, height: 9)
            VStack(alignment: .leading, spacing: 2) {
                Text(name).font(.caption.weight(.semibold)).lineLimit(1)
                Text(detail).font(.caption2.monospaced()).foregroundStyle(.secondary)
            }
        }
        .padding(.horizontal, 11).padding(.vertical, 9).frame(width: 190, alignment: .leading)
        .background((current ? Color.blue : Color.secondary).opacity(0.08), in: RoundedRectangle(cornerRadius: 11))
    }
}

private struct OpenCodexProxyCard: View {
    @EnvironmentObject private var model: AppModel
    @State private var proxy = ""
    @State private var noProxy = ""

    var body: some View {
        DisclosureGroup("OpenCodex 显式 proxy / noProxy（高级）") {
            VStack(alignment: .leading, spacing: 10) {
                Text("这是 OpenCodex 原生配置的薄入口。默认不设置；只有你明确保存时才改变，不进行代理发现、测速、选节点或网络恢复。")
                    .font(.caption).foregroundStyle(.secondary)
                TextField("proxy，例如 http://127.0.0.1:7890；留空表示 unset", text: $proxy)
                    .font(.body.monospaced())
                TextField("noProxy，使用逗号分隔", text: $noProxy)
                    .font(.body.monospaced())
                HStack {
                    Text("该全局选项可能同时影响 official/* 与 pcl/*，保存前请确认。")
                        .font(.caption2).foregroundStyle(.orange)
                    Spacer()
                    if model.openCodexProxyPolicy?.restartRequired == true {
                        Button("空闲后应用") {
                            Task { await model.applyPendingOpenCodexProxyPolicy(); loadValues() }
                        }
                        .buttonStyle(QuietButtonStyle()).disabled(model.isSavingProxyPolicy)
                    }
                    Button("清除显式配置") { Task { await model.clearOpenCodexProxyPolicy(); loadValues() } }
                        .buttonStyle(QuietButtonStyle()).disabled(model.isSavingProxyPolicy)
                    Button(model.isSavingProxyPolicy ? "保存中" : "交给 OpenCodex 保存") {
                        Task { if await model.saveOpenCodexProxyPolicy(proxy: proxy, noProxy: noProxy) { loadValues() } }
                    }
                    .buttonStyle(SecondaryButtonStyle()).disabled(model.isSavingProxyPolicy)
                }
                if model.openCodexProxyPolicy?.restartRequired == true {
                    let count = model.openCodexProxyPolicy?.activeTurnCount
                    Text(count.map { "已保存，等待 \($0) 个活动请求结束后再由 OpenCodex 安全重启。" }
                         ?? "已保存，活动状态暂不可确认；为保护会话没有重启。")
                        .font(.caption2).foregroundStyle(.orange)
                }
            }
            .padding(.top, 10)
        }
        .font(.subheadline.weight(.medium))
        .onAppear { loadValues() }
        .onChange(of: model.openCodexProxyPolicy) { _, _ in loadValues() }
    }

    private func loadValues() {
        proxy = model.openCodexProxyPolicy?.proxy ?? ""
        noProxy = model.openCodexProxyPolicy?.noProxy.joined(separator: ", ") ?? ""
    }
}

private struct RouteDestination: View {
    let symbol: String
    let title: String
    let detail: String
    let color: Color
    var body: some View {
        HStack(spacing: 9) {
            Image(systemName: symbol).foregroundStyle(color)
            VStack(alignment: .leading, spacing: 1) {
                Text(title).font(.caption.weight(.semibold))
                Text(detail).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
            }
        }
        .padding(.horizontal, 11).padding(.vertical, 8).frame(maxWidth: .infinity, alignment: .leading)
        .background(color.opacity(0.09), in: RoundedRectangle(cornerRadius: 10))
    }
}

private struct AddGatewaySheet: View {
    @EnvironmentObject private var model: AppModel
    @Binding var isPresented: Bool
    @State private var name = ""
    @State private var url = ""
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("添加 PCL 中转站").font(.title2.weight(.semibold))
            Text("只登记逻辑 endpoint，并验证 /healthz 与 /v1/models；不会配置底层网络。")
                .font(.caption).foregroundStyle(.secondary)
            TextField("名称，例如 3070Ti", text: $name)
            TextField("http://host:15722/v1", text: $url).font(.body.monospaced())
            HStack {
                Spacer()
                Button("取消") { isPresented = false }
                Button(model.isAddingGateway ? "验证中" : "验证并添加") {
                    Task { if await model.addGatewayRoute(name: name, url: url) { isPresented = false } }
                }
                .buttonStyle(.borderedProminent)
                .disabled(model.isAddingGateway || url.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(24).frame(width: 520)
    }
}

private struct AddRelayPeerSheet: View {
    @EnvironmentObject private var model: AppModel
    @Binding var isPresented: Bool
    @State private var name = ""
    @State private var url = ""
    @State private var tokenFile = ""
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("添加 Relay 同步节点").font(.title2.weight(.semibold))
            Text("填写外部网络已提供的控制 endpoint。跨设备监听必须使用双方预置的 token 文件。")
                .font(.caption).foregroundStyle(.secondary)
            TextField("节点名称", text: $name)
            TextField("http://host:15726", text: $url).font(.body.monospaced())
            TextField("本机 token 文件路径", text: $tokenFile).font(.body.monospaced())
            HStack {
                Spacer()
                Button("取消") { isPresented = false }
                Button(model.isAddingSyncPeer ? "握手中" : "心跳验证并添加") {
                    Task { if await model.addRelaySyncPeer(name: name, url: url, tokenFile: tokenFile) { isPresented = false } }
                }
                .buttonStyle(.borderedProminent)
                .disabled(model.isAddingSyncPeer || url.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(24).frame(width: 560)
    }
}

private struct ConfigureRelaySyncServiceSheet: View {
    @EnvironmentObject private var model: AppModel
    @Binding var isPresented: Bool
    @State private var host = "0.0.0.0"
    @State private var port = "15726"
    @State private var tokenFile = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("配置本机 Relay 心跳").font(.title2.weight(.semibold))
            Text("默认监听 15726，并只接受 loopback 或 Tailscale 地址空间的来源；PCL Relay 不调用 Tailscale，只使用它已经提供的连通性。其他网络必须提供权限为 600 或 400 的 token 文件。")
                .font(.caption).foregroundStyle(.secondary)
            TextField("监听地址，默认 0.0.0.0（按来源限制）", text: $host).font(.body.monospaced())
            TextField("端口", text: $port).font(.body.monospaced())
            TextField("token 文件路径（非 loopback 必填）", text: $tokenFile).font(.body.monospaced())
            HStack {
                Spacer()
                Button("取消") { isPresented = false }
                Button(model.isTogglingSyncService ? "启动中" : "安装并启动") {
                    Task {
                        guard let selectedPort = Int(port) else { return }
                        if await model.setRelaySyncServiceEnabled(
                            true,
                            host: host,
                            port: selectedPort,
                            tokenFile: tokenFile
                        ) { isPresented = false }
                    }
                }
                .buttonStyle(.borderedProminent)
                .disabled(
                    model.isTogglingSyncService
                    || host.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                    || Int(port) == nil
                )
            }
        }
        .padding(24).frame(width: 570)
    }
}

private struct AddDeploymentTargetSheet: View {
    @EnvironmentObject private var model: AppModel
    @Binding var isPresented: Bool
    @State private var name = ""
    @State private var sshTarget = ""
    @State private var controlURL = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("登记首次安装节点").font(.title2.weight(.semibold))
            Text("只需填写已经由你或 Haichen Services 配好、能够登录的 SSH alias。PCL Relay 会从这一条 alias 自动解析 HostName，识别平台并使用 http://HostName:15726 验收；不会扫描其他设备。")
                .font(.caption).foregroundStyle(.secondary)
            TextField("显示名称，例如 Kai Mac", text: $name)
            TextField("SSH alias，例如 kai-mac", text: $sshTarget).font(.body.monospaced())
            TextField("控制 endpoint（可选；留空自动从 SSH HostName 推导）", text: $controlURL).font(.body.monospaced())
            VStack(alignment: .leading, spacing: 5) {
                Label("只登记字面 alias，不枚举 ~/.ssh/config，也不扫描 Tailnet", systemImage: "checkmark.shield")
                Label("远端先从 GitHub 下载并校验；失败后才从当前节点传输已校验缓存", systemImage: "shippingbox")
                Label("不改远端 Codex 历史、auth.json 或活动会话", systemImage: "clock.arrow.circlepath")
            }
            .font(.caption).foregroundStyle(.secondary)
            HStack {
                Spacer()
                Button("取消") { isPresented = false }
                Button(model.isAddingDeploymentTarget ? "验证中" : "登记并验证") {
                    Task {
                        if await model.addDeploymentTarget(name: name, sshTarget: sshTarget, controlURL: controlURL) {
                            isPresented = false
                        }
                    }
                }
                .buttonStyle(.borderedProminent)
                .disabled(
                    model.isAddingDeploymentTarget
                    || sshTarget.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                )
            }
        }
        .padding(24).frame(width: 620)
    }
}

private struct LocalReleaseUpdateStrip: View {
    @EnvironmentObject private var model: AppModel
    private var localVersion: String {
        model.releaseUpdate?.currentVersion
            ?? (Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String)
            ?? "未知"
    }
    private var summary: String {
        if model.appRestartRequired { return "新版本已安装，重新打开后生效" }
        if model.isCheckingAppUpdate { return "正在检查当前平台发布包" }
        if let latest = model.releaseUpdate?.latestVersion, model.releaseUpdate?.updateAvailable == true { return "可升级到 \(latest)" }
        if model.releaseUpdate?.localNewerThanPublished == true { return "本机版本尚未发布，暂不能全网部署" }
        if model.releaseUpdate?.available == true { return "已是已发布最新版" }
        return "按当前操作系统和架构选择发布包"
    }
    var body: some View {
        HStack(spacing: 14) {
            Image(systemName: "shippingbox.and.arrow.backward.fill")
                .font(.system(size: 19, weight: .medium)).foregroundStyle(.blue)
                .frame(width: 40, height: 40).background(Color.blue.opacity(0.11), in: RoundedRectangle(cornerRadius: 11))
            VStack(alignment: .leading, spacing: 3) {
                Text("当前设备版本更新").font(.subheadline.weight(.semibold))
                Text("PCL Relay \(localVersion) · \(summary)")
                    .font(.caption).foregroundStyle(model.releaseUpdate?.updateAvailable == true ? .orange : .secondary)
            }
            Spacer()
            Button(model.isCheckingAppUpdate ? "检查中" : "检查更新") { model.refreshAppUpdate() }
                .buttonStyle(QuietButtonStyle()).disabled(model.isCheckingAppUpdate || model.isInstallingAppUpdate)
            if model.releaseUpdate?.available == true {
                Button(model.isPushingTopologyUpdate ? "推送中" : "更新已接入节点（\(model.relaySync?.count ?? 0)）") {
                    model.pushLatestUpdateToTopology()
                }
                .buttonStyle(SecondaryButtonStyle())
                .disabled(
                    model.isPushingTopologyUpdate
                    || model.isInstallingAppUpdate
                    || (model.relaySync?.count ?? 0) == 0
                    || model.releaseUpdate?.topologyDeploymentReady != true
                )
            }
            if model.appRestartRequired {
                Button("重新打开应用") { model.restartApplication() }.buttonStyle(PrimaryButtonStyle())
            } else if model.releaseUpdate?.updateAvailable == true {
                Button(model.isInstallingAppUpdate ? "正在安装" : "一键更新最新版本") { model.installAppUpdate() }
                    .buttonStyle(PrimaryButtonStyle()).disabled(model.isInstallingAppUpdate)
            }
        }
        .padding(.horizontal, 16).padding(.vertical, 13)
        .background(Color(nsColor: .controlBackgroundColor).opacity(0.62), in: RoundedRectangle(cornerRadius: 15))
        .overlay(RoundedRectangle(cornerRadius: 15).stroke(Color.white.opacity(0.065)))
    }
}
