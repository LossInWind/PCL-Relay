import AppKit
import BridgeCore
import SwiftUI

struct PlannedTopologyEdge: Identifiable, Hashable {
    var id: String { "\(from)-\(to)-\(type)" }
    let from: String
    let to: String
    let type: String
    var verified: Bool
}

struct NetworkView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var positions: [String: CGPoint] = [:]
    @State private var dragOrigins: [String: CGPoint] = [:]
    @State private var selectedID: String?
    @State private var linkStart: String?
    @State private var linking = false
    @State private var plannedEdges: [PlannedTopologyEdge] = []
    @State private var interactionHint = "拖动设备调整布局；开启连线模式后依次点击两个设备的连接点。"
    @State private var initializedRecommendation = false
    @State private var showLogs = false
    @State private var consensusRippleProgress: CGFloat = 1

    private var relayID: String { model.relayDiscovery?.recommendation?.relayID ?? model.currentRelay?.tailscaleIP ?? "" }
    private var localID: String { model.tailnetNodes.first(where: \.isSelf)?.tailscaleIP ?? "" }
    private var selectedNode: RelayCandidate? { model.tailnetNodes.first { $0.tailscaleIP == selectedID } }
    private var topologyHeight: CGFloat {
        let upstreamLane = model.tailnetNodes.filter {
            !$0.isSelf && ($0.tailscaleIP == relayID || $0.feasibility?.recommendedRoute == "local_pcl_direct")
        }.count
        let peerLane = model.tailnetNodes.filter {
            !$0.isSelf && $0.tailscaleIP != relayID && $0.feasibility?.recommendedRoute != "local_pcl_direct"
        }.count
        return max(390, CGFloat(max(max(upstreamLane, peerLane), 1)) * 96 + 100)
    }
    private var topologySignature: [String] {
        model.tailnetNodes.map {
            "\($0.tailscaleIP):\($0.feasibility?.recommendedRoute ?? "unavailable"):read=\($0.online)"
        }
    }

    var body: some View {
        ScrollView {
        VStack(spacing: 16) {
            RelayOverviewCard()

            HStack(spacing: 10) {
                VStack(alignment: .leading, spacing: 3) {
                    Text("连接拓扑").font(.title3.weight(.semibold))
                    Text("当前中转站：\(shortDeviceName(model.currentRelay?.nodeName ?? model.relayNodeName))  ·  \(interactionHint)")
                        .font(.caption).foregroundStyle(.secondary)
                    if let consensus = model.relayDiscovery?.consensus {
                        HStack(spacing: 6) {
                            Image(systemName: consensus.complete == true ? "checkmark.circle.fill" : "clock.badge.questionmark")
                            Text(consensusLabel(consensus))
                        }
                        .font(.caption2.weight(.medium))
                        .foregroundStyle(consensus.complete == true ? Color.green : Color.orange)
                    }
                }
                Spacer()
                Button { useRecommendation() } label: { Label("使用自动推荐", systemImage: "wand.and.stars") }
                    .buttonStyle(SecondaryButtonStyle())
                Button {
                    linking.toggle()
                    linkStart = nil
                    interactionHint = linking ? "连线模式：先点击来源设备的连接点，再点击目标设备。" : "拖动设备调整布局；点击设备查看可行性。"
                } label: {
                    Label(linking ? "结束连线" : "点击连线", systemImage: linking ? "xmark.circle" : "point.topleft.down.to.point.bottomright.curvepath")
                }
                .buttonStyle(SecondaryButtonStyle())
                Button { model.applyTopology() } label: {
                    Label(model.isApplyingTopology ? "正在应用" : "应用并验证", systemImage: "checkmark.shield.fill")
                }
                .buttonStyle(PrimaryButtonStyle())
                .disabled(model.isApplyingTopology || model.topologyRoutes.isEmpty)
            }

            GeometryReader { proxy in
                ZStack {
                    TopologyGrid()

                    if let origin = positions[relayID], (model.relayDiscovery?.consensus?.roundID ?? 0) > 0 {
                        TopologyConsensusRipple(
                            origin: origin,
                            progress: consensusRippleProgress,
                            reduceMotion: reduceMotion
                        )
                    }

                    ForEach(plannedEdges) { edge in
                        if let start = positions[edge.from], let end = positions[edge.to] {
                            TopologyConnection(edge: edge, start: start, end: end)
                        }
                    }

                    TopologyDeviceNode(
                        title: "PCL 内网 API",
                        subtitle: "llmapi.pcl.ac.cn",
                        symbol: "sparkles.rectangle.stack.fill",
                        tint: .purple,
                        badges: ["上游"],
                        selected: selectedID == "pcl-api",
                        linking: linking,
                        armed: linkStart == "pcl-api"
                    )
                    .position(position(for: "pcl-api", in: proxy.size))
                    .onTapGesture { handleNodeTap("pcl-api") }
                    .gesture(dragGesture(for: "pcl-api", size: proxy.size))

                    ForEach(model.tailnetNodes) { node in
                        TopologyDeviceNode(
                            title: shortName(node.nodeName),
                            subtitle: node.isSelf ? "当前 Mac" : "\(node.clientStatus?.system ?? "设备") · \(node.tailscaleIP)",
                            symbol: node.gateway ? "server.rack" : (node.isSelf || node.clientStatus?.system == "Darwin" ? "laptopcomputer" : "shippingbox.fill"),
                            tint: nodeColor(node),
                            badges: nodeBadges(node),
                            selected: selectedID == node.tailscaleIP,
                            linking: linking,
                            armed: linkStart == node.tailscaleIP
                        )
                        .position(position(for: node.tailscaleIP, in: proxy.size))
                        .onTapGesture { handleNodeTap(node.tailscaleIP) }
                        .gesture(dragGesture(for: node.tailscaleIP, size: proxy.size))
                    }
                }
                .coordinateSpace(name: "topology")
                .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 18).stroke(Color.white.opacity(0.09)))
                .onAppear {
                    seedPositions(size: proxy.size, reset: true)
                    if model.relayDiscovery?.recommendation != nil {
                        useRecommendation()
                        initializedRecommendation = true
                    }
                }
                .onChange(of: topologySignature) { _, _ in
                    seedPositions(size: proxy.size, reset: true)
                    if plannedEdges.isEmpty { useRecommendation() }
                }
                .onChange(of: model.relayDiscovery?.checkedAt) { _, _ in
                    if !initializedRecommendation || plannedEdges.isEmpty {
                        useRecommendation()
                        initializedRecommendation = model.relayDiscovery?.recommendation != nil
                    }
                }
                .onChange(of: model.relayDiscovery?.consensus?.roundID) { _, roundID in
                    guard let roundID, roundID > 0 else { return }
                    triggerConsensusRipple()
                }
                .onChange(of: model.topologyRoutes) { _, _ in rebuildEdges() }
            }
            .frame(height: topologyHeight)

            TopologyInspector(node: selectedNode, relayID: relayID, onPlan: planRoute, onSelectRelay: { node in model.selectRelay(node) })

            SectionHeader(
                title: "设备管理",
                subtitle: "每台设备只保留可用状态、当前路径、客户端版本和一个主操作",
                actionTitle: model.isDiscoveringNodes ? "正在扫描" : "扫描全部",
                actionSymbol: "arrow.clockwise",
                action: model.refreshTailnetNodes
            )

            SoftwareUpdateStrip()

            VStack(spacing: 8) {
                ForEach(sortedNodes) { node in
                    UnifiedDeviceRow(node: node)
                }
            }

            DisclosureGroup("当前中转站服务") {
                ServerControlCard(showLogs: $showLogs)
                    .padding(.top, 10)
                if showLogs {
                    ConsolePanel(text: model.gatewayLogs, title: "中转站日志（已脱敏）")
                        .padding(.top, 10)
                }
            }
            .font(.subheadline.weight(.medium))
            .foregroundStyle(.secondary)
        }
        .padding(22)
        }
    }

    private var sortedNodes: [RelayCandidate] {
        model.tailnetNodes.sorted {
            if $0.selected != $1.selected { return $0.selected }
            if $0.isSelf != $1.isSelf { return $0.isSelf }
            if $0.online != $1.online { return $0.online }
            return $0.nodeName.localizedCaseInsensitiveCompare($1.nodeName) == .orderedAscending
        }
    }

    private func seedPositions(size: CGSize, reset: Bool = false) {
        let canvasWidth = max(size.width, 820)
        let canvasHeight = max(size.height, topologyHeight)
        let xAPI = max(105, canvasWidth * 0.10)
        let xUpstream = canvasWidth * 0.34
        let xLocal = canvasWidth * 0.58
        let xPeers = min(canvasWidth - 110, canvasWidth * 0.84)

        if reset { positions = [:] }
        positions["pcl-api"] = positions["pcl-api"] ?? CGPoint(x: xAPI, y: canvasHeight * 0.52)

        let devices = model.tailnetNodes
        if let relay = devices.first(where: { $0.tailscaleIP == relayID }) {
            positions[relay.tailscaleIP] = positions[relay.tailscaleIP] ?? CGPoint(x: xUpstream, y: canvasHeight * 0.52)
        }
        if let local = devices.first(where: \.isSelf) {
            positions[local.tailscaleIP] = positions[local.tailscaleIP] ?? CGPoint(x: xLocal, y: canvasHeight * 0.52)
        }

        let directPCL = devices.filter {
            !$0.isSelf && $0.tailscaleIP != relayID && $0.feasibility?.recommendedRoute == "local_pcl_direct"
        }
        let directSlots = laneSlots(count: directPCL.count, height: canvasHeight, avoidCenter: true)
        for (node, y) in zip(directPCL, directSlots) {
            positions[node.tailscaleIP] = positions[node.tailscaleIP] ?? CGPoint(x: xUpstream, y: y)
        }

        let peers = devices.filter {
            !$0.isSelf && $0.tailscaleIP != relayID && $0.feasibility?.recommendedRoute != "local_pcl_direct"
        }
        let peerSlots = laneSlots(count: peers.count, height: canvasHeight, avoidCenter: false)
        for (node, y) in zip(peers, peerSlots) {
            positions[node.tailscaleIP] = positions[node.tailscaleIP] ?? CGPoint(x: xPeers, y: y)
        }
    }

    private func laneSlots(count: Int, height: CGFloat, avoidCenter: Bool) -> [CGFloat] {
        guard count > 0 else { return [] }
        let top: CGFloat = 62
        let bottom = height - 62
        if avoidCenter {
            let fractions: [CGFloat] = [0.20, 0.84, 0.34, 0.70, 0.08, 0.96]
            let candidates = fractions.map { min(max(height * $0, top), bottom) }
            if count <= candidates.count { return Array(candidates.prefix(count)) }
        }
        if count == 1 { return [height * 0.52] }
        return (0..<count).map { index in
            top + (bottom - top) * CGFloat(index) / CGFloat(count - 1)
        }
    }

    private func position(for id: String, in size: CGSize) -> CGPoint {
        positions[id] ?? CGPoint(x: size.width / 2, y: size.height / 2)
    }

    private func dragGesture(for id: String, size: CGSize) -> some Gesture {
        DragGesture(minimumDistance: 6, coordinateSpace: .named("topology"))
            .onChanged { value in
                let origin = dragOrigins[id] ?? positions[id] ?? value.startLocation
                dragOrigins[id] = origin
                positions[id] = CGPoint(x: origin.x + value.translation.width, y: origin.y + value.translation.height)
            }
            .onEnded { _ in
                dragOrigins[id] = nil
                guard let point = positions[id] else { return }
                let clamped = CGPoint(
                    x: min(max(point.x, 110), size.width - 110),
                    y: min(max(point.y, 70), size.height - 70)
                )
                if reduceMotion { positions[id] = clamped }
                else { withAnimation(.spring(response: 0.4, dampingFraction: 1.0)) { positions[id] = clamped } }
            }
    }

    private func handleNodeTap(_ id: String) {
        selectedID = id == "pcl-api" ? nil : id
        guard linking else { return }
        if let source = linkStart, source != id {
            connect(source: source, target: id)
            linkStart = nil
        } else {
            linkStart = id
            interactionHint = "已选择来源；请点击目标设备的连接点。"
        }
    }

    private func connect(source: String, target: String) {
        let endpointIDs = [target, source]
        let node = endpointIDs.compactMap { id in
            model.tailnetNodes.first(where: { $0.tailscaleIP == id })
        }.first(where: { !$0.isSelf && !$0.gateway }) ?? endpointIDs.compactMap { id in
            model.tailnetNodes.first(where: { $0.tailscaleIP == id })
        }.first
        guard let node else { return }
        let route = node.feasibility?.recommendedRoute ?? "unavailable"
        switch route {
        case "direct" where source == relayID || target == relayID:
            planRoute(node, "direct")
        case "local_pcl_direct" where source == "pcl-api" || target == "pcl-api":
            planRoute(node, "local_pcl_direct")
        case "bridge_via_local_mac" where source == localID || target == localID:
            planRoute(node, "bridge_via_local_mac")
        default:
            interactionHint = "这两个端点没有通过可行性检测；请使用右侧建议路径。"
        }
    }

    private func planRoute(_ node: RelayCandidate, _ route: String) {
        model.planTopologyRoute(node, route: route)
        rebuildEdges()
        interactionHint = "已规划 \(shortName(node.nodeName))：\(routeLabel(route))。点击“应用并验证”后才会更改配置。"
    }

    private func useRecommendation() {
        guard let recommendation = model.relayDiscovery?.recommendation else { return }
        model.useRecommendedTopology()
        plannedEdges = recommendation.edges.map { .init(from: $0.from, to: $0.to, type: $0.type, verified: $0.verified) }
        interactionHint = "已载入自动推荐：\(recommendation.relayName) 作为中转站；实线为已验证，虚线为待应用。"
    }

    private func rebuildEdges() {
        guard let recommendation = model.relayDiscovery?.recommendation else {
            plannedEdges = []
            return
        }
        var edges = recommendation.edges.map { PlannedTopologyEdge(from: $0.from, to: $0.to, type: $0.type, verified: $0.verified) }
        for (nodeID, route) in model.topologyRoutes {
            guard let node = model.tailnetNodes.first(where: { $0.tailscaleIP == nodeID }) else { continue }
            edges.removeAll { $0.to == nodeID && $0.type != "upstream" }
            switch route {
            case "direct":
                if nodeID != relayID { edges.append(.init(from: relayID, to: nodeID, type: route, verified: node.clientStatus?.ready == true)) }
            case "local_pcl_direct":
                edges.append(.init(from: "pcl-api", to: nodeID, type: route, verified: node.feasibility?.localPCLDirectActive == true))
            case "bridge_via_local_mac":
                edges.append(.init(from: localID, to: nodeID, type: route, verified: false))
            default: break
            }
        }
        plannedEdges = Array(Dictionary(grouping: edges, by: \.id).compactMap { $0.value.first })
    }

    private func nodeColor(_ node: RelayCandidate) -> Color {
        if let test = model.deviceTests[node.tailscaleIP] {
            if test.status == "offline" || test.status == "unreachable" { return .orange }
            if test.status == "ready" && test.route == "local_pcl_direct" { return .cyan }
        }
        if node.selected && node.gateway && node.pclAuth == "valid" { return .green }
        switch node.feasibility?.recommendedRoute {
        case "direct": return .blue
        case "local_pcl_direct": return .cyan
        case "bridge_via_local_mac": return .orange
        default:
            return node.gateway && node.pclAuth == "valid" && node.feasibility?.relayCapable == true ? .green : .gray
        }
    }

    private func nodeBadges(_ node: RelayCandidate) -> [String] {
        var values: [String] = []
        if node.selected && node.gateway && node.feasibility?.relayCapable == true { values.append("当前中转站") }
        else if node.feasibility?.localPCLDirectActive == true { values.append("本地适配器") }
        if node.isSelf { values.append("桥接设备") }
        values.append(routeLabel(node.feasibility?.recommendedRoute ?? "unavailable"))
        if node.feasibility?.relayCapable == true && !node.selected { values.append("可作中转") }
        if model.deviceTests[node.tailscaleIP]?.status == "ready" { values.append("已验证") }
        return values
    }

    private func routeLabel(_ route: String) -> String {
        switch route {
        case "direct": return "中转直连"
        case "local_pcl_direct": return "PCL 本地直连"
        case "bridge_via_local_mac": return "经 Mac 桥接"
        default: return "不可用"
        }
    }

    private func consensusLabel(_ consensus: TopologyConsensus) -> String {
        let expected = consensus.expectedCount ?? consensus.reportCount
        if consensus.complete == true {
            return "全网已同步 · 轮次 #\(consensus.roundID) · \(consensus.reportCount)/\(expected) 台 · 每 \(consensus.intervalSeconds) 秒"
        }
        return "正在收集心跳 · \(consensus.reportCount)/\(expected) 台 · 每 \(consensus.intervalSeconds) 秒"
    }

    private func triggerConsensusRipple() {
        var transaction = Transaction()
        transaction.disablesAnimations = true
        withTransaction(transaction) { consensusRippleProgress = 0 }
        DispatchQueue.main.async {
            withAnimation(.easeOut(duration: reduceMotion ? 0.35 : 1.6)) {
                consensusRippleProgress = 1
            }
        }
    }

    private func shortName(_ value: String) -> String {
        value.replacingOccurrences(of: "haichen-", with: "")
    }
}
