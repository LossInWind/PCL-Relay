import AppKit
import BridgeCore
import Foundation
import ServiceManagement
import SwiftUI

struct AgentDefinition: Identifiable, Hashable {
    let id: String
    let model: String
    let title: String
    let detail: String
    let symbol: String
    let tint: Color
    let family: String
    let category: String
    let recommended: Bool

    var nativeRoleName: String { id.replacingOccurrences(of: "_", with: "-") }

    init(id: String, model: String, title: String, detail: String, symbol: String, tint: Color, family: String, category: String, recommended: Bool) {
        self.id = id
        self.model = model
        self.title = title
        self.detail = detail
        self.symbol = symbol
        self.tint = tint
        self.family = family
        self.category = category
        self.recommended = recommended
    }

    static let all: [AgentDefinition] = [
        .init(id: "pcl_deepseek_pro", model: "DeepSeek-V4-Pro", title: "DeepSeek Pro", detail: "复杂编码、调试与推理", symbol: "brain.head.profile", tint: .cyan, family: "DeepSeek", category: "chat", recommended: true),
        .init(id: "pcl_deepseek_flash", model: "DeepSeek-V4-Flash-0731", title: "DeepSeek Flash", detail: "快速修改、测试与检索", symbol: "bolt.fill", tint: .blue, family: "DeepSeek", category: "chat", recommended: true),
        .init(id: "pcl_glm", model: "GLM-5.2", title: "GLM", detail: "中文技术任务与独立审查", symbol: "text.bubble.fill", tint: .purple, family: "GLM", category: "chat", recommended: true),
        .init(id: "pcl_kimi", model: "Kimi-K3", title: "Kimi", detail: "长上下文阅读与综合分析", symbol: "moon.stars.fill", tint: .indigo, family: "Kimi", category: "chat", recommended: true),
    ]

    init(model: DiscoveredModel) {
        id = model.alias
        self.model = model.id
        title = model.id
        detail = model.description
        family = model.family
        category = model.category
        recommended = model.recommended
        switch model.family.lowercased() {
        case "deepseek": symbol = model.id.lowercased().contains("flash") ? "bolt.fill" : "brain.head.profile"; tint = .cyan
        case "glm": symbol = "text.bubble.fill"; tint = .purple
        case "kimi": symbol = "moon.stars.fill"; tint = .indigo
        case "qwen": symbol = "q.circle.fill"; tint = .orange
        case "pcl": symbol = "server.rack"; tint = .green
        case "bge": symbol = "square.stack.3d.up.fill"; tint = .mint
        case "whisper": symbol = "waveform"; tint = .pink
        case "paddleocr": symbol = "text.viewfinder"; tint = .teal
        default: symbol = model.category == "image" ? "photo.fill" : "sparkles"; tint = .blue
        }
    }
}

@MainActor
final class AppModel: ObservableObject {
    @Published var doctor: DoctorStatus?
    @Published var registry: ModelRegistry?
    @Published var serverStatus: RelayServerStatus?
    @Published var portalStatus: PortalStatus?
    @Published var relayDiscovery: RelayDiscovery?
    @Published var selectedAgents = Set(AgentDefinition.all.map(\.id))
    @Published var remoteServiceActive = false
    @Published var remoteStatusText = "尚未检查"
    @Published var gatewayLogs = ""
    @Published var commandLog = ""
    @Published var isRefreshing = false
    @Published var isDetecting = false
    @Published var isDiscovering = false
    @Published var isSavingAgents = false
    @Published var isRestartingGateway = false
    @Published var isCheckingPortal = false
    @Published var isOpeningPortal = false
    @Published var isDiscoveringNodes = false
    @Published var isSelectingRelay = false
    @Published var releaseUpdate: ReleaseUpdateStatus?
    @Published var isCheckingAppUpdate = false
    @Published var isInstallingAppUpdate = false
    @Published var appRestartRequired = false
    @Published var isUpdatingAllClients = false
    @Published var installingClientTarget: String?
    @Published var isApplyingTopology = false
    @Published var topologyRoutes: [String: String] = [:]
    @Published var deviceTests: [String: DeviceConnectivityTest] = [:]
    @Published var refreshingDeviceIDs = Set<String>()
    @Published var testingDeviceIDs = Set<String>()
    @Published var banner: BannerMessage?
    @Published var launchAtLoginEnabled = false
    @Published var launchAtLoginStatusText = "正在配置登录启动"
    @Published var codexReloadRequired = false

