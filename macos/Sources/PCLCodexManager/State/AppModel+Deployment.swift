import BridgeCore
import Foundation

@MainActor
extension AppModel {
    func refreshDeploymentTargets(showBanner: Bool = true) async {
        if let running = checkTasks["devices"] { await running.value; return }
        isRefreshingDeploymentTargets = true
        defer { isRefreshingDeploymentTargets = false }
        await check("devices") { [self] in
            let result = try await runCLI(["deploy", "status", "--probe"])
            guard result.exitCode == 0 else { throw commandError(result) }
            deploymentTargets = try BridgeDecode.value(DeploymentTargetCatalog.self, from: result.stdout)
            deviceChecks = [:]
        }
        if showBanner { show(checks["devices"]?.summary ?? "尚未检查", checks["devices"]?.phase == .succeeded ? .info : .error) }
    }

    func addDeploymentTarget(name: String, sshTarget: String, controlURL: String) async -> Bool {
        guard !isAddingDeploymentTarget else { return false }
        isAddingDeploymentTarget = true
        defer { isAddingDeploymentTarget = false }
        do {
            var arguments = [
                "deploy", "targets", "add",
                "--ssh-target", sshTarget,
                "--control-url", controlURL,
            ]
            if !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                arguments += ["--name", name]
            }
            let result = try await runCLI(arguments)
            commandLog = BridgeDecode.prettyJSON(result.stdout)
            guard result.exitCode == 0 else { throw commandError(result) }
            await refreshDeploymentTargets(showBanner: false)
            show("部署节点已登记；SSH 凭据只保留在本机配置中，不会同步", .success)
            return true
        } catch {
            show("登记部署节点失败：\(error.localizedDescription)", .error)
            return false
        }
    }

    func importDeploymentTargetsFromSSH() async {
        guard !isAddingDeploymentTarget, !isDeployingTopology else { return }
        isAddingDeploymentTarget = true
        defer { isAddingDeploymentTarget = false }
        do {
            let result = try await runCLI(["deploy", "targets", "import-ssh"])
            commandLog = BridgeDecode.prettyJSON(result.stdout)
            guard result.exitCode == 0 else { throw commandError(result) }
            guard let data = result.stdout.data(using: .utf8),
                  let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                throw NSError(domain: "PCLCodexManager", code: 9, userInfo: [NSLocalizedDescriptionKey: "SSH 导入返回了无效结果"])
            }
            let imported = payload["imported"] as? Int ?? 0
            let count = payload["count"] as? Int ?? 0
            await refreshDeploymentTargets(showBanner: false)
            show("已导入 \(imported) 个新设备，按 HostName 去重后共 \(count) 个；尚未连接远端", .success)
        } catch {
            show("导入 SSH 设备失败：\(error.localizedDescription)", .error)
        }
    }

    func removeDeploymentTarget(_ target: DeploymentTarget) async {
        do {
            let result = try await runCLI(["deploy", "targets", "remove", target.id])
            guard result.exitCode == 0 else { throw commandError(result) }
            await refreshDeploymentTargets(showBanner: false)
            show("已移除部署登记；远端软件未被卸载", .success)
        } catch {
            show("移除部署节点失败：\(error.localizedDescription)", .error)
        }
    }

    func deployLatestToRegisteredTargets() {
        guard !isDeployingTopology else { return }
        isDeployingTopology = true
        Task {
            defer { isDeployingTopology = false }
            do {
                let result = try await runCLI(["deploy", "all", "--timeout", "900"])
                commandLog = BridgeDecode.prettyJSON(result.stdout)
                guard result.exitCode == 0 else { throw commandError(result) }
                guard let data = result.stdout.data(using: .utf8),
                      let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                    throw NSError(domain: "PCLCodexManager", code: 8, userInfo: [NSLocalizedDescriptionKey: "部署返回了无效结果"])
                }
                let succeeded = payload["ok_count"] as? Int ?? 0
                let count = payload["count"] as? Int ?? 0
                await refreshDeploymentTargets(showBanner: false)
                await refreshRelaySync(showBanner: false)
                if count == 0 {
                    show("还没有登记部署节点；请先添加 SSH 别名和该节点的 15726 endpoint", .info)
                } else if succeeded == count {
                    let campaign = payload["update_campaign"] as? [String: Any]
                    if let error = campaign?["error"] as? String {
                        show("节点已接入，但更新未完成：\(error)", .error)
                    } else {
                        show("\(succeeded) 个登记节点已安装/接入；运行版本尚待逐台验证，不代表全网升级完成", .info)
                    }
                } else {
                    show("部署完成 \(succeeded)/\(count)；离线或失败节点请查看命令日志", .info)
                }
            } catch {
                show("拓扑部署失败：\(error.localizedDescription)", .error)
            }
        }
    }
}
