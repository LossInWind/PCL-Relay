import XCTest
import BridgeCore
@testable import PCLCodexManager

@MainActor
final class ReliabilityTests: XCTestCase {
    private let initial: Set<String> = ["pcl_deepseek_pro"]
    private func registry(_ selection: Set<String>) -> String {
        let names = selection.sorted().map { "\"\($0)\"" }.joined(separator: ",")
        return "{\"gateway\":\"http://test/v1\",\"models\":{},\"selected_agents\":[\(names)]}"
    }
    private func waitUntil(_ condition: () -> Bool) async {
        for _ in 0..<1000 { if condition() { return }; await Task.yield() }
        XCTFail("Async test did not reach expected state")
    }
    func testRapidTogglesSaveLatestAndReadBack() async {
        let model = AppModel()
        model.selectedAgents = initial
        var stored = initial
        var writes = 0
        var firstWrite: CheckedContinuation<Void, Never>?
        model.commandOverride = { args in
            if args.prefix(2) == ["models", "select"] {
                writes += 1
                if writes == 1 { await withCheckedContinuation { firstWrite = $0 } }
                stored = Set(args.dropFirst(2))
            }
            return CommandResult(stdout: self.registry(stored), stderr: "", exitCode: 0)
        }
        model.setAgent("pcl_glm", enabled: true)
        await waitUntil { firstWrite != nil }
        model.setAgent("pcl_kimi", enabled: true)
        model.setAgent("pcl_glm", enabled: false)
        firstWrite?.resume()
        await waitUntil { !model.isSavingAgents }
        XCTAssertEqual(writes, 2)
        XCTAssertEqual(stored, ["pcl_deepseek_pro", "pcl_kimi"])
        XCTAssertEqual(model.selectedAgents, stored)
    }
    func testFailureRestoresConfirmedAndRetryIsExplicit() async {
        let model = AppModel(); model.selectedAgents = initial
        model.commandOverride = { _ in CommandResult(stdout: "", stderr: "failed", exitCode: 1) }
        model.setAgent("pcl_glm", enabled: true)
        await waitUntil { !model.isSavingAgents }
        XCTAssertEqual(model.selectedAgents, initial)
        XCTAssertNotNil(model.agentSelection.failed)
        XCTAssertTrue(model.agentSaveMessage.contains("未确认"))
        model.commandOverride = { _ in CommandResult(stdout: self.registry(["pcl_deepseek_pro", "pcl_glm"]), stderr: "", exitCode: 0) }
        model.retryAgentSave()
        await waitUntil { !model.isSavingAgents }
        XCTAssertEqual(model.selectedAgents, ["pcl_deepseek_pro", "pcl_glm"])
    }
    func testOldRegistryReadCannotOverwriteNewSelection() async {
        let model = AppModel(); model.selectedAgents = initial
        var read: CheckedContinuation<Void, Never>?
        model.commandOverride = { _ in
            await withCheckedContinuation { read = $0 }
            return CommandResult(stdout: self.registry(self.initial), stderr: "", exitCode: 0)
        }
        let task = Task { await model.readModelRegistry() }
        await waitUntil { read != nil }
        model.agentSelection.request(["pcl_kimi"])
        read?.resume(); await task.value
        XCTAssertEqual(model.selectedAgents, ["pcl_kimi"])
    }
    func testFullRefreshIsReadOnlyWaitsAndSurvivesPartialFailure() async {
        let model = AppModel()
        var commands: [[String]] = []
        var portal: CheckedContinuation<Void, Never>?
        model.commandOverride = { args in
            commands.append(args)
            if args == ["portal", "status"] { await withCheckedContinuation { portal = $0 } }
            if args == ["models", "show"] { return CommandResult(stdout: self.registry(self.initial), stderr: "", exitCode: 0) }
            return CommandResult(stdout: "", stderr: "offline fixture", exitCode: 1)
        }
        model.refreshAll()
        await waitUntil { portal != nil }
        XCTAssertTrue(model.isRefreshing)
        portal?.resume()
        await waitUntil { !model.isRefreshing }
        XCTAssertNotNil(model.registry)
        XCTAssertEqual(model.checks["connection"]?.phase, .failed)
        XCTAssertEqual(model.checks["portal"]?.phase, .failed)
        XCTAssertFalse(model.routeReady)
        for forbidden in ["install", "select", "restart", "detect", "enable", "stage", "now"] {
            XCTAssertFalse(commands.flatMap { $0 }.contains(forbidden), forbidden)
        }
    }
    func testOverlappingChecksCoalesce() async {
        let model = AppModel()
        var resume: CheckedContinuation<Void, Never>?
        var calls = 0
        let a = Task { await model.check("test") { calls += 1; await withCheckedContinuation { resume = $0 } } }
        await waitUntil { resume != nil }
        let b = Task { await model.check("test") { calls += 1 } }
        await Task.yield()
        resume?.resume(); await a.value; await b.value
        XCTAssertEqual(calls, 1)
    }
    func testDetectionBlocksSelectionAndDoesNotRunOnShowConfirmation() {
        let model = AppModel(); model.selectedAgents = initial
        model.showDetectionConfirmation = true
        XCTAssertFalse(model.isDetecting)
        model.isDetecting = true
        model.setAgent("pcl_glm", enabled: true)
        XCTAssertEqual(model.selectedAgents, initial)
        XCTAssertFalse(model.isSavingAgents)
    }
    func testDuplicateRepairIsBlocked() async {
        let model = AppModel()
        var requests = 0
        var pending: CheckedContinuation<Void, Never>?
        model.commandOverride = { _ in
            requests += 1
            await withCheckedContinuation { pending = $0 }
            return CommandResult(stdout: "", stderr: "fixture failure", exitCode: 1)
        }
        model.installCodexIntegration()
        await waitUntil { pending != nil }
        model.installCodexIntegration()
        XCTAssertEqual(requests, 1)
        pending?.resume()
        await waitUntil { !model.isInstallingIntegration }
    }

