import Foundation
import ServiceManagement

struct LoginItemStatus {
    let enabled: Bool
    let message: String
}

struct LoginItemManager {
    private let fallbackLabel = "cn.haichen.pcl-relay-login"

    func configure(appURL: URL = Bundle.main.bundleURL) -> LoginItemStatus {
        let service = SMAppService.mainApp
        if service.status == .requiresApproval {
            return .init(enabled: false, message: "登录启动需要在系统设置中允许")
        }

        do {
            if service.status == .notRegistered {
                try service.register()
            }
            if service.status == .enabled {
                return enabledStatus
            }
            try ensureFallbackAgent(appURL: appURL)
            return enabledStatus
        } catch {
            if service.status == .requiresApproval {
                return .init(enabled: false, message: "登录启动需要在系统设置中允许")
            }
            do {
                try ensureFallbackAgent(appURL: appURL)
                return enabledStatus
            } catch {
                return .init(enabled: false, message: "登录启动配置失败：\(error.localizedDescription)")
            }
        }
    }

    private var enabledStatus: LoginItemStatus {
        .init(enabled: true, message: "登录 Mac 后自动显示菜单栏图标")
    }

    private func ensureFallbackAgent(appURL: URL) throws {
        let manager = FileManager.default
        let directory = manager.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/LaunchAgents", isDirectory: true)
        try manager.createDirectory(at: directory, withIntermediateDirectories: true)
        let plistURL = directory.appendingPathComponent("\(fallbackLabel).plist")
        let payload: [String: Any] = [
            "Label": fallbackLabel,
            "ProgramArguments": ["/usr/bin/open", "-g", appURL.path],
            "RunAtLoad": true,
            "KeepAlive": false,
            "ProcessType": "Interactive",
        ]
        let data = try PropertyListSerialization.data(fromPropertyList: payload, format: .xml, options: 0)
        try data.write(to: plistURL, options: .atomic)
        try manager.setAttributes([.posixPermissions: 0o644], ofItemAtPath: plistURL.path)

        let domain = "gui/\(getuid())"
        if launchctl(["print", "\(domain)/\(fallbackLabel)"]) != 0 {
            let status = launchctl(["bootstrap", domain, plistURL.path])
            guard status == 0 else {
                throw NSError(
                    domain: "PCLRelay.LoginItem",
                    code: Int(status),
                    userInfo: [NSLocalizedDescriptionKey: "无法注册用户登录项（launchctl \(status)）"]
                )
            }
        }
    }

    @discardableResult
    private func launchctl(_ arguments: [String]) -> Int32 {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        process.arguments = arguments
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        do {
            try process.run()
            process.waitUntilExit()
            return process.terminationStatus
        } catch {
            return -1
        }
    }
}
