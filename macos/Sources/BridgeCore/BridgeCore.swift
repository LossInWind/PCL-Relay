import Foundation

public struct DoctorStatus: Codable, Equatable, Sendable {
    public let gateway: Bool
    public let tailscale: Bool
    public let codex: Bool
    public let configManaged: Bool
    public let profile: Bool
    public let catalog: Bool
    public let registry: Bool
    public let unsandboxedFallback: Bool
    public let gatewayError: String?

    enum CodingKeys: String, CodingKey {
        case gateway, tailscale, codex, profile, catalog, registry
        case configManaged = "config_managed"
        case unsandboxedFallback = "unsandboxed_fallback"
        case gatewayError = "gateway_error"
    }
}

public struct RelayModelStatus: Codable, Equatable, Sendable {
    public let agent: String
    public let model: String
    public let advertised: Bool
    public let chat: Bool
    public let stream: Bool?
    public let toolCall: Bool
    public let toolCompatible: Bool?
    public let toolCallMode: String?
    public let executionReady: Bool
    public let error: String

    enum CodingKeys: String, CodingKey {
        case agent, model, advertised, chat, stream, error
        case toolCall = "tool_call"
        case toolCompatible = "tool_compatible"
        case toolCallMode = "tool_call_mode"
        case executionReady = "execution_ready"
    }
}

public struct DiscoveredModel: Codable, Equatable, Sendable, Identifiable {
    public let id: String
    public let alias: String
    public let family: String
    public let category: String
    public let description: String
    public let agentEligible: Bool
    public let recommended: Bool
    public let ownedBy: String
    public let inputModalities: [String]

    enum CodingKeys: String, CodingKey {
        case id, alias, family, category, description, recommended
        case agentEligible = "agent_eligible"
        case ownedBy = "owned_by"
        case inputModalities = "input_modalities"
    }
}

public struct AgentRecord: Codable, Equatable, Sendable {
    public let model: String
    public let description: String
}

public struct RelayServerStatus: Codable, Equatable, Sendable {
    public let status: String
    public let service: String
    public let nodeName: String
    public let magicDNS: String
    public let tailscaleIP: String
    public let port: Int
    public let pid: Int
    public let uptimeSeconds: Int
    public let upstream: String
    public let adminScope: [String]

    enum CodingKeys: String, CodingKey {
        case status, service, port, pid, upstream
        case nodeName = "node_name"
        case magicDNS = "magic_dns"
        case tailscaleIP = "tailscale_ip"
        case uptimeSeconds = "uptime_seconds"
        case adminScope = "admin_scope"
    }
}

public struct RelayServerLogs: Codable, Equatable, Sendable {
    public let service: String
    public let lines: [String]
}

public struct PortalStatus: Codable, Equatable, Sendable {
    public let available: Bool
    public let portalURL: String
    public let proxyURL: String
    public let pacURL: String
    public let latencyMS: Int
    public let httpStatus: Int?
    public let contentType: String?
    public let error: String
    public let opened: Bool?
    public let browser: String?
    public let target: String?
    public let profile: String?
    public let systemProxyChanged: Bool?

    enum CodingKeys: String, CodingKey {
        case available, error, opened, browser, target, profile
        case portalURL = "portal_url"
        case proxyURL = "proxy_url"
        case pacURL = "pac_url"
        case latencyMS = "latency_ms"
        case httpStatus = "http_status"
        case contentType = "content_type"
        case systemProxyChanged = "system_proxy_changed"
    }
}

public struct RemoteClientStatus: Codable, Equatable, Sendable {
    public let ssh: Bool
    public let sshTarget: String?
    public let ready: Bool?
    public let home: String?
    public let system: String?
    public let architecture: String?
    public let pythonVersion: String?
    public let supportedSystem: Bool?
    public let workspaceTailscale: Bool?
    public let workspaceTailscaleIP: String?
    public let pclNetworkReachable: Bool?
    public let relayCapable: Bool?
    public let configManaged: Bool?
    public let clientInstalled: Bool?
    public let gateway: String?
    public let gatewayReachable: Bool?
    public let gatewayLatencyMS: Int?
    public let gatewayModelCount: Int?
    public let gatewayModelsReachable: Bool?
    public let configuredGateway: String?
    public let configuredGatewayReachable: Bool?
    public let configuredGatewayLatencyMS: Int?
    public let configuredGatewayModelCount: Int?
    public let configuredGatewayModelsReachable: Bool?
    public let error: String?

    enum CodingKeys: String, CodingKey {
        case ssh, ready, home, system, architecture, gateway, error
        case sshTarget = "ssh_target"
        case pythonVersion = "python_version"
        case supportedSystem = "supported_system"
        case workspaceTailscale = "workspace_tailscale"
        case workspaceTailscaleIP = "workspace_tailscale_ip"
        case pclNetworkReachable = "pcl_network_reachable"
        case relayCapable = "relay_capable"
        case configManaged = "config_managed"
        case clientInstalled = "client_installed"
        case gatewayReachable = "gateway_reachable"
        case gatewayLatencyMS = "gateway_latency_ms"
        case gatewayModelCount = "gateway_model_count"
        case gatewayModelsReachable = "gateway_models_reachable"
        case configuredGateway = "configured_gateway"
        case configuredGatewayReachable = "configured_gateway_reachable"
        case configuredGatewayLatencyMS = "configured_gateway_latency_ms"
        case configuredGatewayModelCount = "configured_gateway_model_count"
        case configuredGatewayModelsReachable = "configured_gateway_models_reachable"
    }
}

