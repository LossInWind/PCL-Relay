import AppKit
import BridgeCore
import Foundation

extension AppModel {
    func refreshAppUpdate() {
        Task { await checkAppUpdate(showBanner: true) }
    }

    func checkAppUpdate(showBanner: Bool) async {
        guard !isCheckingAppUpdate, !isInstallingAppUpdate else { return }
        isCheckingAppUpdate = true
        defer { isCheckingAppUpdate = false }
        do {
            let result = try await runCLI(["updates", "status"])
            guard result.exitCode == 0 else { throw commandError(result) }
            let decoded = try BridgeDecode.value(ReleaseUpdateStatus.self, from: result.stdout)
            releaseUpdate = decoded
            if showBanner {
                if decoded.updateAvailable {
                    show("发现 PCL Relay \(decoded.latestVersion)，可从 GitHub Release 升级", .info)
                } else if decoded.available {
                    show("本机 PCL Relay 已是最新版 \(decoded.currentVersion)", .success)
                } else {
                    show("暂时无法检查 GitHub Release：\(decoded.error)", .error)
                }
            }
        } catch {
            if showBanner { show("检查本机更新失败：\(error.localizedDescription)", .error) }
        }
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

    func restartApplication() {
        guard appRestartRequired else { return }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/sh")
        process.arguments = ["-c", "sleep 1; /usr/bin/open -a 'PCL Relay'"]
        try? process.run()
        NSApp.terminate(nil)
    }

}