    private let runner = CommandRunner()
    private var detectionJob: UUID?
    private var consensusMonitor: Task<Void, Never>?
    private var didStart = false
    private var didBootstrapClient = false
    private let fallbackLoginLabel = "cn.haichen.pcl-relay-login"
    let relayNodeName = "haichen-pcl-linux-3070ti"
    let relayMagicDNS = "haichen-pcl-linux-3070ti.tail132f30.ts.net"
    let relayTailscaleIP = "100.113.234.58"

    struct BannerMessage: Identifiable, Equatable {
        enum Kind { case success, error, info }
        let id = UUID()
        let text: String
        let kind: Kind
    }

    var gatewayURL: String {
        registry?.gateway ?? "http://haichen-pcl-linux-3070ti.tail132f30.ts.net:15722/v1"
    }

    var currentRelay: RelayCandidate? {
        relayDiscovery?.nodes.first(where: { $0.selected && $0.gateway })
    }

    var tailnetNodes: [RelayCandidate] {
        relayDiscovery?.nodes ?? []
    }

    var manageableRemoteClients: [RelayCandidate] {
        tailnetNodes.filter {
            !$0.isSelf
                && $0.online
                && $0.clientStatus?.ssh == true
                && !($0.sshTarget ?? "").isEmpty
                && $0.clientStatus?.supportedSystem != false
                && $0.feasibility?.recommendedRoute != "unavailable"
        }
    }

    var remoteUpdateCandidates: [RelayCandidate] {
        manageableRemoteClients.filter {
            $0.clientStatus?.updateAvailable == true
                || $0.clientStatus?.nativeV2 != true
                || $0.clientStatus?.nativeRoles != true
        }
    }

    var codexIntegrationReady: Bool {
        doctor?.codex == true
            && doctor?.configManaged == true
            && doctor?.nativeRouter == true
            && doctor?.nativeCatalog == true
            && doctor?.nativeV2 == true
            && doctor?.nativeRoles == true
    }

    var relayReady: Bool {
        doctor?.gateway == true && doctor?.tailscale == true
    }

    var allDiscoveredModels: [DiscoveredModel] {
        (registry?.availableModels?.values.map { $0 } ?? []).sorted {
            if $0.agentEligible != $1.agentEligible { return $0.agentEligible && !$1.agentEligible }
            if $0.recommended != $1.recommended { return $0.recommended && !$1.recommended }
            return $0.id.localizedCaseInsensitiveCompare($1.id) == .orderedAscending
        }
    }

    var agentOptions: [AgentDefinition] {
        let found = allDiscoveredModels.filter(\.agentEligible).map(AgentDefinition.init(model:))
        return found.isEmpty ? AgentDefinition.all : found
    }

    var readyAgentCount: Int {
        agentOptions.filter { registry?.models[$0.id]?.executionReady == true }.count
    }

    var partialAgentCount: Int {
        agentOptions.filter {
            guard let status = registry?.models[$0.id] else { return false }
            return !status.executionReady && (status.chat || status.stream == true || status.toolCompatible == true)
        }.count
    }

