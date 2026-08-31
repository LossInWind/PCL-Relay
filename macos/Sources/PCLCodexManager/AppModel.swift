import AppKit
import BridgeCore
import Foundation
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
    @Published var delegateReport: DelegateReport?
    @Published var isRefreshing = false
    @Published var isDetecting = false
    @Published var isDiscovering = false
    @Published var isSavingAgents = false
    @Published var isRunningAgent = false
    @Published var isRestartingGateway = false
    @Published var isCheckingPortal = false
    @Published var isOpeningPortal = false
    @Published var isDiscoveringNodes = false
    @Published var isSelectingRelay = false
    @Published var installingClientTarget: String?
    @Published var isApplyingTopology = false
    @Published var topologyRoutes: [String: String] = [:]
    @Published var deviceTests: [String: DeviceConnectivityTest] = [:]
    @Published var refreshingDeviceIDs = Set<String>()
    @Published var testingDeviceIDs = Set<String>()
    @Published var banner: BannerMessage?

    private let runner = CommandRunner()
    private var detectionJob: UUID?
    private var delegateJob: UUID?
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

    var codexIntegrationReady: Bool {
        doctor?.codex == true && doctor?.configManaged == true && doctor?.profile == true && doctor?.catalog == true
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
        if FileManager.default.isExecutableFile(atPath: installedCLIURL.path) { return installedCLIURL }
        if let bundledCLIURL, FileManager.default.isExecutableFile(atPath: bundledCLIURL.path) { return bundledCLIURL }
        return nil
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
            } catch {
                show("刷新失败：\(error.localizedDescription)", .error)
            }
        }
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
                show("子 Agent 已更新；重新加载 Codex 后生效，官方 GPT 保持不变", .success)
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
                show("Codex 子 Agent 已安装/修复，请重新加载 Codex", .success)
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
            let relayChanged = relayDiscovery?.selectedGateway != decoded.selectedGateway
            relayDiscovery = decoded
            synchronizeTopologyRoutes(reset: relayChanged || topologyRoutes.isEmpty)
            if showBanner {
                let relays = decoded.nodes.filter { $0.gateway && $0.pclAuth == "valid" }.count
                show("发现 \(relays) 个可用中转站、\(decoded.readyCount) 个已配置远端客户端", .success)
            }
        } catch {
            if showBanner { show("Tailnet 节点检测失败：\(error.localizedDescription)", .error) }
        }
    }

    private func synchronizeTopologyRoutes(reset: Bool) {
        let recommended = Dictionary(uniqueKeysWithValues: tailnetNodes.compactMap { node -> (String, String)? in
            guard !node.isSelf,
                  !node.selected,
                  let route = node.feasibility?.recommendedRoute,
                  route != "unavailable" else { return nil }
            return (node.tailscaleIP, route)
        })
        if reset {
            topologyRoutes = recommended
            return
        }
        var merged = topologyRoutes.filter { id, _ in tailnetNodes.contains(where: { $0.tailscaleIP == id }) }
        for (id, route) in recommended where merged[id] == nil { merged[id] = route }
        topologyRoutes = merged
    }

    func useRecommendedTopology() {
        synchronizeTopologyRoutes(reset: true)
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

    func runAgent(agent: String, task: String, workspace: String, readOnly: Bool, timeout: Int) {
        guard !isRunningAgent else { return }
        guard !task.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            show("请先填写任务", .info)
            return
        }
        let job = UUID()
        delegateJob = job
        isRunningAgent = true
        delegateReport = nil
        commandLog = "正在启动 \(agent)……"
        Task {
            defer {
                isRunningAgent = false
                delegateJob = nil
            }
            do {
                let args = [
                    "delegate", agent, task,
                    "--workspace", workspace,
                    "--timeout", String(timeout),
                    "--execution-mode", readOnly ? "read-only" : "workspace-write",
                ]
                let result = try await runCLI(args, id: job)
                commandLog = BridgeDecode.prettyJSON(result.stdout) + (result.stderr.isEmpty ? "" : "\n" + result.stderr)
                guard result.exitCode == 0 else { throw commandError(result) }
                delegateReport = try BridgeDecode.value(DelegateReport.self, from: result.stdout)
                show("子 Agent 任务已完成", .success)
            } catch {
                show("子 Agent 已停止：\(error.localizedDescription)", .error)
            }
        }
    }

    func cancelAgent() {
        guard let delegateJob else { return }
        runner.cancel(delegateJob)
        commandLog += "\n正在停止子 Agent……"
    }

    func copyGatewayURL() {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(gatewayURL, forType: .string)
        show("中转站地址已复制", .success)
    }

    private func bootstrapClientIfNeeded() async throws {
        guard let bundledCLIURL,
              FileManager.default.isExecutableFile(atPath: bundledCLIURL.path) else { return }
        let wasInstalled = FileManager.default.isExecutableFile(atPath: installedCLIURL.path)
        let result = try await runner.run(id: UUID(), executable: bundledCLIURL, arguments: ["install", "client"])
        guard result.exitCode == 0 else { throw commandError(result) }
        if !wasInstalled { show("客户端已自动安装，官方 GPT 配置保持不变", .success) }
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
