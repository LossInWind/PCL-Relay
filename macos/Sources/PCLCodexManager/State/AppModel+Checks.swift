import BridgeCore
import Foundation

@MainActor
extension AppModel {
    /// Coalesce overlapping readers and await them; no detached refresh work.
    func check(_ key: String, operation: @escaping @MainActor () async throws -> Void) async {
        if let running = checkTasks[key] { await running.value; return }
        let generation = checks[key, default: CheckEvidence()].begin()
        let task = Task { @MainActor in
            do {
                try await operation()
                checks[key]?.finish(generation)
            } catch {
                checks[key]?.finish(generation, error: error.localizedDescription)
            }
        }
        checkTasks[key] = task
        await task.value
        checkTasks[key] = nil
    }

    func readConnection() async {
        await check("connection") {
            let result = try await self.runCLI(["doctor"])
            // doctor can return nonzero when one component is unhealthy; retain its evidence.
            self.doctor = try BridgeDecode.value(DoctorStatus.self, from: result.stdout)
            guard result.exitCode == 0 else { throw self.commandError(result) }
        }
    }

    func readIntegration() async {
        await check("integration") {
            let result = try await self.runCLI(["integration", "status"])
            guard result.exitCode == 0 else { throw self.commandError(result) }
            struct Status: Decodable { let enabled: Bool; let active: Bool }
            let status = try BridgeDecode.value(Status.self, from: result.stdout)
            self.integrationEnabled = status.enabled
            self.integrationActive = status.active
        }
    }

    func readModelRegistry() async {
        guard !isSavingAgents, !isDetecting, !isDiscovering else { return }
        let revision = agentSelection.revision
        await check("models") {
            let result = try await self.runCLI(["models", "show"])
            guard result.exitCode == 0 else { throw self.commandError(result) }
            let value = try BridgeDecode.value(ModelRegistry.self, from: result.stdout)
            guard revision == self.agentSelection.revision, !self.isSavingAgents,
                  !self.isDetecting, !self.isDiscovering else { return }
            self.registry = value
            self.agentSelection.adopt(Set(value.selectedAgents ?? AgentDefinition.all.map(\.id)), revision: revision)
        }
    }

    var buildIdentity: String {
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "开发版"
        let build = Bundle.main.object(forInfoDictionaryKey: "PCLBuildCommit") as? String ?? "未标记构建"
        return "PCL Relay \(version) · \(build)"
    }
}
