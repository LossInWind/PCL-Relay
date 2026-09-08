import BridgeCore
import Foundation

@MainActor
extension AppModel {
    var routingDevices: [RoutingDevice] {
        RoutingSnapshot.devices(localID: relaySync?.nodeID ?? "local", localName: relaySync?.nodeName ?? "当前 Mac",
                                localVersion: Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "开发版",
                                peers: (relaySync?.peers ?? []).map { peerChecks[$0.id] ?? $0 }, targets: (deploymentTargets?.targets ?? []).map { deviceChecks[$0.id] ?? $0 })
    }

    /// This path deliberately excludes bootstrap, sync-now, install and route selection.
    func checkRoutingDashboard() async {
        guard !isCheckingRoutingDashboard else { return }
        isCheckingRoutingDashboard = true
        defer { isCheckingRoutingDashboard = false }
        routingCheckError = nil
        await refreshRoutes(showBanner: false)
        await refreshRelaySync(showBanner: false)
        await refreshDeploymentTargets(showBanner: false)
        routingCheckedAt = Date()
    }

    func checkRoutingDevice(_ device: RoutingDevice) async {
        guard !checkingRoutingDevices.contains(device.id) else { return }
        checkingRoutingDevices.insert(device.id)
        defer { checkingRoutingDevices.remove(device.id) }
        if device.isLocal { await refreshRoutes(); return }
        guard let target = device.targets.first else {
            // A peer without SSH registration is checked only through its control endpoint.
            guard let peer = device.peers.first else { return }
            do {
                let result = try await runCLI(["sync", "status", "--probe", "--peer", peer.id])
                guard result.exitCode == 0 else { throw commandError(result) }
                let catalog = try BridgeDecode.value(RelaySyncCatalog.self, from: result.stdout)
                for value in catalog.peers { peerChecks[value.id] = value }
            } catch { show("设备心跳检查失败：\(error.localizedDescription)", .error) }
            return
        }
        do {
            let result = try await runCLI(["deploy", "status", "--probe", "--target", target.id])
            guard result.exitCode == 0 else { throw commandError(result) }
            let catalog = try BridgeDecode.value(DeploymentTargetCatalog.self, from: result.stdout)
            for value in catalog.targets { deviceChecks[value.id] = value }
            show("设备检查完成；心跳在线不代表模型调用已验证", .info)
        } catch { show("设备检查失败：\(error.localizedDescription)", .error) }
    }
}
