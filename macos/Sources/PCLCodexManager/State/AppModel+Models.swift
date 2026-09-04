import BridgeCore
import Foundation

extension AppModel {
    func discoverModels() {
        guard !isDiscovering else { return }
        isDiscovering = true
        commandLog = "正在从中转站读取最新模型目录……"
        Task {
            defer { isDiscovering = false }
            do {
                let result = try await runCLI(["models", "discover"])
                commandLog = BridgeDecode.prettyJSON(result.stdout)
                guard result.exitCode == 0 else { throw commandError(result) }
                let decoded = try BridgeDecode.value(ModelRegistry.self, from: result.stdout)
                registry = decoded
                selectedAgents = Set(decoded.selectedAgents ?? AgentDefinition.all.map(\.id))
                show("模型目录已更新：发现 \(decoded.availableModels?.count ?? 0) 个模型", .success)
            } catch {
                show("检查更新失败：\(error.localizedDescription)", .error)
            }
        }
    }

    func refreshRemoteStatus() async {
        do {
            let result = try await runCLI(["server", "status"])
            guard result.exitCode == 0 else { throw commandError(result) }
            let decoded = try BridgeDecode.value(RelayServerStatus.self, from: result.stdout)
            serverStatus = decoded
            remoteServiceActive = decoded.status == "active"
            remoteStatusText = "PID \(decoded.pid) · uptime \(decoded.uptimeSeconds)s · \(decoded.tailscaleIP):\(decoded.port) · scope: \(decoded.adminScope.joined(separator: ", "))"
        } catch {
            remoteServiceActive = false
            remoteStatusText = error.localizedDescription
        }
    }

    func detectModels() {
        guard !isDetecting else { return }
        let job = UUID()
        detectionJob = job
        isDetecting = true
        commandLog = "正在依次检测普通响应、SSE 流式输出和工具调用……"
        Task {
            defer {
                isDetecting = false
                detectionJob = nil
            }
            do {
                let result = try await runCLI(["models", "detect"], id: job)
                commandLog = BridgeDecode.prettyJSON(result.stdout) + (result.stderr.isEmpty ? "" : "\n" + result.stderr)
                guard result.exitCode == 0 else { throw commandError(result) }
                let decoded = try BridgeDecode.value(ModelRegistry.self, from: result.stdout)
                registry = decoded
                selectedAgents = Set(decoded.selectedAgents ?? AgentDefinition.all.map(\.id))
                show("已选子 Agent 能力检测完成", .success)
            } catch {
                show("模型检测停止：\(error.localizedDescription)", .error)
            }
        }
    }

    func cancelDetection() {
        guard let detectionJob else { return }
        runner.cancel(detectionJob)
        commandLog += "\n正在停止检测……"
    }

    func setAgent(_ id: String, enabled: Bool) {
        if enabled {
            selectedAgents.insert(id)
        } else if selectedAgents.count > 1 {
            selectedAgents.remove(id)
        } else {
            show("至少保留一个子 Agent", .info)
            return
        }
        saveSelectedAgents()
    }

    func saveSelectedAgents() {
        guard !isSavingAgents else { return }
        isSavingAgents = true
        let ordered = agentOptions.filter { selectedAgents.contains($0.id) }.map { option in
            registry?.availableModels?[option.model] != nil ? option.model : option.id
        }
        Task {
            defer { isSavingAgents = false }
            do {
                let result = try await runCLI(["models", "select"] + ordered)
                guard result.exitCode == 0 else { throw commandError(result) }
                show("Codex 原生子 Agent 已更新；新建任务或重新加载 Codex 后生效", .success)
            } catch {
                show("保存失败：\(error.localizedDescription)", .error)
            }
        }
    }

    func installCodexIntegration() {
        Task {
            do {
                let result = try await runCLI(["install", "client"])
                guard result.exitCode == 0 else { throw commandError(result) }
                show("PCL Relay 原生子 Agent 已安装/修复；请新建任务或重新加载 Codex", .success)
                refreshAll()
            } catch {
                show("安装失败：\(error.localizedDescription)", .error)
            }
        }
    }

}
