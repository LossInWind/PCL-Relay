import Foundation

public struct RoutingRuntime: Codable, Equatable, Sendable {
    public let installedVersion: String?
    public let opencodexVersion: String?
    public let configuredEndpoint: String?
    public let transportHealthy: Bool?
    public let checkedAt: String?
    enum CodingKeys: String, CodingKey {
        case installedVersion = "installed_version", opencodexVersion = "opencodex_version"
        case configuredEndpoint = "configured_endpoint", transportHealthy = "transport_healthy", checkedAt = "checked_at"
    }
}

/// Presentation only: never applies topology or infers routes from heartbeats.
public struct RoutingDevice: Identifiable, Equatable, Sendable {
    public var id: String
    public var name: String
    public var isLocal: Bool
    public var online: Bool?
    public var runningVersion: String?
    public var installedVersion: String?
    public var targets: [DeploymentTarget]
    public var peers: [RelaySyncPeer]
    public var runtime: RoutingRuntime? { targets.compactMap(\.runtime).first ?? peers.compactMap(\.runtime).first }

    public var needsRestart: Bool {
        guard let installedVersion, !installedVersion.isEmpty,
              let runningVersion, !runningVersion.isEmpty else { return false }
        return installedVersion.compare(runningVersion, options: .numeric) == .orderedDescending
    }

    public var connectionTitle: String {
        if isLocal { return "当前设备" }
        if online == true { return "Relay 在线" }
        if targets.contains(where: { $0.ssh == true }) { return "SSH 可达 · Relay 未就绪" }
        return online == false ? "离线或不可达" : "尚未检查"
    }

    public var versionTitle: String {
        if needsRestart { return "待重启完成升级" }
        return runningVersion.flatMap { $0.isEmpty ? nil : $0 } ?? "运行版本未知"
    }
}

public enum RoutingSnapshot {
    private static func endpoint(_ raw: String) -> String {
        // Exact endpoint equivalence only; no DNS guesses and no name matching.
        guard var url = URLComponents(string: raw) else { return raw }
        url.host = url.host?.lowercased()
        while url.path.hasSuffix("/") { url.path.removeLast() }
        return url.string ?? raw
    }

    public static func sameEndpoint(_ lhs: String, _ rhs: String) -> Bool {
        !lhs.isEmpty && !rhs.isEmpty && endpoint(lhs) == endpoint(rhs)
    }

    public static func devices(localID: String, localName: String, localVersion: String,
                               peers: [RelaySyncPeer], targets: [DeploymentTarget]) -> [RoutingDevice] {
        var rows = [RoutingDevice(id: "local:\(localID)", name: localName, isLocal: true,
                                  online: true, runningVersion: localVersion, installedVersion: nil,
                                  targets: [], peers: [])]
        for target in targets.sorted(by: { $0.id < $1.id }) {
            let identity = target.relayNodeID.flatMap { $0.isEmpty ? nil : $0 }
            let key = identity.map { "node:\($0)" } ?? "target:\(target.id)"
            if let i = rows.firstIndex(where: { $0.id == key || (identity == localID && $0.isLocal) }) {
                rows[i].targets.append(target)
            } else {
                rows.append(RoutingDevice(id: key, name: target.name, isLocal: false,
                                          online: target.receiverOnline, runningVersion: target.relayVersion,
                                          installedVersion: target.version, targets: [target], peers: []))
            }
        }
        for peer in peers.sorted(by: { $0.id < $1.id }) {
            let identity = peer.nodeID.flatMap { $0.isEmpty ? nil : $0 }
            let match = rows.firstIndex { row in
                if let identity { return row.id == "node:\(identity)" || (row.isLocal && identity == localID) }
                return row.targets.contains { $0.receiverOnline == true && endpoint($0.controlURL) == endpoint(peer.url) }
                    || row.peers.contains { endpoint($0.url) == endpoint(peer.url) }
            }
            if let i = match {
                rows[i].peers.append(peer)
                if !rows[i].isLocal && rows[i].targets.isEmpty {
                    rows[i].online = peer.online
                    if peer.online == true { rows[i].runningVersion = peer.version }
                }
            } else {
                rows.append(RoutingDevice(id: identity.map { "node:\($0)" } ?? "peer:\(peer.id)",
                                          name: peer.name, isLocal: false, online: peer.online,
                                          runningVersion: peer.version, installedVersion: nil, targets: [], peers: [peer]))
            }
        }
        return rows.sorted { a, b in
            if a.isLocal != b.isLocal { return a.isLocal }
            return a.id < b.id
        }
    }
}