public struct DeviceCheck: Codable, Equatable, Sendable, Identifiable {
    public var id: String { name }
    public let name: String
    public let passed: Bool
    public let detail: String
}

public struct DeviceConnectivityTest: Codable, Equatable, Sendable {
    public let target: String
    public let nodeID: String
    public let checkedAt: String
    public let status: String
    public let summary: String
    public let route: String
    public let tailnetOnline: Bool
    public let tailnetLastSeen: String
    public let ssh: Bool
    public let gatewayReachable: Bool
    public let catalogReachable: Bool?
    public let modelCount: Int
    public let latencyMS: Int?
    public let error: String
    public let checks: [DeviceCheck]

    enum CodingKeys: String, CodingKey {
        case target, status, summary, route, ssh, error, checks
        case nodeID = "node_id"
        case checkedAt = "checked_at"
        case tailnetOnline = "tailnet_online"
        case tailnetLastSeen = "tailnet_last_seen"
        case gatewayReachable = "gateway_reachable"
        case catalogReachable = "catalog_reachable"
        case modelCount = "model_count"
        case latencyMS = "latency_ms"
    }
}

public struct NodeFeasibility: Codable, Equatable, Sendable {
    public let relayCapable: Bool
    public let relayInstalled: Bool
    public let workspaceTailscale: Bool
    public let pclNetworkReachable: Bool
    public let direct: Bool
    public let bridgeViaLocalMac: Bool
    public let localPCLDirect: Bool
    public let localPCLDirectActive: Bool
    public let recommendedRoute: String
    public let recommendationReason: String
    public let stabilityScore: Int

    enum CodingKeys: String, CodingKey {
        case direct
        case relayCapable = "relay_capable"
        case relayInstalled = "relay_installed"
        case workspaceTailscale = "workspace_tailscale"
        case pclNetworkReachable = "pcl_network_reachable"
        case bridgeViaLocalMac = "bridge_via_local_mac"
        case localPCLDirect = "local_pcl_direct"
        case localPCLDirectActive = "local_pcl_direct_active"
        case recommendedRoute = "recommended_route"
        case recommendationReason = "recommendation_reason"
        case stabilityScore = "stability_score"
    }
}

public struct TopologyEdge: Codable, Equatable, Sendable, Identifiable {
    public var id: String { "\(from)-\(to)-\(type)" }
    public let from: String
    public let to: String
    public let type: String
    public let verified: Bool
    public let remotePort: Int?

    enum CodingKeys: String, CodingKey {
        case from, to, type, verified
        case remotePort = "remote_port"
    }
}

public struct TopologyRecommendation: Codable, Equatable, Sendable {
    public let relayID: String
    public let relayName: String
    public let reason: String
    public let edges: [TopologyEdge]

    enum CodingKeys: String, CodingKey {
        case reason, edges
        case relayID = "relay_id"
        case relayName = "relay_name"
    }
}

public struct RelayCandidate: Codable, Equatable, Sendable, Identifiable {
    public var id: String { tailscaleIP }
    public let nodeName: String
    public let magicDNS: String
    public let tailscaleIP: String
    public let online: Bool
    public let isSelf: Bool
    public let gatewayURL: String
    public let gateway: Bool
    public let pclAuth: String
    public let modelCount: Int
    public let latencyMS: Int?
    public let selected: Bool
    public let error: String
    public let service: String?
    public let version: String?
    public let sshTarget: String?
    public let clientStatus: RemoteClientStatus?
    public let feasibility: NodeFeasibility?

    enum CodingKeys: String, CodingKey {
        case online, gateway, selected, error, service, version, feasibility
        case isSelf = "self"
        case nodeName = "node_name"
        case magicDNS = "magic_dns"
        case tailscaleIP = "tailscale_ip"
        case gatewayURL = "gateway_url"
        case pclAuth = "pcl_auth"
        case modelCount = "model_count"
        case latencyMS = "latency_ms"
        case sshTarget = "ssh_target"
        case clientStatus = "client_status"
    }
}

public struct RelayDiscovery: Codable, Equatable, Sendable {
    public let tailnetConnected: Bool?
    public let selectedGateway: String
    public let remoteGateway: String?
    public let checkedAt: String
    public let readyCount: Int
    public let nodes: [RelayCandidate]
    public let recommendation: TopologyRecommendation?

    enum CodingKeys: String, CodingKey {
        case nodes, recommendation
        case tailnetConnected = "tailnet_connected"
        case selectedGateway = "selected_gateway"
        case remoteGateway = "remote_gateway"
        case checkedAt = "checked_at"
        case readyCount = "ready_count"
    }
}

