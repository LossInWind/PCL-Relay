import AppKit
import BridgeCore
import Foundation
import SwiftUI

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

    let runner = CommandRunner()
    private let loginItemManager = LoginItemManager()
    var detectionJob: UUID?
    private var consensusMonitor: Task<Void, Never>?
    private var didStart = false
    private var didBootstrapClient = false
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
        let loginItem = loginItemManager.configure()
        launchAtLoginEnabled = loginItem.enabled
        launchAtLoginStatusText = loginItem.message
        refreshAll()
        startConsensusMonitoring()
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

    func runCLI(_ arguments: [String], id: UUID = UUID()) async throws -> CommandResult {
        guard let cliURL else {
            throw NSError(domain: "PCLCodexManager", code: 2, userInfo: [NSLocalizedDescriptionKey: "App 内未找到 pcl-codex 客户端，请重新安装 PCL Relay.app"])
        }
        return try await runner.run(id: id, executable: cliURL, arguments: arguments)
    }

    func commandError(_ result: CommandResult) -> NSError {
        let detail = [result.stderr, result.stdout].first { !$0.isEmpty } ?? "命令失败"
        return NSError(domain: "PCLCodexManager", code: Int(result.exitCode), userInfo: [NSLocalizedDescriptionKey: detail])
    }

    func show(_ text: String, _ kind: BannerMessage.Kind) {
        let message = BannerMessage(text: text, kind: kind)
        banner = message
        Task {
            try? await Task.sleep(for: .seconds(4))
            if banner?.id == message.id { banner = nil }
        }
    }
}
