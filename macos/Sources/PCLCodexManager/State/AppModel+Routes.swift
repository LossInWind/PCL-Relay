import BridgeCore
import Foundation

@MainActor
extension AppModel {
    func refreshRoutes(showBanner: Bool = true) async {
        if let running = checkTasks["routes"] { await running.value; return }
        isRefreshingRoutes = true
        defer { isRefreshingRoutes = false }
        await check("routes") { [self] in
            let result = try await runCLI(["routes", "list", "--probe"])
            guard result.exitCode == 0 else { throw commandError(result) }
            let catalog = try BridgeDecode.value(GatewayRouteCatalog.self, from: result.stdout)
            gatewayRoutes = catalog
            let snapshot = try await runCLI(["routes", "snapshot"])
            if snapshot.exitCode == 0 { routingRuntime = try? BridgeDecode.value(RoutingRuntime.self, from: snapshot.stdout) }
            if !catalog.gateways.contains(where: { $0.id == selectedGatewayID }) {
                selectedGatewayID = catalog.gateways.first(where: \.selected)?.id
            }
            for route in catalog.gateways where route.healthy == true { routeLastSuccess[route.id] = Date() }
            await refreshOpenCodexProxyPolicy(showBanner: false)
        }
        if showBanner { show(checks["routes"]?.summary ?? "尚未检查", checks["routes"]?.phase == .succeeded ? .info : .error) }
    }

    func refreshOpenCodexProxyPolicy(showBanner: Bool = true) async {
        do {
            let result = try await runCLI(["routes", "proxy", "show"])
            guard result.exitCode == 0 else { throw commandError(result) }
            openCodexProxyPolicy = try BridgeDecode.value(OpenCodexProxyPolicy.self, from: result.stdout)
            if showBanner { show("OpenCodex 代理选项已刷新", .success) }
        } catch {
            if showBanner { show("读取 OpenCodex 代理选项失败：\(error.localizedDescription)", .error) }
        }
    }

    func saveOpenCodexProxyPolicy(proxy: String, noProxy: String) async -> Bool {
        guard !isSavingProxyPolicy else { return false }
        isSavingProxyPolicy = true
        defer { isSavingProxyPolicy = false }
        let bypass = noProxy
            .split(whereSeparator: { $0 == "," || $0 == "\n" })
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
        do {
            let result = try await runCLI(["routes", "proxy", "set", "--proxy", proxy, "--no-proxy"] + bypass)
            guard result.exitCode == 0 else { throw commandError(result) }
            openCodexProxyPolicy = try BridgeDecode.value(OpenCodexProxyPolicy.self, from: result.stdout)
            showProxyPolicyLifecycle(openCodexProxyPolicy!)
            return true
        } catch {
            show("保存 OpenCodex 代理选项失败：\(error.localizedDescription)", .error)
            return false
        }
    }

    func clearOpenCodexProxyPolicy() async {
        guard !isSavingProxyPolicy else { return }
        isSavingProxyPolicy = true
        defer { isSavingProxyPolicy = false }
        do {
            let result = try await runCLI(["routes", "proxy", "clear"])
            guard result.exitCode == 0 else { throw commandError(result) }
            openCodexProxyPolicy = try BridgeDecode.value(OpenCodexProxyPolicy.self, from: result.stdout)
            showProxyPolicyLifecycle(openCodexProxyPolicy!)
        } catch {
            show("清除 OpenCodex 代理选项失败：\(error.localizedDescription)", .error)
        }
    }

    func applyPendingOpenCodexProxyPolicy() async {
        guard !isSavingProxyPolicy else { return }
        isSavingProxyPolicy = true
        defer { isSavingProxyPolicy = false }
        do {
            let result = try await runCLI(["routes", "proxy", "apply"])
            guard result.exitCode == 0 else { throw commandError(result) }
            openCodexProxyPolicy = try BridgeDecode.value(OpenCodexProxyPolicy.self, from: result.stdout)
            showProxyPolicyLifecycle(openCodexProxyPolicy!)
        } catch {
            show("应用 OpenCodex 代理选项失败：\(error.localizedDescription)", .error)
        }
    }

    private func showProxyPolicyLifecycle(_ policy: OpenCodexProxyPolicy) {
        if policy.serviceRestarted == true {
            show("OpenCodex 已在空闲状态完成安全排空、重启和新进程健康验证", .success)
        } else if policy.restartRequired == true {
            let active = policy.activeTurnCount.map { "（当前 \($0) 个活动请求）" } ?? ""
            show("配置已保存，\(active)为保护会话暂不重启；请在空闲后应用", .info)
        } else {
            show("配置已保存；服务下次启动时由 OpenCodex 原生加载", .success)
        }
    }