    private var installedCLIURL: URL {
        FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".local/bin/pcl-codex")
    }

    private var bundledCLIURL: URL? {
        Bundle.main.resourceURL?.appendingPathComponent("bridge/pcl-codex")
    }

    private var cliURL: URL? {
        if let bundledCLIURL, FileManager.default.isExecutableFile(atPath: bundledCLIURL.path) { return bundledCLIURL }
        if FileManager.default.isExecutableFile(atPath: installedCLIURL.path) { return installedCLIURL }
        return nil
    }

    func start() {
        guard !didStart else { return }
        didStart = true
        configureLaunchAtLogin()
        refreshAll()
        startConsensusMonitoring()
    }

    private func configureLaunchAtLogin() {
        let service = SMAppService.mainApp
        if service.status == .requiresApproval {
            launchAtLoginEnabled = false
            launchAtLoginStatusText = "登录启动需要在系统设置中允许"
            return
        }
        do {
            // Register only a genuinely new login item. If the user disabled
            // it in System Settings, macOS reports that approval is required;
            // do not silently override that choice.
            if service.status == .notRegistered {
                try service.register()
            }
            if service.status == .enabled {
                launchAtLoginEnabled = true
                launchAtLoginStatusText = "登录 Mac 后自动显示菜单栏图标"
            } else {
                // Locally distributed/ad-hoc-signed builds can remain
                // notRegistered even when register() returns successfully.
                // A one-shot user LaunchAgent is a reliable personal-install
                // fallback. It has no KeepAlive and therefore never relaunches
                // the app after an explicit Quit.
                try ensureFallbackLoginAgent()
                launchAtLoginEnabled = true
                launchAtLoginStatusText = "登录 Mac 后自动显示菜单栏图标"
            }
        } catch {
            if service.status == .requiresApproval {
                launchAtLoginEnabled = false
                launchAtLoginStatusText = "登录启动需要在系统设置中允许"
                return
            }
            do {
                try ensureFallbackLoginAgent()
                launchAtLoginEnabled = true
                launchAtLoginStatusText = "登录 Mac 后自动显示菜单栏图标"
            } catch {
                launchAtLoginEnabled = false
                launchAtLoginStatusText = "登录启动配置失败：\(error.localizedDescription)"
            }
        }
    }

    private func ensureFallbackLoginAgent() throws {
        let manager = FileManager.default
        let directory = manager.homeDirectoryForCurrentUser.appendingPathComponent("Library/LaunchAgents", isDirectory: true)
        try manager.createDirectory(at: directory, withIntermediateDirectories: true)
        let plistURL = directory.appendingPathComponent("\(fallbackLoginLabel).plist")
        let payload: [String: Any] = [
            "Label": fallbackLoginLabel,
            "ProgramArguments": ["/usr/bin/open", "-g", Bundle.main.bundleURL.path],
            "RunAtLoad": true,
            "KeepAlive": false,
            "ProcessType": "Interactive",
        ]
        let data = try PropertyListSerialization.data(fromPropertyList: payload, format: .xml, options: 0)
        try data.write(to: plistURL, options: .atomic)
        try manager.setAttributes([.posixPermissions: 0o644], ofItemAtPath: plistURL.path)

        let domain = "gui/\(getuid())"
        if launchctl(["print", "\(domain)/\(fallbackLoginLabel)"]) != 0 {
            let status = launchctl(["bootstrap", domain, plistURL.path])
            guard status == 0 else {
                throw NSError(
                    domain: "PCLRelay.LoginItem",
                    code: Int(status),
                    userInfo: [NSLocalizedDescriptionKey: "无法注册用户登录项（launchctl \(status)）"]
                )
            }
        }
    }

    @discardableResult
    private func launchctl(_ arguments: [String]) -> Int32 {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        process.arguments = arguments
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        do {
            try process.run()
            process.waitUntilExit()
            return process.terminationStatus
        } catch {
            return -1
        }
    }

    func refreshAll() {
        guard !isRefreshing else { return }
        isRefreshing = true
        Task {
            defer { isRefreshing = false }
            do {
                try await bootstrapClientIfNeeded()
                let doctorResult = try await runCLI(["doctor"])
                guard doctorResult.exitCode == 0 else { throw commandError(doctorResult) }
                doctor = try BridgeDecode.value(DoctorStatus.self, from: doctorResult.stdout)

                let registryResult = try await runCLI(["models", "show"])
                if registryResult.exitCode == 0 {
                    let decoded = try BridgeDecode.value(ModelRegistry.self, from: registryResult.stdout)
                    registry = decoded
                    selectedAgents = Set(decoded.selectedAgents ?? AgentDefinition.all.map(\.id))
                }
                remoteServiceActive = doctor?.gateway == true
                remoteStatusText = remoteServiceActive ? "已通过 Tailnet 健康检查" : (doctor?.gatewayError ?? "中转站不可达")
                Task { await refreshRemoteStatus() }
                Task { await discoverNodes(showBanner: false) }
                Task { await refreshPortalStatus(showBanner: false) }
                Task { await checkAppUpdate(showBanner: false) }
            } catch {
                show("刷新失败：\(error.localizedDescription)", .error)
            }
        }
    }

    func startConsensusMonitoring() {
        guard consensusMonitor == nil else { return }
        consensusMonitor = Task { [weak self] in
            let interval = 30.0
            while !Task.isCancelled {
                let now = Date().timeIntervalSince1970
                // Endpoint probes are allowed several seconds.  Read the
                // previous completed round after all nodes have had time to
                // publish, rather than exposing a partially updated graph.
                let nextRound = (floor(now / interval) + 1) * interval + 12
                try? await Task.sleep(for: .seconds(max(1, nextRound - now)))
                guard !Task.isCancelled, let self else { return }
                await self.discoverNodes(showBanner: false)
            }
        }
    }

    func refreshAppUpdate() {
        Task { await checkAppUpdate(showBanner: true) }
    }

    private func checkAppUpdate(showBanner: Bool) async {
        guard !isCheckingAppUpdate, !isInstallingAppUpdate else { return }
        isCheckingAppUpdate = true
        defer { isCheckingAppUpdate = false }
        do {
            let result = try await runCLI(["updates", "status"])
            guard result.exitCode == 0 else { throw commandError(result) }
            let decoded = try BridgeDecode.value(ReleaseUpdateStatus.self, from: result.stdout)
            releaseUpdate = decoded
            if showBanner {
                if decoded.updateAvailable {
                    show("发现 PCL Relay \(decoded.latestVersion)，可从 GitHub Release 升级", .info)
                } else if decoded.available {
                    show("本机 PCL Relay 已是最新版 \(decoded.currentVersion)", .success)
                } else {
                    show("暂时无法检查 GitHub Release：\(decoded.error)", .error)
                }
            }
        } catch {
            if showBanner { show("检查本机更新失败：\(error.localizedDescription)", .error) }
        }
    }

    func installAppUpdate() {
        guard !isInstallingAppUpdate else { return }
        isInstallingAppUpdate = true
        Task {
            defer { isInstallingAppUpdate = false }
            do {
                let result = try await runCLI(["updates", "install"])
                commandLog = BridgeDecode.prettyJSON(result.stdout)
                guard result.exitCode == 0 else { throw commandError(result) }
                appRestartRequired = true
                show("新版本已校验并安装；重新打开应用后即可升级远端设备", .success)
            } catch {
                show("本机升级失败：\(error.localizedDescription)", .error)
            }
        }
    }

    func restartApplication() {
        guard appRestartRequired else { return }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/sh")
        process.arguments = ["-c", "sleep 1; /usr/bin/open -a 'PCL Relay'"]
        try? process.run()
        NSApp.terminate(nil)
    }

    func discoverModels() {
        guard !isDiscovering else { return }
        isDiscovering = true
        commandLog = "正在从中转站读取最新模型目录……"
        Task {
            defer { isDiscovering = false }
            do {
                let result = try await runCLI(["models", "discover"])
                commandLog = BridgeDecode.prettyJSON(result.stdout)
                guard result.exitCode == 0 else { throw commandError(result) }
                let decoded = try BridgeDecode.value(ModelRegistry.self, from: result.stdout)
                registry = decoded
                selectedAgents = Set(decoded.selectedAgents ?? AgentDefinition.all.map(\.id))
                show("模型目录已更新：发现 \(decoded.availableModels?.count ?? 0) 个模型", .success)
            } catch {
                show("检查更新失败：\(error.localizedDescription)", .error)
            }
        }
    }

    func refreshRemoteStatus() async {
        do {
            let result = try await runCLI(["server", "status"])
            guard result.exitCode == 0 else { throw commandError(result) }
            let decoded = try BridgeDecode.value(RelayServerStatus.self, from: result.stdout)
            serverStatus = decoded
            remoteServiceActive = decoded.status == "active"
            remoteStatusText = "PID \(decoded.pid) · uptime \(decoded.uptimeSeconds)s · \(decoded.tailscaleIP):\(decoded.port) · scope: \(decoded.adminScope.joined(separator: ", "))"
        } catch {
            remoteServiceActive = false
            remoteStatusText = error.localizedDescription
        }
    }

    func detectModels() {
        guard !isDetecting else { return }
        let job = UUID()
        detectionJob = job
        isDetecting = true
        commandLog = "正在依次检测普通响应、SSE 流式输出和工具调用……"
        Task {
            defer {
                isDetecting = false
                detectionJob = nil
            }
            do {
                let result = try await runCLI(["models", "detect"], id: job)
                commandLog = BridgeDecode.prettyJSON(result.stdout) + (result.stderr.isEmpty ? "" : "\n" + result.stderr)
                guard result.exitCode == 0 else { throw commandError(result) }
                let decoded = try BridgeDecode.value(ModelRegistry.self, from: result.stdout)
                registry = decoded
                selectedAgents = Set(decoded.selectedAgents ?? AgentDefinition.all.map(\.id))
                show("已选子 Agent 能力检测完成", .success)
            } catch {
                show("模型检测停止：\(error.localizedDescription)", .error)
            }
        }
    }

    func cancelDetection() {
        guard let detectionJob else { return }
        runner.cancel(detectionJob)
        commandLog += "\n正在停止检测……"
    }

    func setAgent(_ id: String, enabled: Bool) {
        if enabled {
            selectedAgents.insert(id)
        } else if selectedAgents.count > 1 {
            selectedAgents.remove(id)
        } else {
            show("至少保留一个子 Agent", .info)
            return
        }
        saveSelectedAgents()
    }

    func saveSelectedAgents() {
        guard !isSavingAgents else { return }
        isSavingAgents = true
        let ordered = agentOptions.filter { selectedAgents.contains($0.id) }.map { option in
            registry?.availableModels?[option.model] != nil ? option.model : option.id
        }
        Task {
            defer { isSavingAgents = false }
            do {
                let result = try await runCLI(["models", "select"] + ordered)
                guard result.exitCode == 0 else { throw commandError(result) }
                show("Codex 原生子 Agent 已更新；新建任务或重新加载 Codex 后生效", .success)
            } catch {
                show("保存失败：\(error.localizedDescription)", .error)
            }
        }
    }

    func installCodexIntegration() {
        Task {
            do {
                let result = try await runCLI(["install", "client"])
                guard result.exitCode == 0 else { throw commandError(result) }
                show("PCL Relay 原生子 Agent 已安装/修复；请新建任务或重新加载 Codex", .success)
                refreshAll()
            } catch {
                show("安装失败：\(error.localizedDescription)", .error)
            }
        }
    }

    func restartGateway() {
        guard !isRestartingGateway else { return }
        isRestartingGateway = true
        Task {
            defer { isRestartingGateway = false }
            do {
                let result = try await runCLI(["server", "restart"])
                guard result.exitCode == 0 else { throw commandError(result) }
                await refreshRemoteStatus()
                show("中转站已重启", .success)
            } catch {
                show("重启失败：\(error.localizedDescription)", .error)
            }
        }
    }

    func loadGatewayLogs() {
        Task {
            do {
                let result = try await runCLI(["server", "logs"])
                guard result.exitCode == 0 else { throw commandError(result) }
                let decoded = try BridgeDecode.value(RelayServerLogs.self, from: result.stdout)
                gatewayLogs = decoded.lines.joined(separator: "\n")
            } catch {
                gatewayLogs = error.localizedDescription
            }
        }
    }

    func refreshServerStatus() {
        Task { await refreshRemoteStatus() }
    }

    func refreshPortal() {
        Task { await refreshPortalStatus(showBanner: true) }
    }

    private func refreshPortalStatus(showBanner: Bool) async {
        guard !isCheckingPortal else { return }
        isCheckingPortal = true
        defer { isCheckingPortal = false }
        do {
            let result = try await runCLI(["portal", "status"])
            guard result.exitCode == 0 else { throw commandError(result) }
            let decoded = try BridgeDecode.value(PortalStatus.self, from: result.stdout)
            portalStatus = decoded
            if showBanner {
                show(
                    decoded.available
                        ? "PCL 内网页面可用，延迟 \(decoded.latencyMS) ms"
                        : "PCL 内网页面暂不可用：\(decoded.error)",
                    decoded.available ? .success : .error
                )
            }
        } catch {
            if showBanner { show("门户检测失败：\(error.localizedDescription)", .error) }
        }
    }

    func openPortal(path: String) {
        guard !isOpeningPortal else { return }
        isOpeningPortal = true
        Task {
            defer { isOpeningPortal = false }
            do {
                let result = try await runCLI(["portal", "open", "--path", path])
                guard result.exitCode == 0 else { throw commandError(result) }
                let decoded = try BridgeDecode.value(PortalStatus.self, from: result.stdout)
                portalStatus = decoded
                show("已通过 \(decoded.browser ?? "浏览器") 打开 PCL 内网页面", .success)
            } catch {
                show("打开失败：\(error.localizedDescription)", .error)
            }
        }
    }

    func refreshTailnetNodes() {
        Task { await discoverNodes(showBanner: true) }
    }

    private func discoverNodes(showBanner: Bool) async {
        guard !isDiscoveringNodes else { return }
        isDiscoveringNodes = true
        defer { isDiscoveringNodes = false }
        do {
            let result = try await runCLI(["clients", "discover", "--timeout", "2"])
            guard result.exitCode == 0 else { throw commandError(result) }
            let decoded = try BridgeDecode.value(RelayDiscovery.self, from: result.stdout)
            relayDiscovery = decoded
            synchronizeTopologyRoutes()
            if showBanner {
                let relays = decoded.nodes.filter { $0.gateway && $0.pclAuth == "valid" }.count
                show("发现 \(relays) 个可用中转站、\(decoded.readyCount) 个已配置远端客户端", .success)
            }
        } catch {
            if showBanner { show("Tailnet 节点检测失败：\(error.localizedDescription)", .error) }
        }
    }

    private func synchronizeTopologyRoutes() {
        let recommended = Dictionary(uniqueKeysWithValues: tailnetNodes.compactMap { node -> (String, String)? in
            guard !node.isSelf,
                  !node.selected,
                  let route = node.feasibility?.recommendedRoute,
                  route != "unavailable" else { return nil }
            return (node.tailscaleIP, route)
        })
        // Discovery is the source of truth.  Do not retain a previously planned
        // bridge after a better direct route has been verified.
        topologyRoutes = recommended
    }

    func useRecommendedTopology() {
        synchronizeTopologyRoutes()
    }

    func planTopologyRoute(_ node: RelayCandidate, route: String) {
        guard route != "unavailable" else { return }
        topologyRoutes[node.tailscaleIP] = route
    }

    func refreshDevice(_ node: RelayCandidate) {
        probeDevice(node, deep: false)
    }

    func testDeviceConnectivity(_ node: RelayCandidate) {
        probeDevice(node, deep: true)
    }

    private func probeDevice(_ node: RelayCandidate, deep: Bool) {
        let id = node.tailscaleIP
        guard !refreshingDeviceIDs.contains(id), !testingDeviceIDs.contains(id) else { return }
        if deep { testingDeviceIDs.insert(id) } else { refreshingDeviceIDs.insert(id) }
        Task {
            defer {
                if deep { testingDeviceIDs.remove(id) } else { refreshingDeviceIDs.remove(id) }
            }
            do {
                let target = node.isSelf ? "local" : (node.sshTarget ?? "")
                guard !target.isEmpty else {
                    throw NSError(domain: "PCLCodexManager", code: 5, userInfo: [NSLocalizedDescriptionKey: "没有找到与该设备对应的 SSH 配置"])
                }
                var arguments = ["clients", "test", target, "--node", id]
                if !deep { arguments.append("--quick") }
                let result = try await runCLI(arguments)
                guard result.exitCode == 0 else { throw commandError(result) }
                let decoded = try BridgeDecode.value(DeviceConnectivityTest.self, from: result.stdout)
                deviceTests[id] = decoded
                let action = deep ? "连通性测试" : "状态刷新"
                show("\(node.nodeName) \(action)完成：\(decoded.summary)", decoded.status == "ready" ? .success : .info)
            } catch {
                show("\(node.nodeName) 检查失败：\(error.localizedDescription)", .error)
            }
        }
    }

    func selectRelay(_ relay: RelayCandidate) {
        guard relay.gateway, relay.pclAuth == "valid", !relay.selected, !isSelectingRelay else { return }
        isSelectingRelay = true
        Task {
            defer { isSelectingRelay = false }
            do {
                let result = try await runCLI(["relays", "select", relay.gatewayURL])
                guard result.exitCode == 0 else { throw commandError(result) }
                show("中转站已切换到 \(relay.nodeName)；重新加载 Codex 后生效", .success)
                refreshAll()
            } catch {
                show("切换中转站失败：\(error.localizedDescription)", .error)
            }
        }
    }

    func installRemoteClient(_ node: RelayCandidate) {
        guard let target = node.sshTarget, !target.isEmpty, installingClientTarget == nil else {
            show("该节点没有可用的 SSH 主机配置", .info)
            return
        }
        installingClientTarget = target
        Task {
            defer { installingClientTarget = nil }
            do {
                let result = try await runCLI(["clients", "install", target])
                guard result.exitCode == 0 else { throw commandError(result) }
                show("\(node.nodeName) 已接入当前中转站；请重新加载该服务器的 VS Code 窗口", .success)
                await discoverNodes(showBanner: false)
            } catch {
                show("远端接入失败：\(error.localizedDescription)", .error)
            }
        }
    }

    func updateRemoteClient(_ node: RelayCandidate) {
        guard let target = node.sshTarget, !target.isEmpty, installingClientTarget == nil else {
            show("该节点没有可用的 SSH 主机配置", .info)
            return
        }
        installingClientTarget = target
        Task {
            defer { installingClientTarget = nil }
            do {
                let result = try await performRemoteUpdate(node, target: target)
                commandLog = BridgeDecode.prettyJSON(result.stdout)
                show("\(node.nodeName) 已升级客户端、模型目录和原生角色；请重新加载该服务器的 VS Code 窗口", .success)
                await discoverNodes(showBanner: false)
            } catch {
                show("远端升级失败：\(error.localizedDescription)", .error)
            }
        }
    }

    func updateAllRemoteClients() {
        guard !isUpdatingAllClients, installingClientTarget == nil else { return }
        let candidates = remoteUpdateCandidates
        guard !candidates.isEmpty else {
            show("可管理的远端设备均已是当前版本", .info)
            return
        }
        isUpdatingAllClients = true
        Task {
            var succeeded: [String] = []
            var failed: [String] = []
            for node in candidates {
                guard let target = node.sshTarget, !target.isEmpty else {
                    failed.append("\(node.nodeName)：缺少 SSH 配置")
                    continue
                }
                installingClientTarget = target
                do {
                    let result = try await performRemoteUpdate(node, target: target)
                    commandLog = BridgeDecode.prettyJSON(result.stdout)
                    succeeded.append(node.nodeName)
                } catch {
                    failed.append("\(node.nodeName)：\(error.localizedDescription)")
                }
            }
            installingClientTarget = nil
            isUpdatingAllClients = false
            await discoverNodes(showBanner: false)
            if failed.isEmpty {
                show("已升级 \(succeeded.count) 台远端设备；请重新加载对应的 VS Code 窗口", .success)
            } else {
                commandLog = (["成功：\(succeeded.joined(separator: "、"))"] + failed).joined(separator: "\n")
                show("远端升级完成：\(succeeded.count) 台成功，\(failed.count) 台失败", .error)
            }
        }
    }

    private func performRemoteUpdate(_ node: RelayCandidate, target: String) async throws -> CommandResult {
        let result: CommandResult
        switch node.feasibility?.recommendedRoute {
        case "local_pcl_direct":
            result = try await runCLI(["direct", "install", target])
        case "bridge_via_local_mac":
            result = try await runCLI(["bridges", "install", target])
        default:
            result = try await runCLI(["clients", "update", target])
        }
        guard result.exitCode == 0 else { throw commandError(result) }
        return result
    }

    func configureNode(_ node: RelayCandidate, route: String? = nil) {
        guard let target = node.sshTarget, !target.isEmpty, installingClientTarget == nil else {
            show("该节点没有可用的 SSH 主机配置", .info)
            return
        }
        let selectedRoute = route ?? node.feasibility?.recommendedRoute ?? "unavailable"
        installingClientTarget = target
        Task {
            defer { installingClientTarget = nil }
            do {
                let result: CommandResult
                switch selectedRoute {
                case "direct":
                    result = try await runCLI(["clients", "install", target])
                case "local_pcl_direct":
                    result = try await runCLI(["direct", "install", target])
                case "bridge_via_local_mac":
                    result = try await runCLI(["bridges", "install", target])
                default:
                    throw NSError(domain: "PCLCodexManager", code: 4, userInfo: [NSLocalizedDescriptionKey: "当前没有经过可行性验证的接入路径"])
                }
                guard result.exitCode == 0 else { throw commandError(result) }
                topologyRoutes[node.tailscaleIP] = selectedRoute
                commandLog = BridgeDecode.prettyJSON(result.stdout)
                show("\(node.nodeName) 已按 \(routeName(selectedRoute)) 配置；请重新加载对应 VS Code 窗口", .success)
                await discoverNodes(showBanner: false)
            } catch {
                show("设备配置失败：\(error.localizedDescription)", .error)
            }
        }
    }

    func applyTopology(routes: [String: String]? = nil) {
        guard !isApplyingTopology else { return }
        let routes = routes ?? topologyRoutes
        isApplyingTopology = true
        Task {
            defer { isApplyingTopology = false }
            var reports: [String] = []
            do {
                for node in tailnetNodes where !node.isSelf {
                    guard let route = routes[node.tailscaleIP], route != "unavailable" else { continue }
                    guard let target = node.sshTarget, !target.isEmpty else {
                        reports.append("\(node.nodeName): 缺少 SSH 配置")
                        continue
                    }
                    if route == "direct", node.clientStatus?.ready == true {
                        reports.append("\(node.nodeName): 直连已验证")
                        continue
                    }
                    if route == "local_pcl_direct", node.feasibility?.localPCLDirectActive == true {
                        reports.append("\(node.nodeName): 本地直连已验证")
                        continue
                    }
                    let arguments: [String]
                    switch route {
                    case "direct": arguments = ["clients", "install", target]
                    case "local_pcl_direct": arguments = ["direct", "install", target]
                    case "bridge_via_local_mac": arguments = ["bridges", "install", target]
                    default: continue
                    }
                    let result = try await runCLI(arguments)
                    guard result.exitCode == 0 else { throw commandError(result) }
                    reports.append("\(node.nodeName): \(routeName(route))配置通过")
                }
                commandLog = reports.joined(separator: "\n")
                await discoverNodes(showBanner: false)
                show("拓扑已应用并完成连通性复检", .success)
            } catch {
                commandLog = reports.joined(separator: "\n")
                show("拓扑应用停止：\(error.localizedDescription)", .error)
            }
        }
    }

    private func routeName(_ route: String) -> String {
        switch route {
        case "direct": return "中转站直连"
        case "local_pcl_direct": return "PCL API 本地直连"
        case "bridge_via_local_mac": return "当前 Mac 桥接"
        default: return route
        }
    }

    func copyGatewayURL() {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(gatewayURL, forType: .string)
        show("中转站地址已复制", .success)
    }

    private func bootstrapClientIfNeeded() async throws {
        guard !didBootstrapClient else { return }
        guard let bundledCLIURL,
              FileManager.default.isExecutableFile(atPath: bundledCLIURL.path) else { return }
        let wasInstalled = FileManager.default.isExecutableFile(atPath: installedCLIURL.path)
        if !localClientNeedsBootstrap() {
            didBootstrapClient = true
            return
        }
        let result = try await runner.run(id: UUID(), executable: bundledCLIURL, arguments: ["install", "client"])
        guard result.exitCode == 0 else { throw commandError(result) }
        didBootstrapClient = true
        if let data = result.stdout.data(using: .utf8),
           let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           payload["router_port_changed"] as? Bool == true {
            codexReloadRequired = true
            let oldPort = payload["previous_router_port"] as? Int
            let newPort = ((payload["native_router_service"] as? [String: Any])?["port"] as? Int)
            show("本地路由端口已从 \(oldPort.map(String.init) ?? "旧端口") 切换到 \(newPort.map(String.init) ?? "新端口")；请退出并重新打开 Codex", .info)
        }
        if !wasInstalled { show("客户端已自动安装，官方 GPT 配置保持不变", .success) }
    }

    private func localClientNeedsBootstrap() -> Bool {
        guard FileManager.default.isExecutableFile(atPath: installedCLIURL.path) else { return true }
        let installedVersionURL = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".local/share/pcl-codex-bridge/VERSION")
        let installedVersion = (try? String(contentsOf: installedVersionURL, encoding: .utf8))?.trimmingCharacters(in: .whitespacesAndNewlines)
        let bundledVersion = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String
        guard installedVersion == bundledVersion else { return true }

        let configURL = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".codex/config.toml")
        guard let config = try? String(contentsOf: configURL, encoding: .utf8),
              config.contains("# >>> pcl-relay native router root >>>"),
              config.contains("# >>> pcl-codex-bridge managed block >>>"),
              let configPort = firstCapture(in: config, pattern: #"openai_base_url\s*=\s*\"http://127\.0\.0\.1:(\d+)/v1\""#) else {
            return true
        }

        let plistURL = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/LaunchAgents/cn.haichen.pcl-relay-router.plist")
        guard let data = try? Data(contentsOf: plistURL),
              let plist = try? PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any],
              let environment = plist["EnvironmentVariables"] as? [String: Any],
              let servicePort = environment["PCL_RELAY_NATIVE_PORT"] as? String else {
            return true
        }
        return configPort != servicePort
    }

    private func firstCapture(in text: String, pattern: String) -> String? {
        guard let expression = try? NSRegularExpression(pattern: pattern),
              let match = expression.firstMatch(in: text, range: NSRange(text.startIndex..., in: text)),
              match.numberOfRanges > 1,
              let range = Range(match.range(at: 1), in: text) else { return nil }
        return String(text[range])
    }

    private func runCLI(_ arguments: [String], id: UUID = UUID()) async throws -> CommandResult {
        guard let cliURL else {
            throw NSError(domain: "PCLCodexManager", code: 2, userInfo: [NSLocalizedDescriptionKey: "App 内未找到 pcl-codex 客户端，请重新安装 PCL Relay.app"])
        }
        return try await runner.run(id: id, executable: cliURL, arguments: arguments)
    }

    private func commandError(_ result: CommandResult) -> NSError {
        let detail = [result.stderr, result.stdout].first { !$0.isEmpty } ?? "命令失败"
        return NSError(domain: "PCLCodexManager", code: Int(result.exitCode), userInfo: [NSLocalizedDescriptionKey: detail])
    }

    private func show(_ text: String, _ kind: BannerMessage.Kind) {
        let message = BannerMessage(text: text, kind: kind)
        banner = message
        Task {
            try? await Task.sleep(for: .seconds(4))
            if banner?.id == message.id { banner = nil }
        }
    }
}
