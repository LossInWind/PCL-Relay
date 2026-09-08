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
    @Published var agentSelection = AgentSelectionState(Set(AgentDefinition.all.map(\.id)))
    var selectedAgents: Set<String> {
        get { agentSelection.displayed }
        set { agentSelection.adopt(newValue, revision: agentSelection.revision) }
    }
    @Published var agentSaveMessage = "尚未修改"
    @Published var isInstallingIntegration = false
    @Published var showDetectionConfirmation = false
    @Published var checks: [String: CheckEvidence] = [:]
    @Published var portalOpenMessage = ""
    @Published var portalOpenFailed = false
    var lastPortalPath = "/"
    var checkTasks: [String: Task<Void, Never>] = [:]
    // Injected only in isolated tests; production always uses the bundled CLI.
    var commandOverride: (([String]) async throws -> CommandResult)?
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
    @Published var releaseUpdate: ReleaseUpdateStatus?
    @Published var isCheckingAppUpdate = false
    @Published var isInstallingAppUpdate = false
    @Published var isPushingTopologyUpdate = false
    @Published var appRestartRequired = false
    @Published var banner: BannerMessage?
    @Published var launchAtLoginEnabled = false
    @Published var launchAtLoginStatusText = "正在配置登录启动"
    @Published var codexReloadRequired = false
    @Published var integrationEnabled = true
    @Published var integrationActive = false
    @Published var isTogglingIntegration = false
    @Published var gatewayRoutes: GatewayRouteCatalog?
    @Published var openCodexProxyPolicy: OpenCodexProxyPolicy?
    @Published var relaySync: RelaySyncCatalog?
    @Published var relaySyncService: RelaySyncServiceStatus?
    @Published var deploymentTargets: DeploymentTargetCatalog?
    @Published var selectedGatewayID: String?
    @Published var isRefreshingRoutes = false
    @Published var isAddingGateway = false
    @Published var isSwitchingGateway = false
    @Published var isSavingProxyPolicy = false
    @Published var isRefreshingSync = false
    @Published var isSynchronizing = false
    @Published var isAddingSyncPeer = false
    @Published var isTogglingSyncService = false
    @Published var isRefreshingDeploymentTargets = false
    @Published var isAddingDeploymentTarget = false
    @Published var isDeployingTopology = false
    @Published var isCheckingRoutingDashboard = false
    @Published var routingCheckedAt: Date?
    @Published var routingCheckError: String?
    @Published var routeLastSuccess: [String: Date] = [:]
    @Published var selectedRoutingDeviceID: String?
    @Published var deviceChecks: [String: DeploymentTarget] = [:]
    @Published var peerChecks: [String: RelaySyncPeer] = [:]
    @Published var isPreparingLocalComponents = false
    @Published var routingRuntime: RoutingRuntime?
    @Published var checkingRoutingDevices = Set<String>()

    let runner = CommandRunner()
    private let loginItemManager = LoginItemManager()
    var detectionJob: UUID?
    private var didStart = false
    private var didBootstrapClient = false

    struct BannerMessage: Identifiable, Equatable {
        enum Kind { case success, error, info }
        let id = UUID()
        let text: String
        let kind: Kind
    }

    var gatewayURL: String {
        registry?.gateway ?? "http://haichen-pcl-linux-3070ti.tail132f30.ts.net:15722/v1"
    }

    var codexIntegrationReady: Bool {
        doctor?.codex == true && integrationActive
    }

    var routeReady: Bool {
        checks["connection"]?.phase == .succeeded && checks["integration"]?.phase == .succeeded
            && doctor?.gateway == true && integrationActive
    }

    var routeStatusTitle: String {
        if checks["connection"]?.phase == .failed { return "连接检查失败" }
        if checks["connection"]?.phase != .succeeded { return "连接待确认" }
        if doctor?.gateway != true { return "PCL 接入点不可达" }
        if checks["integration"]?.phase != .succeeded { return "Codex 接入待确认" }
        if !integrationActive { return "Codex 集成待启用" }
        return "PCL 接入点可达"
    }

    var gatewayDisplayName: String {
        guard let host = URL(string: gatewayURL)?.host else { return gatewayURL }
        return host
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
        SettingsWindowPresenter.shared.model = self
        let loginItem = loginItemManager.status()
        launchAtLoginEnabled = loginItem.enabled
        launchAtLoginStatusText = loginItem.message
        refreshAll()
    }

    func enableLoginItem() {
        let result = loginItemManager.configure()
        launchAtLoginEnabled = result.enabled
        launchAtLoginStatusText = result.message
    }

    func refreshAll() {
        guard !isRefreshing else { return }
        isRefreshing = true
        Task {
            defer { isRefreshing = false }
            async let connection: Void = readConnection()
            async let integration: Void = readIntegration()
            async let models: Void = readModelRegistry()
            async let routing: Void = checkRoutingDashboard()
            async let portal: Void = refreshPortalStatus(showBanner: false)
            async let updates: Void = checkAppUpdate(showBanner: false)
            _ = await (connection, integration, models, routing, portal, updates)
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
        if !localControlPlaneNeedsBootstrap() {
            didBootstrapClient = true
            return
        }
        let result = try await runner.run(
            id: UUID(),
            executable: bundledCLIURL,
            arguments: ["sidecar", "stage"]
        )
        guard result.exitCode == 0 else { throw commandError(result) }
        didBootstrapClient = true
        if !wasInstalled {
            show("控制面与 OpenCodex 已暂存；当前 Codex 会话和数据面未重启", .success)
        }
    }

    func prepareLocalComponents() async {
        guard !isPreparingLocalComponents else { return }
        isPreparingLocalComponents = true
        defer { isPreparingLocalComponents = false }
        do {
            try await bootstrapClientIfNeeded()
            show("本机组件已准备；模型服务未重启", .success)
        } catch { show("准备本机组件失败：\(error.localizedDescription)", .error) }
    }

    private func localControlPlaneNeedsBootstrap() -> Bool {
        guard FileManager.default.isExecutableFile(atPath: installedCLIURL.path) else { return true }
        let installedVersionURL = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".local/share/pcl-codex-bridge/VERSION")
        let installedVersion = (try? String(contentsOf: installedVersionURL, encoding: .utf8))?.trimmingCharacters(in: .whitespacesAndNewlines)
        let bundledVersion = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String
        guard installedVersion == bundledVersion else { return true }

        guard let bundledManifestURL = Bundle.main.resourceURL?
                .appendingPathComponent("bridge/opencodex/UPSTREAM.json") else { return true }
        let installedManifestURL = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".local/share/pcl-codex-bridge/opencodex/current/UPSTREAM.json")
        guard let bundledManifest = try? Data(contentsOf: bundledManifestURL),
              let installedManifest = try? Data(contentsOf: installedManifestURL) else { return true }
        return bundledManifest != installedManifest
    }

    func runCLI(_ arguments: [String], id: UUID = UUID()) async throws -> CommandResult {
        if let commandOverride { return try await commandOverride(arguments) }
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
