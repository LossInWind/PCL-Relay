import BridgeCore
import Foundation

@MainActor
extension AppModel {
    var updateOfferDevices: [RoutingDevice] { routingDevices.filter(\.canReceiveUpdateOffer) }
    var routingDevices: [RoutingDevice] {
        RoutingSnapshot.devices(localID: relaySync?.nodeID ?? "local", localName: relaySync?.nodeName ?? "当前 Mac",
                                localVersion: Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "开发版",
                                peers: (relaySync?.peers ?? []).map { peerChecks[$0.id] ?? $0 }, targets: (deploymentTargets?.targets ?? []).map { deviceChecks[$0.id] ?? $0 })
    }

    /// This path deliberately excludes bootstrap, sync-now, install and route selection.
    func checkRoutingDashboard() async {
        if let running = checkTasks["dashboard"] { await running.value; return }
        isCheckingRoutingDashboard = true
        defer { isCheckingRoutingDashboard = false }
        routingCheckError = nil
        await check("dashboard") { [self] in
            async let routes: Void = refreshRoutes(showBanner: false)
            async let sync: Void = refreshRelaySync(showBanner: false)
            async let devices: Void = refreshDeploymentTargets(showBanner: false)
            _ = await (routes, sync, devices)
            routingCheckedAt = Date()
            let errors = ["routes", "sync", "devices"].compactMap { checks[$0]?.error }
            routingCheckError = errors.isEmpty ? nil : errors.joined(separator: "；")
            if let routingCheckError {
                throw NSError(domain: "PCLRelay", code: 1, userInfo: [NSLocalizedDescriptionKey: routingCheckError])
            }
        }
    }

    func checkRoutingDevice(_ device: RoutingDevice) async {
        guard !checkingRoutingDevices.contains(device.id), !isCheckingRoutingDashboard,
              !isRefreshingDeploymentTargets, !isRefreshingSync else { return }
        checkingRoutingDevices.insert(device.id)
        defer { checkingRoutingDevices.remove(device.id) }
        if device.isLocal { await refreshRoutes(); return }
        let key = "device:\(device.id)"
        let generation = checks[key, default: CheckEvidence()].begin()
        guard let target = device.targets.first else {
            // A peer without SSH registration is checked only through its control endpoint.
            guard let peer = device.peers.first else {
                checks[key]?.finish(generation, error: "设备没有检查入口"); return
            }
            do {
                let result = try await runCLI(["sync", "status", "--probe", "--peer", peer.id])
                guard result.exitCode == 0 else { throw commandError(result) }
                let catalog = try BridgeDecode.value(RelaySyncCatalog.self, from: result.stdout)
                for value in catalog.peers { peerChecks[value.id] = value }
                checks[key]?.finish(generation)
            } catch { checks[key]?.finish(generation, error: error.localizedDescription) }
            return
        }
        do {
            let result = try await runCLI(["deploy", "status", "--probe", "--target", target.id])
            guard result.exitCode == 0 else { throw commandError(result) }
            let catalog = try BridgeDecode.value(DeploymentTargetCatalog.self, from: result.stdout)
            for value in catalog.targets { deviceChecks[value.id] = value }
            checks[key]?.finish(generation)
            show("设备检查完成；心跳在线不代表模型调用已验证", .info)
        } catch { checks[key]?.finish(generation, error: error.localizedDescription) }
    }
}
