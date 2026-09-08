import BridgeCore
import SwiftUI

struct RoutingView: View {
    @EnvironmentObject private var model: AppModel
    @State private var showAddGateway = false
    @State private var showAddPeer = false
    @State private var showSyncService = false
    @State private var showAddDevice = false
    @State private var showAdvanced = false
    @State private var showUpgrade = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                RoutingOverview(onUpgrade: { showUpgrade = true })
                RelayTopologyCanvas()
                RoutingDeviceList(onAdd: { showAddDevice = true })
                DisclosureGroup("高级设置", isExpanded: $showAdvanced) {
                    VStack(alignment: .leading, spacing: 16) {
                        HStack {
                            Button("准备本机组件") { Task { await model.prepareLocalComponents() } }
                                .disabled(model.isPreparingLocalComponents)
                            Button("添加中转站") { showAddGateway = true }
                            Button("添加同步节点") { showAddPeer = true }
                            Button("立即同步配置") { Task { await model.synchronizeRelayTopology() } }
                                .disabled(model.isSynchronizing)
                            Button("心跳服务设置") { showSyncService = true }
                            if model.relaySyncService?.active == true {
                                Button("停止本机心跳") { Task { _ = await model.setRelaySyncServiceEnabled(false) } }
                                    .disabled(model.isTogglingSyncService)
                            }
                        }
                        OpenCodexProxyCard()
                        Text("Relay 只管理模型接入与软件更新，不修改 SSH、VPN、端口转发或文件挂载。检查不会应用路由或重启模型服务。")
                            .font(.caption).foregroundStyle(.secondary)
                    }.padding(.top, 12)
                }.font(.subheadline)
            }.padding(24)
        }
        .sheet(isPresented: $showAddGateway) { AddGatewaySheet(isPresented: $showAddGateway).environmentObject(model) }
        .sheet(isPresented: $showAddPeer) { AddRelayPeerSheet(isPresented: $showAddPeer).environmentObject(model) }
        .sheet(isPresented: $showSyncService) { ConfigureRelaySyncServiceSheet(isPresented: $showSyncService).environmentObject(model) }
        .sheet(isPresented: $showAddDevice) { AddDeploymentTargetSheet(isPresented: $showAddDevice).environmentObject(model) }
        .sheet(isPresented: $showUpgrade) {
            VStack(alignment: .leading, spacing: 18) {
                Text("统一升级").font(.title2.weight(.semibold))
                Text("先检查发布版本，再更新已接入设备。安装成功不代表后台已切换版本；返回设备列表检查运行版本。离线设备不计完成。")
                    .foregroundStyle(.secondary)
                LocalReleaseUpdateStrip()
                Button(model.isDeployingTopology ? "安装接入中…" : "安装 / 接入登记设备") {
                    model.deployLatestToRegisteredTargets()
                }.disabled(model.isDeployingTopology || model.releaseUpdate?.topologyDeploymentReady != true)
                if !model.commandLog.isEmpty {
                    DisclosureGroup("最近操作详情") {
                        ScrollView { Text(model.commandLog).font(.caption.monospaced()).textSelection(.enabled) }.frame(maxHeight: 220)
                    }
                }
                HStack { Spacer(); Button("返回") { showUpgrade = false }.keyboardShortcut(.cancelAction) }
            }.padding(24).frame(minWidth: 650)
        }
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
