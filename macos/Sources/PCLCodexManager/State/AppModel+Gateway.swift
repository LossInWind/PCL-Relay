import BridgeCore
import Foundation

extension AppModel {
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
        guard !isCheckingPortal else { return }
        isCheckingPortal = true
        defer { isCheckingPortal = false }
        do {
            let result = try await runCLI(["portal", "status"])
            guard result.exitCode == 0 else { throw commandError(result) }
            let decoded = try BridgeDecode.value(PortalStatus.self, from: result.stdout)
            portalStatus = decoded
            if showBanner {
                show(
                    decoded.available
                        ? "PCL 内网页面可用，延迟 \(decoded.latencyMS) ms"
                        : "PCL 内网页面暂不可用：\(decoded.error)",
                    decoded.available ? .success : .error
                )
            }
        } catch {
            if showBanner { show("门户检测失败：\(error.localizedDescription)", .error) }
        }
    }

    func openPortal(path: String) {
        guard !isOpeningPortal else { return }
        isOpeningPortal = true
        Task {
            defer { isOpeningPortal = false }
            do {
                let result = try await runCLI(["portal", "open", "--path", path])
                guard result.exitCode == 0 else { throw commandError(result) }
                let decoded = try BridgeDecode.value(PortalStatus.self, from: result.stdout)
                portalStatus = decoded
                show("已通过 \(decoded.browser ?? "浏览器") 打开 PCL 内网页面", .success)
            } catch {
                show("打开失败：\(error.localizedDescription)", .error)
            }
        }
    }

}