public struct ModelRegistry: Codable, Equatable, Sendable {
    public let gateway: String?
    public let checkedAt: String?
    public let catalogCheckedAt: String?
    public let selectedAgents: [String]?
    public let agentDefinitions: [String: AgentRecord]?
    public let availableModels: [String: DiscoveredModel]?
    public let models: [String: RelayModelStatus]
    public let allChatReady: Bool?
    public let allStreamReady: Bool?
    public let allToolCompatible: Bool?

    enum CodingKeys: String, CodingKey {
        case gateway, models
        case checkedAt = "checked_at"
        case catalogCheckedAt = "catalog_checked_at"
        case selectedAgents = "selected_agents"
        case agentDefinitions = "agent_definitions"
        case availableModels = "available_models"
        case allChatReady = "all_chat_ready"
        case allStreamReady = "all_stream_ready"
        case allToolCompatible = "all_tool_compatible"
    }
}

public struct DelegateReport: Codable, Equatable, Sendable {
    public let agent: String?
    public let model: String?
    public let workspace: String?
    public let executionMode: String?
    public let effectiveSandbox: String?
    public let returncode: Int?
    public let timedOut: Bool?
    public let durationSeconds: Double?
    public let summary: String?
    public let gitRepository: Bool?
    public let gitStatusBefore: String?
    public let gitStatusAfter: String?
    public let gitDiff: String?
    public let modifiedFiles: [String]?
    public let stderrTail: String?

    enum CodingKeys: String, CodingKey {
        case agent, model, workspace, returncode, summary
        case executionMode = "execution_mode"
        case effectiveSandbox = "effective_sandbox"
        case timedOut = "timed_out"
        case durationSeconds = "duration_seconds"
        case gitRepository = "git_repository"
        case gitStatusBefore = "git_status_before"
        case gitStatusAfter = "git_status_after"
        case gitDiff = "git_diff"
        case modifiedFiles = "modified_files"
        case stderrTail = "stderr_tail"
    }
}

public struct CommandResult: Sendable {
    public let stdout: String
    public let stderr: String
    public let exitCode: Int32

    public init(stdout: String, stderr: String, exitCode: Int32) {
        self.stdout = stdout
        self.stderr = stderr
        self.exitCode = exitCode
    }
}

public enum BridgeDecode {
    public static func value<T: Decodable>(_ type: T.Type, from text: String) throws -> T {
        guard let data = text.data(using: .utf8) else {
            throw CocoaError(.fileReadInapplicableStringEncoding)
        }
        return try JSONDecoder().decode(type, from: data)
    }

    public static func prettyJSON(_ text: String) -> String {
        guard let data = text.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data),
              let pretty = try? JSONSerialization.data(withJSONObject: object, options: [.prettyPrinted, .sortedKeys]),
              let result = String(data: pretty, encoding: .utf8) else {
            return text
        }
        return result
    }
}

public final class CommandRunner: @unchecked Sendable {
    private let lock = NSLock()
    private var processes: [UUID: Process] = [:]

    public init() {}

    public func run(
        id: UUID,
        executable: URL,
        arguments: [String],
        environment: [String: String]? = nil
    ) async throws -> CommandResult {
        try await withCheckedThrowingContinuation { continuation in
            DispatchQueue.global(qos: .userInitiated).async {
                let process = Process()
                let stdoutPipe = Pipe()
                let stderrPipe = Pipe()
                process.executableURL = executable
                process.arguments = arguments
                process.standardOutput = stdoutPipe
                process.standardError = stderrPipe
                if let environment {
                    process.environment = ProcessInfo.processInfo.environment.merging(environment) { _, new in new }
                }

                self.lock.lock()
                self.processes[id] = process
                self.lock.unlock()

                do {
                    try process.run()
                    let group = DispatchGroup()
                    var stdout = Data()
                    var stderr = Data()
                    group.enter()
                    DispatchQueue.global(qos: .utility).async {
                        stdout = stdoutPipe.fileHandleForReading.readDataToEndOfFile()
                        group.leave()
                    }
                    group.enter()
                    DispatchQueue.global(qos: .utility).async {
                        stderr = stderrPipe.fileHandleForReading.readDataToEndOfFile()
                        group.leave()
                    }
                    process.waitUntilExit()
                    group.wait()
                    self.lock.lock()
                    self.processes.removeValue(forKey: id)
                    self.lock.unlock()
                    continuation.resume(returning: CommandResult(
                        stdout: String(data: stdout, encoding: .utf8) ?? "",
                        stderr: String(data: stderr, encoding: .utf8) ?? "",
                        exitCode: process.terminationStatus
                    ))
                } catch {
                    self.lock.lock()
                    self.processes.removeValue(forKey: id)
                    self.lock.unlock()
                    continuation.resume(throwing: error)
                }
            }
        }
    }

    public func cancel(_ id: UUID) {
        lock.lock()
        let process = processes[id]
        lock.unlock()
        if process?.isRunning == true {
            process?.terminate()
        }
    }
}