    func testCatalogShrinkPreservesSelectedCustomAgentDuringSave() async throws {
        let model = AppModel()
        let data = #"{"models":{},"catalog_checked_at":"2026-09-08","available_models":{},"selected_agents":["custom_agent","pcl_kimi"],"agent_definitions":{"custom_agent":{"model":"Custom-Model","description":"Saved model"}}}"#
        model.registry = try BridgeDecode.value(ModelRegistry.self, from: data)
        model.selectedAgents = ["custom_agent", "pcl_kimi"]
        XCTAssertEqual(Set(model.allDiscoveredModels.map(\.alias)), ["custom_agent", "pcl_kimi"])
        XCTAssertNotNil(model.catalogWarning(for: "Custom-Model"))
        var stored = Set<String>()
        model.commandOverride = { args in
            if args.prefix(2) == ["models", "select"] { stored = Set(args.dropFirst(2)) }
            return CommandResult(stdout: self.registry(stored), stderr: "", exitCode: 0)
        }
        model.setAgent("pcl_glm", enabled: true)
        await waitUntil { !model.isSavingAgents }
        XCTAssertEqual(stored, ["custom_agent", "pcl_kimi", "pcl_glm"])
        XCTAssertNil(model.agentSelection.failed)
    }

    func testUnknownSavedAliasCannotBeSilentlyDropped() async {
        let model = AppModel()
        model.selectedAgents = ["missing_definition"]
        var writes = 0
        model.commandOverride = { _ in
            writes += 1
            return CommandResult(stdout: "", stderr: "", exitCode: 0)
        }
        model.setAgent("pcl_glm", enabled: true)
        await waitUntil { !model.isSavingAgents }
        XCTAssertEqual(writes, 0)
        XCTAssertEqual(model.selectedAgents, ["missing_definition"])
        XCTAssertNotNil(model.agentSelection.failed)
    }
}
