import BridgeCore
import Foundation

extension AppModel {
    func setIntegrationEnabled(_ enabled: Bool) {
        guard !isTogglingIntegration, enabled != integrationEnabled else { return }
        isTogglingIntegration = true
        Task {
            defer { isTogglingIntegration = false }
            do {
                let result = try await runCLI(["integration", enabled ? "enable" : "disable"])
                guard result.exitCode == 0 else { throw commandError(result) }
                integrationEnabled = enabled
                codexReloadRequired = true
                show(
                    enabled
                        ? "PCL 子 Agent 已启用；重新打开 Codex 后生效"
                        : "PCL 子 Agent 已关闭，Codex 已恢复官方设置；重新打开 Codex 后生效",
                    .success
                )
                refreshAll()
            } catch {
                show("切换 Codex 集成失败：\(error.localizedDescription)", .error)
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

    func refreshPortalStatus(showBanner: Bool) async {
        if let running = checkTasks["portal"] { await running.value; return }
        isCheckingPortal = true
        defer { isCheckingPortal = false }
        await check("portal") { [self] in
            let result = try await runCLI(["portal", "status"])
            guard result.exitCode == 0 else { throw commandError(result) }
            let decoded = try BridgeDecode.value(PortalStatus.self, from: result.stdout)
            portalStatus = decoded
            if !decoded.available {
                throw NSError(domain: "PCLRelay", code: 1, userInfo: [NSLocalizedDescriptionKey: decoded.error])
            }
            if showBanner {
                show(
                    decoded.available
                        ? "PCL 内网页面可用，延迟 \(decoded.latencyMS) ms"
                        : "PCL 内网页面暂不可用：\(decoded.error)",
                    decoded.available ? .success : .error
                )
            }
        }
        if showBanner, let error = checks["portal"]?.error { show("门户检测失败：\(error)", .error) }
    }

    func openPortal(path: String) {
        guard !isOpeningPortal else { return }
        isOpeningPortal = true
        lastPortalPath = path
        portalOpenFailed = false
        portalOpenMessage = "正在打开专用浏览器…"
        Task {
            defer { isOpeningPortal = false }
            do {
                let result = try await runCLI(["portal", "open", "--path", path])
                guard result.exitCode == 0 else { throw commandError(result) }
                let decoded = try BridgeDecode.value(PortalStatus.self, from: result.stdout)
                portalOpenMessage = "已交给 \(decoded.browser ?? "浏览器") 打开；页面登录状态请在浏览器中确认"
                show("已通过 \(decoded.browser ?? "浏览器") 打开 PCL 内网页面", .success)
            } catch {
                portalOpenFailed = true
                portalOpenMessage = "打开失败：\(error.localizedDescription)"
                show("打开失败：\(error.localizedDescription)", .error)
            }
        }
    }

}
