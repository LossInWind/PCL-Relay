import BridgeCore
import Foundation

extension AppModel {
    func refreshTailnetNodes() {
        Task { await discoverNodes(showBanner: true) }
    }

    func discoverNodes(showBanner: Bool) async {
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

}
