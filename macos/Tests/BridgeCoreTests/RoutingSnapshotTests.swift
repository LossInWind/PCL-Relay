import XCTest
@testable import BridgeCore

final class RoutingSnapshotTests: XCTestCase {
    func testEndpointEvidenceNeverCrossesPathsOrHosts() {
        XCTAssertTrue(RoutingSnapshot.sameEndpoint("http://RELAY:15722/v1/", "http://relay:15722/v1"))
        XCTAssertFalse(RoutingSnapshot.sameEndpoint("http://relay:15722/v1", "http://relay:15722/v2"))
        XCTAssertFalse(RoutingSnapshot.sameEndpoint("http://relay:15722/v1", "http://100.1.2.3:15722/v1"))
        XCTAssertFalse(RoutingSnapshot.sameEndpoint("", ""))
    }
    private func target(_ id: String, node: String? = nil, online: Bool? = true, name: String = "same") throws -> DeploymentTarget {
        var value: [String: Any] = ["id": id, "name": name, "ssh_target": id, "control_url": "http://\(id):15726", "added_at": "",
                                    "version": "2.5.12", "relay_version": "2.5.10"]
        if let node { value["relay_node_id"] = node }
        if let online { value["receiver_online"] = online }
        return try JSONDecoder().decode(DeploymentTarget.self, from: JSONSerialization.data(withJSONObject: value))
    }
    private func peer(_ id: String, node: String? = nil, url: String? = nil) throws -> RelaySyncPeer {
        var value: [String: Any] = ["id": id, "name": "same", "url": url ?? "http://\(id):15726", "online": true, "error": "", "version": "2.5.10"]
        if let node { value["node_id"] = node }
        return try JSONDecoder().decode(RelaySyncPeer.self, from: JSONSerialization.data(withJSONObject: value))
    }
    private func rows(_ targets: [DeploymentTarget], _ peers: [RelaySyncPeer] = []) -> [RoutingDevice] {
        RoutingSnapshot.devices(localID: "local", localName: "Mac", localVersion: "2.5.12", peers: peers, targets: targets)
    }
    func testConfirmedAliasesAndPeerMergeByNodeIdentity() throws {
        let result = rows(try [target("a", node: "n"), target("b", node: "n")], try [peer("c", node: "n")])
        XCTAssertEqual(result.count, 2)
        XCTAssertEqual(result[1].targets.count, 2)
        XCTAssertEqual(result[1].peers.count, 1)
        XCTAssertTrue(result[1].needsRestart)
    }
    func testSameNamesWithoutIdentityDoNotMerge() throws {
        XCTAssertEqual(rows(try [target("a", online: nil), target("b", online: nil)]).count, 3)
    }
    func testOlderPeerCanMatchVerifiedExactEndpoint() throws {
        XCTAssertEqual(rows(try [target("a")], try [peer("other", url: "http://A:15726/")]).count, 2)
    }
    func testUnverifiedTargetIsNotAssumedToBePeer() throws {
        XCTAssertEqual(rows(try [target("a", online: nil)], try [peer("a")]).count, 3)
    }
    func testOrderStableAcrossRefresh() throws {
        let a = try target("a"), b = try target("b")
        XCTAssertEqual(rows([a, b]), rows([b, a]))
    }
    func testNewerRuntimeIsNotMarkedForDowngrade() {
        let device = RoutingDevice(id: "a", name: "a", isLocal: false, runningVersion: "2.5.13", installedVersion: "2.5.12", targets: [], peers: [])
        XCTAssertFalse(device.needsRestart)
    }
    func testUnknownRuntimeNeverUsesInstalledVersion() {
        let device = RoutingDevice(id: "a", name: "a", isLocal: false, installedVersion: "2.5.12", targets: [], peers: [])
        XCTAssertEqual(device.versionTitle, "运行版本未知")
        XCTAssertFalse(device.needsRestart)
    }
    func testLocalIdentityDoesNotDuplicateDevice() throws {
        XCTAssertEqual(rows(try [target("a", node: "local")], try [peer("b", node: "local")]).count, 1)
    }
}
