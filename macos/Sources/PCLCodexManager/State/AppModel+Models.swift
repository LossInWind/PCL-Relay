import BridgeCore
import Foundation

extension AppModel {
    func readOfficialCatalogStatus() async {
        do {
            let result = try await runCLI(["catalog", "status"])
            guard result.exitCode == 0,
                  let data = result.stdout.data(using: .utf8),
                  let state = try JSONSerialization.jsonObject(with: data) as? [String: Any]
            else { return }
            let enabled = state["auto_refresh_enabled"] as? Bool == true
            catalogSyncMessage = enabled
                ? "官方目录由 OpenCodex 每小时自动同步；已有窗口可能需在任务结束后重新打开。"
                : "官方目录自动同步未启用；可通过刷新模型目录手动更新。"
            if state["status"] as? String == "failed" {
                catalogSyncMessage += " 上次手动同步失败，旧目录已保留。"
            }
        } catch { catalogSyncMessage = "无法读取官方目录同步状态：\(error.localizedDescription)" }
    }

    func syncOfficialCatalog(ifDue: Bool) async {
        guard !isSyncingCatalog else { return }
        isSyncingCatalog = true
        defer { isSyncingCatalog = false }
        do {
            let result = try await runCLI(["catalog", "refresh"] + (ifDue ? ["--if-due"] : []))
            guard result.exitCode == 0,
                  let data = result.stdout.data(using: .utf8),
                  let state = try JSONSerialization.jsonObject(with: data) as? [String: Any]
            else { throw commandError(result) }
            let status = state["status"] as? String ?? "failed"
            if status == "success" {
                let time = (state["last_success"] as? Double).map {
                    Date(timeIntervalSince1970: $0).formatted(date: .abbreviated, time: .shortened)
                } ?? "未知"
                catalogSyncMessage = "官方目录已同步 · \(time)；已有窗口如未更新，请在任务结束后重新打开。"
            } else if status == "disabled" {
                catalogSyncMessage = "PCL 接入已关闭，未改动官方目录。"
            } else if status != "busy" {
                catalogSyncMessage = "官方目录同步失败，保留上次目录：\(state["error"] as? String ?? "上游未完成更新")"
            }
        } catch {
            catalogSyncMessage = "官方目录同步失败，保留上次目录：\(error.localizedDescription)"
        }
    }

    func discoverModels() {
        guard !isDiscovering, !isSavingAgents, !isDetecting else { return }
        isDiscovering = true
        commandLog = "正在从中转站读取最新模型目录……"
        Task {
            defer { isDiscovering = false }
            await syncOfficialCatalog(ifDue: false)
            let generation = checks["models", default: CheckEvidence()].begin()
            do {
                let result = try await runCLI(["models", "discover"])
                commandLog = BridgeDecode.prettyJSON(result.stdout)
                guard result.exitCode == 0 else { throw commandError(result) }
                let decoded = try BridgeDecode.value(ModelRegistry.self, from: result.stdout)
                registry = decoded
                selectedAgents = Set(decoded.selectedAgents ?? AgentDefinition.all.map(\.id))
                checks["models"]?.finish(generation)
                show("模型目录已更新：发现 \(decoded.availableModels?.count ?? 0) 个模型", .success)
            } catch {
                checks["models"]?.finish(generation, error: error.localizedDescription)
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
            remoteStatusText = "PID \(decoded.pid) · uptime \(decoded.uptimeSeconds)s · \(decoded.listenHost ?? decoded.tailscaleIP):\(decoded.port) · scope: \(decoded.adminScope.joined(separator: ", "))"
        } catch {
            remoteServiceActive = false
            remoteStatusText = error.localizedDescription
        }
    }

    func detectModels() {
        guard !isDetecting, !isSavingAgents, !isDiscovering else { return }
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
        guard !isDetecting, !isDiscovering, !isInstallingIntegration else { return }
        var desired = selectedAgents
        if enabled {
            desired.insert(id)
        } else if selectedAgents.count > 1 {
            desired.remove(id)
        } else {
            show("至少保留一个子 Agent", .info)
            return
        }
        agentSelection.request(desired)
        saveSelectedAgents()
    }

    func saveSelectedAgents() {
        guard !isSavingAgents else { return }
        isSavingAgents = true
        agentSaveMessage = "保存中…"
        Task {
            defer { isSavingAgents = false }
            while let desired = agentSelection.takeNext() {
                let options = agentOptions.filter { desired.contains($0.id) }
                guard Set(options.map(\.id)) == desired else {
                    agentSelection.fail(desired)
                    agentSaveMessage = "保存未确认：部分已选模型缺少定义；未写入配置，请刷新目录后重试"
                    return
                }
                let ordered = options.map { option in
                    registry?.availableModels?[option.model] != nil ? option.model : option.id
                }
                do {
                    let result = try await runCLI(["models", "select"] + ordered)
                    guard result.exitCode == 0 else { throw commandError(result) }
                    let readback = try await runCLI(["models", "show"])
                    guard readback.exitCode == 0 else { throw commandError(readback) }
                    let value = try BridgeDecode.value(ModelRegistry.self, from: readback.stdout)
                    guard let selected = value.selectedAgents, Set(selected) == desired else {
                        throw NSError(domain: "PCLRelay", code: 1, userInfo: [NSLocalizedDescriptionKey: "保存回读不一致，请重试"])
                    }
                    registry = value
                    agentSelection.succeed(Set(selected))
                } catch {
                    agentSelection.fail(desired)
                    agentSaveMessage = "保存未确认：\(error.localizedDescription)；界面已恢复最近确认的选择，请重试核对实际配置"
                    return
                }
            }
            agentSaveMessage = "已保存并回读确认；新任务或重新加载 Codex 后生效"
        }
    }

    func retryAgentSave() {
        guard !isSavingAgents, let desired = agentSelection.failed else { return }
        agentSelection.request(desired)
        saveSelectedAgents()
    }

    func installCodexIntegration() {
        guard !isInstallingIntegration, !isSavingAgents, !isDiscovering, !isDetecting else { return }
        isInstallingIntegration = true
        Task {
            defer { isInstallingIntegration = false }
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