    func addGatewayRoute(name: String, url: String) async -> Bool {
        guard !isAddingGateway else { return false }
        isAddingGateway = true
        defer { isAddingGateway = false }
        do {
            var arguments = ["routes", "add", url]
            if !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                arguments += ["--name", name]
            }
            let result = try await runCLI(arguments)
            guard result.exitCode == 0 else { throw commandError(result) }
            await refreshRoutes(showBanner: false)
            await refreshRelaySync(showBanner: false)
            show("中转站已加入逻辑拓扑；未修改任何网络设置", .success)
            return true
        } catch {
            show("添加中转站失败：\(error.localizedDescription)", .error)
            return false
        }
    }

    func selectGatewayRoute(_ route: GatewayRouteRecord) async {
        guard !isSwitchingGateway, !route.selected else { return }
        isSwitchingGateway = true
        defer { isSwitchingGateway = false }
        do {
            let result = try await runCLI(["routes", "select", route.id])
            guard result.exitCode == 0 else { throw commandError(result) }
            await refreshRoutes(showBanner: false)
            await refreshRelaySync(showBanner: false)
            show("pcl/* 已切换到 \(route.name)；官方模型路由未改变", .success)
        } catch {
            show("切换中转站失败：\(error.localizedDescription)", .error)
        }
    }

    func removeGatewayRoute(_ route: GatewayRouteRecord) async {
        guard !route.selected else { return }
        do {
            let result = try await runCLI(["routes", "remove", route.id])
            guard result.exitCode == 0 else { throw commandError(result) }
            await refreshRoutes(showBanner: false)
            await refreshRelaySync(showBanner: false)
            show("中转站已从逻辑拓扑移除", .success)
        } catch {
            show("移除中转站失败：\(error.localizedDescription)", .error)
        }
    }

    func refreshRelaySync(showBanner: Bool = true) async {
        if let running = checkTasks["sync"] { await running.value; return }
        isRefreshingSync = true
        defer { isRefreshingSync = false }
        await check("sync") { [self] in
            let result = try await runCLI(["sync", "status", "--probe"])
            guard result.exitCode == 0 else { throw commandError(result) }
            relaySync = try BridgeDecode.value(RelaySyncCatalog.self, from: result.stdout)
            peerChecks = [:]
            let serviceResult = try await runCLI(["sync", "service", "status"])
            if serviceResult.exitCode == 0 {
                relaySyncService = try BridgeDecode.value(RelaySyncServiceStatus.self, from: serviceResult.stdout)
            }
        }
        if showBanner { show(checks["sync"]?.summary ?? "尚未检查", checks["sync"]?.phase == .succeeded ? .info : .error) }
    }

    func setRelaySyncServiceEnabled(
        _ enabled: Bool,
        host: String = "0.0.0.0",
        port: Int = 15726,
        tokenFile: String = ""
    ) async -> Bool {
        guard !isTogglingSyncService else { return false }
        isTogglingSyncService = true
        defer { isTogglingSyncService = false }
        do {
            var arguments = enabled
                ? ["sync", "service", "install", "--host", host, "--port", String(port)]
                : ["sync", "service", "uninstall"]
            if enabled && !tokenFile.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                arguments += ["--token-file", tokenFile]
            }
            let result = try await runCLI(arguments)
            guard result.exitCode == 0 else { throw commandError(result) }
            await refreshRelaySync(showBanner: false)
            show(enabled ? "本机 Relay 心跳服务已启动" : "本机 Relay 心跳服务已停止", .success)
            return true
        } catch {
            show("切换 Relay 心跳服务失败：\(error.localizedDescription)", .error)
            return false
        }
    }

    func synchronizeRelayTopology() async {
        guard !isSynchronizing else { return }
        isSynchronizing = true
        defer { isSynchronizing = false }
        do {
            let result = try await runCLI(["sync", "now"])
            guard result.exitCode == 0 else { throw commandError(result) }
            commandLog = BridgeDecode.prettyJSON(result.stdout)
            await refreshRoutes(showBanner: false)
            await refreshRelaySync(showBanner: false)
            show("多端 Relay 拓扑同步完成", .success)
        } catch {
            show("Relay 拓扑同步失败：\(error.localizedDescription)", .error)
        }
    }

    func addRelaySyncPeer(name: String, url: String, tokenFile: String) async -> Bool {
        guard !isAddingSyncPeer else { return false }
        isAddingSyncPeer = true
        defer { isAddingSyncPeer = false }
        do {
            var arguments = ["sync", "peers", "add", url]
            if !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                arguments += ["--name", name]
            }
            if !tokenFile.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                arguments += ["--token-file", tokenFile]
            }
            let result = try await runCLI(arguments)
            guard result.exitCode == 0 else { throw commandError(result) }
            await refreshRelaySync(showBanner: false)
            show("Relay 同步节点已添加", .success)
            return true
        } catch {
            show("添加 Relay 同步节点失败：\(error.localizedDescription)", .error)
            return false
        }
    }

    func removeRelaySyncPeer(_ peer: RelaySyncPeer) async {
        do {
            let result = try await runCLI(["sync", "peers", "remove", peer.id])
            guard result.exitCode == 0 else { throw commandError(result) }
            await refreshRelaySync(showBanner: false)
            show("Relay 同步节点已移除", .success)
        } catch {
            show("移除 Relay 同步节点失败：\(error.localizedDescription)", .error)
        }
    }
}
