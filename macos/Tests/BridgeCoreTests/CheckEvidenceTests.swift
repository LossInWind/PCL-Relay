import XCTest
@testable import BridgeCore

final class CheckEvidenceTests: XCTestCase {
    func testLateCompletionIgnoredAndFailureKeepsHistory() {
        var state = CheckEvidence()
        let first = state.begin(), second = state.begin()
        state.finish(first)
        XCTAssertEqual(state.phase, .checking)
        let date = Date(timeIntervalSince1970: 1)
        state.finish(second, at: date)
        state.finish(state.begin(), error: "timeout")
        XCTAssertEqual(state.phase, .failed)
        XCTAssertEqual(state.lastSuccessAt, date)
        XCTAssertTrue(state.summary.contains("历史"))
    }
    func testPendingSelectionSurvivesPreviousWriteSuccess() {
        var state = AgentSelectionState(["a"])
        state.request(["a", "b"])
        XCTAssertEqual(state.takeNext(), ["a", "b"])
        state.request(["c"])
        state.succeed(["a", "b"])
        XCTAssertEqual(state.displayed, ["c"])
        state.fail(["c"])
        XCTAssertEqual(state.displayed, ["a", "b"])
        XCTAssertEqual(state.failed, ["c"])
    }
}
