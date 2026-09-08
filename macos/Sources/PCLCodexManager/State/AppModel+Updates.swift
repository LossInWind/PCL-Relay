import AppKit
import BridgeCore
import Foundation

extension AppModel {
    func refreshAppUpdate() {
        Task { await checkAppUpdate(showBanner: true) }
    }

    func checkAppUpdate(showBanner: Bool) async {
        if let running = checkTasks["updates"] { await running.value; return }
        guard !isInstallingAppUpdate else { return }
        isCheckingAppUpdate = true
        defer { isCheckingAppUpdate = false }
        await check("updates") { [self] in
            let result = try await runCLI(["updates", "status"])
            guard result.exitCode == 0 else { throw commandError(result) }
            let decoded = try BridgeDecode.value(ReleaseUpdateStatus.self, from: result.stdout)
            releaseUpdate = decoded
            guard decoded.available else {
                throw NSError(domain: "PCLRelay", code: 1, userInfo: [NSLocalizedDescriptionKey: decoded.error])
            }
            if showBanner {
                if decoded.updateAvailable {
                    show("发现 PCL Relay \(decoded.latestVersion)，可从 GitHub Release 升级", .info)
                } else if decoded.available {
                    show("本机 PCL Relay 已是最新版 \(decoded.currentVersion)", .success)
                } else {
                    show("暂时无法检查 GitHub Release：\(decoded.error)", .error)
                }
            }
        }
        if showBanner, let error = checks["updates"]?.error { show("检查发布版本失败：\(error)", .error) }
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

    func pushLatestUpdateToTopology() {
        guard !isPushingTopologyUpdate else { return }
        isPushingTopologyUpdate = true
        Task {
            defer { isPushingTopologyUpdate = false }
            do {
                let result = try await runCLI(["updates", "push"])
                commandLog = BridgeDecode.prettyJSON(result.stdout)
                guard result.exitCode == 0 else { throw commandError(result) }
                guard let data = result.stdout.data(using: .utf8),
                      let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                    throw NSError(domain: "PCLCodexManager", code: 7, userInfo: [NSLocalizedDescriptionKey: "升级推送返回了无效结果"])
                }
                let succeeded = payload["ok_count"] as? Int ?? 0
                let count = payload["count"] as? Int ?? 0
                if succeeded == count {
                    show("升级指令已持久化，\(succeeded) 个 Relay 节点已接受：优先 GitHub，失败时使用拓扑缓存", .success)
                } else {
                    show("升级指令已持久化：\(succeeded)/\(count) 个在线节点已接受，其余节点恢复心跳后补领", .info)
                }
                await refreshRelaySync(showBanner: false)
            } catch {
                show("推送拓扑更新失败：\(error.localizedDescription)", .error)
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

}
