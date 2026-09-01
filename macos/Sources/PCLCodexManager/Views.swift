import AppKit
import BridgeCore
import SwiftUI

private enum AppSection: String, CaseIterable, Identifiable {
    case network = "网络"
    case models = "模型与 Agent"
    case portal = "PCL 门户"
    var id: String { rawValue }
    var symbol: String {
        switch self {
        case .network: return "point.3.connected.trianglepath.dotted"
        case .models: return "person.3.sequence.fill"
        case .portal: return "globe.asia.australia.fill"
        }
    }
}

struct RootView: View {
    @EnvironmentObject private var model: AppModel
    @AppStorage("selectedSection") private var sectionRaw = AppSection.network.rawValue

    private var section: AppSection {
        AppSection(rawValue: sectionRaw) ?? .network
    }

    private var sectionBinding: Binding<AppSection> {
        Binding(
            get: { section },
            set: { sectionRaw = $0.rawValue }
        )
    }

    var body: some View {
        ZStack(alignment: .top) {
            LinearGradient(
                colors: [Color(nsColor: .windowBackgroundColor), Color.accentColor.opacity(0.055)],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
            .ignoresSafeArea()

            VStack(spacing: 0) {
                HeaderBar(section: sectionBinding)
                Group {
                    switch section {
                    case .network: NetworkView()
                    case .models: ModelsAgentsView()
                    case .portal: PortalView()
                    }
                }
            }

            if let banner = model.banner {
                BannerView(message: banner)
                    .padding(.top, 62)
                    .transition(.move(edge: .top).combined(with: .opacity))
                    .zIndex(4)
            }
        }
        .preferredColorScheme(.dark)
    }
}

private struct PlannedTopologyEdge: Identifiable, Hashable {
    var id: String { "\(from)-\(to)-\(type)" }
    let from: String
    let to: String
    let type: String
    var verified: Bool
}

private struct NetworkView: View {
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

    private var relayID: String { model.relayDiscovery?.recommendation?.relayID ?? model.currentRelay?.tailscaleIP ?? "" }
    private var localID: String { model.tailnetNodes.first(where: \.isSelf)?.tailscaleIP ?? "" }
    private var selectedNode: RelayCandidate? { model.tailnetNodes.first { $0.tailscaleIP == selectedID } }

    var body: some View {
        ScrollView {
        VStack(spacing: 16) {
            RelayOverviewCard()

            HStack(spacing: 10) {
                VStack(alignment: .leading, spacing: 3) {
                    Text("连接拓扑").font(.title3.weight(.semibold))
                    Text("当前中转站：\(shortDeviceName(model.currentRelay?.nodeName ?? model.relayNodeName))  ·  \(interactionHint)")
                        .font(.caption).foregroundStyle(.secondary)
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
                    seedPositions(size: proxy.size)
                    if model.relayDiscovery?.recommendation != nil {
                        useRecommendation()
                        initializedRecommendation = true
                    }
                }
                .onChange(of: model.tailnetNodes.map(\.tailscaleIP)) { _, _ in
                    seedPositions(size: proxy.size)
                    if plannedEdges.isEmpty { useRecommendation() }
                }
                .onChange(of: model.relayDiscovery?.checkedAt) { _, _ in
                    if !initializedRecommendation || plannedEdges.isEmpty {
                        useRecommendation()
                        initializedRecommendation = model.relayDiscovery?.recommendation != nil
                    }
                }
                .onChange(of: model.topologyRoutes) { _, _ in rebuildEdges() }
            }
            .frame(height: 370)

            TopologyInspector(node: selectedNode, relayID: relayID, onPlan: planRoute, onSelectRelay: { node in model.selectRelay(node) })

            SectionHeader(
                title: "设备与远端更新",
                subtitle: "拓扑负责连接关系；这里负责逐台检查、安装和升级 Codex 接入",
                actionTitle: model.isDiscoveringNodes ? "正在扫描" : "扫描全部",
                actionSymbol: "arrow.clockwise",
                action: model.refreshTailnetNodes
            )

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

    private func seedPositions(size: CGSize) {
        let width = max(size.width - 80, 760)
        let height = max(size.height - 60, 390)
        positions["pcl-api"] = positions["pcl-api"] ?? CGPoint(x: width * 0.13, y: height * 0.5)
        let devices = model.tailnetNodes
        for (index, node) in devices.enumerated() where positions[node.tailscaleIP] == nil {
            if node.tailscaleIP == relayID {
                positions[node.tailscaleIP] = CGPoint(x: width * 0.37, y: height * 0.5)
            } else if node.isSelf {
                positions[node.tailscaleIP] = CGPoint(x: width * 0.61, y: height * 0.36)
            } else {
                let row = CGFloat(index % 3)
                positions[node.tailscaleIP] = CGPoint(x: width * 0.84, y: height * (0.20 + row * 0.30))
            }
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
        interactionHint = "已载入自动推荐：\(recommendation.relayName) 作为中转站；绿色为已验证，蓝色虚线为待应用。"
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
        if node.gateway && node.pclAuth == "valid" && node.feasibility?.relayCapable == true { return .green }
        switch node.feasibility?.recommendedRoute {
        case "direct": return .blue
        case "local_pcl_direct": return .cyan
        case "bridge_via_local_mac": return .orange
        default: return .gray
        }
    }

    private func nodeBadges(_ node: RelayCandidate) -> [String] {
        var values: [String] = []
        if node.gateway && node.feasibility?.relayCapable == true { values.append("中转站") }
        else if node.feasibility?.localPCLDirectActive == true { values.append("本地适配器") }
        if node.isSelf { values.append("桥接设备") }
        if node.feasibility?.relayCapable == true && !node.gateway { values.append("可作中转") }
        values.append(routeLabel(node.feasibility?.recommendedRoute ?? "unavailable"))
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

    private func shortName(_ value: String) -> String {
        value.replacingOccurrences(of: "haichen-", with: "")
    }
}

private struct TopologyGrid: View {
    var body: some View {
        Canvas { context, size in
            var path = Path()
            stride(from: 0.0, through: size.width, by: 28).forEach { x in
                path.move(to: CGPoint(x: x, y: 0)); path.addLine(to: CGPoint(x: x, y: size.height))
            }
            stride(from: 0.0, through: size.height, by: 28).forEach { y in
                path.move(to: CGPoint(x: 0, y: y)); path.addLine(to: CGPoint(x: size.width, y: y))
            }
            context.stroke(path, with: .color(.white.opacity(0.035)), lineWidth: 0.5)
        }
        .background(Color.black.opacity(0.13))
    }
}

private struct TopologyConnection: View {
    let edge: PlannedTopologyEdge
    let start: CGPoint
    let end: CGPoint

    private var color: Color {
        switch edge.type {
        case "upstream": return .purple
        case "direct": return .green
        case "local_pcl_direct": return .cyan
        default: return .orange
        }
    }

    private var label: String {
        switch edge.type {
        case "upstream": return "PCL 上游"
        case "direct": return "Tailnet 直连"
        case "local_pcl_direct": return "本地 API"
        default: return "SSH 桥接"
        }
    }

    var body: some View {
        let midpoint = CGPoint(x: (start.x + end.x) / 2, y: (start.y + end.y) / 2)
        ZStack {
            Path { path in
                path.move(to: start)
                let bend = max(50, abs(end.x - start.x) * 0.42)
                path.addCurve(to: end, control1: CGPoint(x: start.x + bend, y: start.y), control2: CGPoint(x: end.x - bend, y: end.y))
            }
            .stroke(color.opacity(edge.verified ? 0.82 : 0.62), style: StrokeStyle(lineWidth: edge.verified ? 3 : 2, dash: edge.verified ? [] : [8, 7]))

            Text(label)
                .font(.caption2.weight(.medium))
                .padding(.horizontal, 7).padding(.vertical, 3)
                .background(.ultraThinMaterial, in: Capsule())
                .overlay(Capsule().stroke(color.opacity(0.35)))
                .position(midpoint)
        }
        .allowsHitTesting(false)
    }
}

private struct TopologyDeviceNode: View {
    let title: String
    let subtitle: String
    let symbol: String
    let tint: Color
    let badges: [String]
    let selected: Bool
    let linking: Bool
    let armed: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 9) {
                Image(systemName: symbol).foregroundStyle(tint).font(.system(size: 19, weight: .medium))
                VStack(alignment: .leading, spacing: 1) {
                    Text(title).font(.subheadline.weight(.semibold)).lineLimit(1)
                    Text(subtitle).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
                }
            }
            HStack(spacing: 4) {
                ForEach(badges.prefix(2), id: \.self) { badge in
                    Text(badge).font(.caption2.weight(.medium)).padding(.horizontal, 6).padding(.vertical, 2)
                        .background(tint.opacity(0.12), in: Capsule())
                }
                Spacer()
                Circle().fill(armed ? Color.yellow : (linking ? tint : Color.secondary.opacity(0.45)))
                    .frame(width: armed ? 13 : 10, height: armed ? 13 : 10)
                    .overlay(Circle().stroke(.white.opacity(0.55), lineWidth: 1))
            }
        }
        .padding(12)
        .frame(width: 205)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(selected || armed ? tint.opacity(0.95) : Color.white.opacity(0.11), lineWidth: selected || armed ? 2 : 1))
        .shadow(color: tint.opacity(selected ? 0.22 : 0.08), radius: selected ? 18 : 8, y: 5)
        .contentShape(RoundedRectangle(cornerRadius: 14))
    }
}

private struct TopologyInspector: View {
    @EnvironmentObject private var model: AppModel
    let node: RelayCandidate?
    let relayID: String
    let onPlan: (RelayCandidate, String) -> Void
    let onSelectRelay: (RelayCandidate) -> Void

    var body: some View {
        GlassCard {
            if let node {
                VStack(spacing: 10) {
                    HStack(spacing: 12) {
                        Image(systemName: deviceSymbol(node))
                            .font(.system(size: 18, weight: .medium))
                            .foregroundStyle(node.online ? .blue : .orange)
                            .frame(width: 38, height: 38)
                            .background(Color.blue.opacity(0.09), in: RoundedRectangle(cornerRadius: 10))
                        VStack(alignment: .leading, spacing: 3) {
                            Text(shortDeviceName(node.nodeName)).font(.headline)
                            Text(model.deviceTests[node.tailscaleIP]?.summary ?? node.feasibility?.recommendationReason ?? "尚未完成可行性检测")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        Button { model.refreshDevice(node) } label: { Label("刷新", systemImage: "arrow.clockwise") }
                            .buttonStyle(QuietButtonStyle())
                            .disabled(model.refreshingDeviceIDs.contains(node.tailscaleIP) || model.testingDeviceIDs.contains(node.tailscaleIP))
                        Button { model.testDeviceConnectivity(node) } label: { Label("测试连通性", systemImage: "stethoscope") }
                            .buttonStyle(QuietButtonStyle())
                            .disabled(model.refreshingDeviceIDs.contains(node.tailscaleIP) || model.testingDeviceIDs.contains(node.tailscaleIP))
                        Menu("连接方式") {
                            if node.feasibility?.direct == true && !node.isSelf { Button("中转站直连") { onPlan(node, "direct") } }
                            if node.feasibility?.localPCLDirect == true { Button("PCL 本机直连") { onPlan(node, "local_pcl_direct") } }
                            if node.feasibility?.bridgeViaLocalMac == true { Button("经当前 Mac 桥接") { onPlan(node, "bridge_via_local_mac") } }
                        }
                        .menuStyle(.borderlessButton)
                        .fixedSize()
                        if node.gateway && node.pclAuth == "valid" && node.feasibility?.relayCapable == true && node.tailscaleIP != relayID {
                            Button("设为中转站") { onSelectRelay(node) }.buttonStyle(SecondaryButtonStyle())
                        }
                    }
                    if let test = model.deviceTests[node.tailscaleIP] {
                        HStack(spacing: 14) {
                            ForEach(test.checks) { check in
                                Label("\(check.name) · \(check.detail)", systemImage: check.passed ? "checkmark.circle.fill" : "xmark.circle.fill")
                                    .font(.caption2)
                                    .foregroundStyle(check.passed ? .green : .orange)
                            }
                            Spacer()
                        }
                        .padding(.leading, 50)
                    }
                }
            } else {
                Label("点击设备查看角色可行性、推荐路径和稳定性评分。", systemImage: "cursorarrow.click.2")
                    .foregroundStyle(.secondary)
            }
        }
    }
}

private struct HeaderBar: View {
    @EnvironmentObject private var model: AppModel
    @Binding var section: AppSection

    var body: some View {
        HStack(spacing: 16) {
            HStack(spacing: 10) {
                ZStack {
                    RoundedRectangle(cornerRadius: 9, style: .continuous)
                        .fill(LinearGradient(colors: [.blue, .cyan], startPoint: .topLeading, endPoint: .bottomTrailing))
                    Image(systemName: "point.3.connected.trianglepath.dotted")
                        .font(.system(size: 15, weight: .bold))
                        .foregroundStyle(.white)
                }
                .frame(width: 32, height: 32)
                VStack(alignment: .leading, spacing: 1) {
                    Text("PCL Relay")
                        .font(.system(size: 15, weight: .semibold, design: .rounded))
                    Text("Tailnet LLM Gateway")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }

            HStack(spacing: 5) {
                ForEach(AppSection.allCases) { item in
                    Button {
                        section = item
                    } label: {
                        Label(item.rawValue, systemImage: item.symbol)
                            .font(.system(size: 13, weight: section == item ? .semibold : .medium))
                            .padding(.horizontal, 15)
                            .padding(.vertical, 8)
                            .background(section == item ? Color.white.opacity(0.095) : .clear, in: RoundedRectangle(cornerRadius: 9, style: .continuous))
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(4)
            .background(Color.black.opacity(0.16), in: RoundedRectangle(cornerRadius: 12, style: .continuous))

            Spacer()

            StatusPill(
                title: model.relayReady ? "中转站在线" : "需要检查",
                active: model.relayReady,
                symbol: model.relayReady ? "checkmark.circle.fill" : "exclamationmark.circle.fill"
            )
            Button {
                model.refreshAll()
            } label: {
                Image(systemName: "arrow.clockwise")
                    .frame(width: 30, height: 30)
            }
            .buttonStyle(.plain)
            .background(Color.white.opacity(0.07), in: RoundedRectangle(cornerRadius: 9))
            .disabled(model.isRefreshing)
            .rotationEffect(.degrees(model.isRefreshing ? 360 : 0))
            .animation(model.isRefreshing ? .linear(duration: 1).repeatForever(autoreverses: false) : .default, value: model.isRefreshing)
        }
        .padding(.horizontal, 20)
        .padding(.top, 12)
        .padding(.bottom, 10)
        .background(.ultraThinMaterial)
        .overlay(alignment: .bottom) { Divider().opacity(0.45) }
    }
}

private struct RelayOverviewCard: View {
    @EnvironmentObject private var model: AppModel

    private var ready: Bool { model.relayReady && model.currentRelay != nil }
    private var currentName: String {
        shortDeviceName(model.currentRelay?.nodeName ?? model.relayNodeName)
    }

    var body: some View {
        HStack(spacing: 20) {
            ZStack {
                RoundedRectangle(cornerRadius: 18, style: .continuous)
                    .fill(ready ? Color.green.opacity(0.13) : Color.orange.opacity(0.13))
                Image(systemName: ready ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                    .font(.system(size: 28, weight: .semibold))
                    .foregroundStyle(ready ? .green : .orange)
            }
            .frame(width: 62, height: 62)

            VStack(alignment: .leading, spacing: 7) {
                Text(ready ? "PCL 网络已就绪" : "PCL 网络需要检查")
                    .font(.system(size: 22, weight: .semibold, design: .rounded))
                Text("当前中转站：\(currentName)")
                    .font(.subheadline.weight(.medium))
                Text("官方 GPT 保持不变；PCL 模型通过 Tailnet 中转或服务器本机直连接入。")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Spacer()

            HStack(spacing: 18) {
                OverviewValue(value: "\(model.tailnetNodes.filter(\.online).count)", label: "在线设备")
                Divider().frame(height: 32)
                OverviewValue(value: "\(model.relayDiscovery?.readyCount ?? 0)", label: "已接入")
                Divider().frame(height: 32)
                OverviewValue(value: "\(model.currentRelay?.modelCount ?? 0)", label: "模型")
            }
        }
        .padding(20)
        .background(Color(nsColor: .controlBackgroundColor).opacity(0.74), in: RoundedRectangle(cornerRadius: 20, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 20).stroke(Color.white.opacity(0.08)))
    }
}

private struct OverviewValue: View {
    let value: String
    let label: String
    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(value).font(.title2.weight(.semibold).monospacedDigit())
            Text(label).font(.caption).foregroundStyle(.secondary)
        }
        .frame(minWidth: 58, alignment: .leading)
    }
}

private struct UnifiedDeviceRow: View {
    @EnvironmentObject private var model: AppModel
    let node: RelayCandidate

    private var test: DeviceConnectivityTest? { model.deviceTests[node.tailscaleIP] }
    private var status: RemoteClientStatus? { node.clientStatus }
    private var refreshing: Bool { model.refreshingDeviceIDs.contains(node.tailscaleIP) }
    private var testing: Bool { model.testingDeviceIDs.contains(node.tailscaleIP) }
    private var available: Bool { test?.status == "ready" || (test == nil && node.online) }
    private var role: String {
        if node.selected { return "当前中转站" }
        if node.isSelf { return "当前 Mac" }
        if node.feasibility?.localPCLDirectActive == true { return "PCL 本机直连" }
        if node.feasibility?.relayCapable == true { return "可作为中转站" }
        if !node.online { return "离线" }
        return routeDisplayName(node.feasibility?.recommendedRoute ?? "unavailable")
    }
    private var summary: String {
        if let test { return test.summary }
        if !node.online {
            return node.nodeName.contains("bupt")
                ? "Tailscale 已离线；先通过北邮 VPN 应急入口登录并恢复 Tailscale"
                : "Tailnet 中未在线，无法从当前 Mac 访问"
        }
        if status?.updateAvailable == true {
            return "PCL Relay \(status?.clientVersion ?? "旧版") → \(status?.expectedClientVersion ?? "最新版")；可一键同步客户端与原生角色"
        }
        if status?.clientInstalled == true && status?.nativeRoles == false {
            return "检测到旧式委派配置；需要升级为 Codex 原生 custom roles"
        }
        if node.isSelf { return "本地 Codex 桌面版与 VS Code 使用当前中转站" }
        if node.feasibility?.localPCLDirectActive == true { return "服务器通过 127.0.0.1 本地适配器直接访问 PCL API" }
        return node.feasibility?.recommendationReason ?? "尚未完成连通性检查"
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 14) {
                Image(systemName: deviceSymbol(node))
                    .font(.system(size: 19, weight: .medium))
                    .foregroundStyle(available ? roleColor : .secondary)
                    .frame(width: 40, height: 40)
                    .background((available ? roleColor : Color.secondary).opacity(0.10), in: RoundedRectangle(cornerRadius: 11))

                VStack(alignment: .leading, spacing: 4) {
                    HStack(spacing: 8) {
                        Text(shortDeviceName(node.nodeName)).font(.headline)
                        Text(role)
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(roleColor)
                            .padding(.horizontal, 7).padding(.vertical, 3)
                            .background(roleColor.opacity(0.11), in: Capsule())
                    }
                    Text(summary)
                        .font(.caption)
                        .foregroundStyle(available ? Color.secondary : Color.orange)
                        .lineLimit(2)
                    Text("\(node.tailscaleIP)\(node.sshTarget.map { "  ·  SSH \($0)" } ?? "")")
                        .font(.system(.caption2, design: .monospaced))
                        .foregroundStyle(.tertiary)
                }

                Spacer(minLength: 12)

                if let latency = test?.latencyMS ?? node.latencyMS, available {
                    Text("\(latency) ms")
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(.secondary)
                }

                Button { model.refreshDevice(node) } label: {
                    Label(refreshing ? "刷新中" : "刷新", systemImage: "arrow.clockwise")
                }
                .buttonStyle(QuietButtonStyle())
                .disabled(refreshing || testing)

                Button { model.testDeviceConnectivity(node) } label: {
                    Label(testing ? "测试中" : "测试连通性", systemImage: "stethoscope")
                }
                .buttonStyle(QuietButtonStyle())
                .disabled(refreshing || testing)

                if node.feasibility?.relayCapable == true && node.gateway && node.pclAuth == "valid" && !node.selected {
                    Button("设为中转站") { model.selectRelay(node) }
                        .buttonStyle(SecondaryButtonStyle())
                        .disabled(model.isSelectingRelay)
                } else if !node.isSelf && node.online && node.clientStatus?.ssh == true && node.feasibility?.recommendedRoute != "unavailable" {
                    Button(remoteActionTitle) {
                        if status?.updateAvailable == true {
                            model.updateRemoteClient(node)
                        } else {
                            model.configureNode(node)
                        }
                    }
                    .buttonStyle(SecondaryButtonStyle())
                    .disabled(model.installingClientTarget != nil)
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 14)

            if let test {
                Divider().opacity(0.45).padding(.leading, 70)
                HStack(spacing: 16) {
                    ForEach(test.checks) { check in
                        HStack(spacing: 5) {
                            Image(systemName: check.passed ? "checkmark.circle.fill" : "xmark.circle.fill")
                            Text(check.name)
                            Text(check.detail).foregroundStyle(.secondary).lineLimit(1)
                        }
                        .font(.caption2)
                        .foregroundStyle(check.passed ? .green : .orange)
                    }
                    Spacer()
                    Text("刚刚检查").font(.caption2).foregroundStyle(.tertiary)
                }
                .padding(.leading, 70).padding(.trailing, 16).padding(.vertical, 9)
            }
        }
        .background(Color(nsColor: .controlBackgroundColor).opacity(0.62), in: RoundedRectangle(cornerRadius: 15, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 15).stroke(node.selected ? Color.green.opacity(0.32) : Color.white.opacity(0.06)))
    }

    private var roleColor: Color {
        if !node.online { return .orange }
        if node.selected { return .green }
        if node.feasibility?.localPCLDirectActive == true { return .cyan }
        return .blue
    }

    private var remoteActionTitle: String {
        if model.installingClientTarget == node.sshTarget { return "正在更新" }
        if status?.updateAvailable == true { return "升级到 \(status?.expectedClientVersion ?? "最新版")" }
        if status?.ready == true || node.feasibility?.localPCLDirectActive == true { return "重新配置" }
        return "接入"
    }
}

private struct ServerControlCard: View {
    @EnvironmentObject private var model: AppModel
    @Binding var showLogs: Bool

    var body: some View {
        GlassCard {
            VStack(alignment: .leading, spacing: 14) {
                HStack(alignment: .top, spacing: 14) {
                    Image(systemName: "server.rack")
                        .font(.system(size: 23, weight: .medium))
                        .foregroundStyle(.green)
                        .frame(width: 48, height: 48)
                        .background(Color.green.opacity(0.12), in: RoundedRectangle(cornerRadius: 12))
                    VStack(alignment: .leading, spacing: 4) {
                        HStack(spacing: 8) {
                            Text("当前中转站设备").font(.headline)
                            StatusPill(title: model.doctor?.gateway == true ? "API 在线" : "API 离线", active: model.doctor?.gateway == true, symbol: "circle.fill")
                            StatusPill(title: model.remoteServiceActive ? "Tailnet 可运维" : "运维不可达", active: model.remoteServiceActive, symbol: "wrench.and.screwdriver.fill")
                        }
                        Text(model.serverStatus?.nodeName ?? model.relayNodeName).font(.title3.weight(.semibold)).textSelection(.enabled)
                        Text("MagicDNS  \(model.serverStatus?.magicDNS ?? model.relayMagicDNS)   ·   Tailscale IPv4  \(model.serverStatus?.tailscaleIP ?? model.relayTailscaleIP)")
                            .font(.system(.caption, design: .monospaced))
                            .foregroundStyle(.secondary)
                            .textSelection(.enabled)
                    }
                    Spacer()
                    VStack(alignment: .trailing, spacing: 3) {
                        Text("运维范围").font(.caption).foregroundStyle(.secondary)
                        Text("仅 pcl-codex-gateway 用户服务")
                            .font(.caption.weight(.medium)).foregroundStyle(.green)
                    }
                }

                HStack(spacing: 10) {
                    Button { model.refreshServerStatus() } label: {
                        Label("检查服务器", systemImage: "stethoscope")
                    }
                    .buttonStyle(SecondaryButtonStyle())

                    Button { model.restartGateway() } label: {
                        Label(model.isRestartingGateway ? "正在重启" : "重启网关服务", systemImage: "arrow.triangle.2.circlepath")
                    }
                    .buttonStyle(SecondaryButtonStyle())
                    .disabled(model.isRestartingGateway || !model.remoteServiceActive)

                    Button {
                        showLogs.toggle()
                        if showLogs { model.loadGatewayLogs() }
                    } label: {
                        Label(showLogs ? "收起服务日志" : "查看服务日志", systemImage: "doc.text.magnifyingglass")
                    }
                    .buttonStyle(SecondaryButtonStyle())
                    .disabled(!model.remoteServiceActive)

                    Spacer()
                    Text("Tailnet Admin API · 无任意命令执行")
                        .font(.system(.caption, design: .monospaced))
                        .foregroundStyle(.secondary)
                }

                if !model.remoteStatusText.isEmpty {
                    Text(model.remoteStatusText)
                        .font(.system(.caption2, design: .monospaced))
                        .foregroundStyle(.secondary)
                        .lineLimit(4)
                        .textSelection(.enabled)
                }
            }
        }
    }
}

private struct PortalView: View {
    @EnvironmentObject private var model: AppModel

    private var available: Bool { model.portalStatus?.available == true }
    private var relayName: String {
        shortDeviceName(model.currentRelay?.nodeName ?? model.relayNodeName)
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                HStack(spacing: 20) {
                    ZStack {
                        RoundedRectangle(cornerRadius: 18, style: .continuous)
                            .fill((available ? Color.green : Color.blue).opacity(0.13))
                        Image(systemName: available ? "globe.badge.chevron.backward" : "network.badge.shield.half.filled")
                            .font(.system(size: 28, weight: .semibold))
                            .foregroundStyle(available ? .green : .blue)
                    }
                    .frame(width: 62, height: 62)

                    VStack(alignment: .leading, spacing: 7) {
                        Text("PCL 内网页面")
                            .font(.system(size: 22, weight: .semibold, design: .rounded))
                        Text(available ? "已验证可通过当前中转站访问" : "通过 Tailnet 中转打开 PCL API 广场")
                            .font(.subheadline.weight(.medium))
                        Text("登录、查看用量和管理 API Key 都在 PCL 官方网页中完成。")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }

                    Spacer()

                    VStack(alignment: .trailing, spacing: 8) {
                        StatusPill(
                            title: available ? "门户可用" : "尚未验证",
                            active: available,
                            symbol: available ? "checkmark.circle.fill" : "questionmark.circle.fill"
                        )
                        if let latency = model.portalStatus?.latencyMS {
                            Text("\(latency) ms")
                                .font(.caption.monospacedDigit())
                                .foregroundStyle(.secondary)
                        }
                    }
                }
                .padding(20)
                .background(Color(nsColor: .controlBackgroundColor).opacity(0.74), in: RoundedRectangle(cornerRadius: 20, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 20).stroke(Color.white.opacity(0.08)))

                SectionHeader(
                    title: "打开页面",
                    subtitle: "浏览器使用独立登录资料；只有 PCL 域名经过中转站",
                    actionTitle: model.isCheckingPortal ? "正在检测" : "测试访问",
                    actionSymbol: "arrow.clockwise",
                    action: model.refreshPortal
                )

                HStack(spacing: 12) {
                    PortalDestinationCard(
                        title: "API 广场",
                        detail: "查看模型、服务状态和调用入口",
                        symbol: "square.grid.2x2.fill",
                        tint: .blue,
                        actionTitle: "打开广场",
                        disabled: model.isOpeningPortal,
                        action: { model.openPortal(path: "/") }
                    )
                    PortalDestinationCard(
                        title: "用量与钱包",
                        detail: "查看余额、额度和历史用量",
                        symbol: "chart.bar.xaxis",
                        tint: .green,
                        actionTitle: "查看用量",
                        disabled: model.isOpeningPortal,
                        action: { model.openPortal(path: "/wallet") }
                    )
                    PortalDestinationCard(
                        title: "API Key",
                        detail: "创建、撤销或更换访问密钥",
                        symbol: "key.fill",
                        tint: .orange,
                        actionTitle: "管理 Key",
                        disabled: model.isOpeningPortal,
                        action: { model.openPortal(path: "/keys") }
                    )
                }

                VStack(alignment: .leading, spacing: 14) {
                    HStack {
                        Label("当前访问路径", systemImage: "point.3.connected.trianglepath.dotted")
                            .font(.headline)
                        Spacer()
                        Text("不修改系统代理")
                            .font(.caption.weight(.medium))
                            .foregroundStyle(.green)
                    }

                    HStack(spacing: 10) {
                        PortalRouteNode(symbol: "laptopcomputer", title: "专用浏览器", detail: "当前 Mac")
                        FlowArrow()
                        PortalRouteNode(symbol: "lock.shield.fill", title: "Tailscale", detail: "个人 Tailnet")
                        FlowArrow()
                        PortalRouteNode(symbol: "server.rack", title: relayName, detail: "受限转发")
                        FlowArrow()
                        PortalRouteNode(symbol: "globe.asia.australia.fill", title: "PCL 门户", detail: "llmapi.pcl.ac.cn")
                    }

                    Divider().opacity(0.5)

                    VStack(alignment: .leading, spacing: 7) {
                        Label("使用独立浏览器资料保存 PCL 登录状态，不读取你的日常浏览器 Cookie。", systemImage: "person.crop.circle.badge.checkmark")
                        Label("中转站只允许 pcl.ac.cn 域名，不能用于访问其他网站。", systemImage: "checkmark.shield.fill")
                        Label("应用不会显示、下载或记录现有 API Key。", systemImage: "eye.slash.fill")
                    }
                    .font(.caption)
                    .foregroundStyle(.secondary)
                }
                .padding(18)
                .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 16).stroke(Color.white.opacity(0.075)))

                if let error = model.portalStatus?.error, !error.isEmpty {
                    Label(error, systemImage: "exclamationmark.triangle.fill")
                        .font(.caption)
                        .foregroundStyle(.orange)
                        .padding(12)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Color.orange.opacity(0.08), in: RoundedRectangle(cornerRadius: 10))
                }
            }
            .padding(22)
        }
    }
}

private struct PortalDestinationCard: View {
    let title: String
    let detail: String
    let symbol: String
    let tint: Color
    let actionTitle: String
    let disabled: Bool
    let action: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Image(systemName: symbol)
                .font(.system(size: 21, weight: .semibold))
                .foregroundStyle(tint)
                .frame(width: 42, height: 42)
                .background(tint.opacity(0.12), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
            VStack(alignment: .leading, spacing: 4) {
                Text(title).font(.headline)
                Text(detail).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            Button(actionTitle, action: action)
                .buttonStyle(SecondaryButtonStyle())
                .disabled(disabled)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16)
        .background(Color(nsColor: .controlBackgroundColor).opacity(0.58), in: RoundedRectangle(cornerRadius: 15, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 15).stroke(Color.white.opacity(0.075)))
    }
}

private struct PortalRouteNode: View {
    let symbol: String
    let title: String
    let detail: String

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: symbol).foregroundStyle(.blue)
            VStack(alignment: .leading, spacing: 1) {
                Text(title).font(.caption.weight(.semibold))
                Text(detail).font(.caption2).foregroundStyle(.secondary)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(10)
        .background(Color.white.opacity(0.045), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
    }
}

private struct ModelsAgentsView: View {
    @EnvironmentObject private var model: AppModel
    @State private var selectedAgent = "pcl_deepseek_pro"
    @State private var selectedModel: DiscoveredModel?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                AgentFlowCard()

                SectionHeader(
                    title: "PCL 模型目录",
                    subtitle: "\(model.readyAgentCount) 个执行可用 · \(model.partialAgentCount) 个部分兼容；点开模型可查看能力详情",
                    actionTitle: model.isDiscovering ? "正在检查" : "检查更新",
                    actionSymbol: "arrow.triangle.2.circlepath",
                    action: model.discoverModels
                )

                LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 12) {
                    ForEach(model.allDiscoveredModels) { discovered in
                        RelayModelCard(model: discovered, status: model.registry?.models[discovered.alias])
                            .contentShape(Rectangle())
                            .onTapGesture { selectedModel = discovered }
                    }
                }

                HStack(spacing: 12) {
                    Button {
                        model.isDetecting ? model.cancelDetection() : model.detectModels()
                    } label: {
                        Label(model.isDetecting ? "停止检测" : "检测已选模型", systemImage: model.isDetecting ? "stop.fill" : "waveform.path.ecg")
                    }
                    .buttonStyle(PrimaryButtonStyle())

                    Spacer()
                    if let checked = model.registry?.catalogCheckedAt {
                        Text("目录更新：\(checked)")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }

                if model.isDetecting {
                    ConsolePanel(text: model.commandLog, title: "模型能力检测")
                }

                Divider().opacity(0.45)

                HStack(alignment: .top, spacing: 16) {
                    VStack(alignment: .leading, spacing: 12) {
                        SectionHeader(title: "启用为 Codex 子 Agent", subtitle: "同一份模型状态直接用于角色启用，不再在另一页重复选择")
                        ForEach(model.agentOptions) { agent in
                            AgentToggleCard(
                                agent: agent,
                                status: model.registry?.models[agent.id],
                                enabled: model.selectedAgents.contains(agent.id),
                                selected: selectedAgent == agent.id,
                                onSelect: { selectedAgent = agent.id },
                                onToggle: { model.setAgent(agent.id, enabled: $0) }
                            )
                        }
                    }
                    .frame(maxWidth: 430)

                    NativeAgentUsageCard(selectedAgent: selectedAgent)
                }
            }
            .padding(22)
        }
        .sheet(item: $selectedModel) { item in
            ModelDetailSheet(model: item, status: model.registry?.models[item.alias], checkedAt: model.registry?.checkedAt)
        }
        .onChange(of: model.selectedAgents) { _, agents in
            if !agents.contains(selectedAgent), let fallback = model.agentOptions.first(where: { agents.contains($0.id) }) {
                selectedAgent = fallback.id
            }
        }
        .onChange(of: model.agentOptions) { _, options in
            if !options.contains(where: { $0.id == selectedAgent }), let fallback = options.first(where: { model.selectedAgents.contains($0.id) }) {
                selectedAgent = fallback.id
            }
        }
    }
}

private struct AgentFlowCard: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        GlassCard {
            HStack(spacing: 14) {
                FlowNode(symbol: "sparkles", title: "官方 GPT", detail: "登录与模型选择保留", color: .green)
                FlowArrow()
                FlowNode(symbol: "arrow.triangle.branch", title: "PCL Relay 路由", detail: "一个 App 内置", color: .blue)
                FlowArrow()
                FlowNode(symbol: "person.3.sequence.fill", title: "原生子 Agent", detail: "过程显示在 Codex", color: .purple)
                Spacer()
                VStack(alignment: .trailing, spacing: 8) {
                    StatusPill(title: model.codexIntegrationReady ? "原生角色已就绪" : "需要安装", active: model.codexIntegrationReady, symbol: "arrow.triangle.branch")
                    Button(model.codexIntegrationReady ? "修复注册" : "安装到 Codex") {
                        model.installCodexIntegration()
                    }
                    .buttonStyle(SecondaryButtonStyle())
                }
            }
        }
    }
}

private struct NativeAgentUsageCard: View {
    @EnvironmentObject private var model: AppModel
    let selectedAgent: String

    private var selected: AgentDefinition? {
        model.agentOptions.first { $0.id == selectedAgent }
    }

    private var nativeRoleName: String {
        selected?.nativeRoleName ?? selectedAgent.replacingOccurrences(of: "_", with: "-")
    }

    var body: some View {
        GlassCard {
            VStack(alignment: .leading, spacing: 15) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("在 Codex 中直接使用").font(.headline)
                    Text("不再启动外部 codex exec，也不需要手动填写工作区。子 Agent 自动继承主任务的当前工作区和权限。")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                HStack(spacing: 10) {
                    Image(systemName: selected?.symbol ?? "person.3.fill")
                        .foregroundStyle(selected?.tint ?? .purple)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(selected?.title ?? selectedAgent).font(.subheadline.weight(.semibold))
                        Text("原生模型 ID：pcl/\(selected?.model ?? "")")
                            .font(.caption.monospaced())
                            .foregroundStyle(.secondary)
                    }
                }

                VStack(alignment: .leading, spacing: 8) {
                    Text("示例").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                    NativePromptExample(text: "让 \(nativeRoleName) 实现这个功能，并运行测试；完成后由你复核。")
                    NativePromptExample(text: "启动多个 \(nativeRoleName) 子 Agent，并行处理边界清晰的子任务。")
                }

                Divider().opacity(0.35)
                Label("运行时会像 Codex 自带子 Agent 一样显示创建、进度和结果。", systemImage: "eye.fill")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .frame(maxWidth: .infinity)
    }
}

private struct NativePromptExample: View {
    let text: String

    var body: some View {
        Text(text)
            .font(.system(.caption, design: .monospaced))
            .textSelection(.enabled)
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.black.opacity(0.16), in: RoundedRectangle(cornerRadius: 9))
    }
}

private struct RelayModelCard: View {
    let model: DiscoveredModel
    let status: RelayModelStatus?

    private var agent: AgentDefinition { AgentDefinition(model: model) }

    var body: some View {
        GlassCard {
            HStack(alignment: .top, spacing: 13) {
                Image(systemName: agent.symbol)
                    .font(.system(size: 20, weight: .medium))
                    .foregroundStyle(agent.tint)
                    .frame(width: 42, height: 42)
                    .background(agent.tint.opacity(0.12), in: RoundedRectangle(cornerRadius: 10))
                VStack(alignment: .leading, spacing: 6) {
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(agent.title).font(.headline)
                            Text("\(model.family) · \(categoryName)").font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        if model.agentEligible {
                            AvailabilityPill(status: status)
                        } else {
                            Text("非 Agent")
                                .font(.caption2.weight(.semibold))
                                .foregroundStyle(.secondary)
                                .padding(.horizontal, 8).padding(.vertical, 4)
                                .background(Color.white.opacity(0.06), in: Capsule())
                        }
                    }
                    Text(model.description)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                    if model.agentEligible {
                        HStack(spacing: 6) {
                            MiniBadge("对话", active: status?.chat == true)
                            MiniBadge("流式", active: status?.stream == true)
                            MiniBadge("工具", active: status?.toolCompatible == true)
                            if model.recommended { Text("推荐").font(.caption2).foregroundStyle(.green) }
                            if let mode = status?.toolCallMode, mode != "unavailable" {
                                Text(mode == "native" ? "原生工具" : "兼容工具")
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                            }
                        }
                    } else {
                        Text("不适用于文本执行 Agent")
                            .font(.caption2.weight(.medium))
                            .foregroundStyle(.secondary)
                    }
                    if let error = status?.error, !error.isEmpty {
                        Text(error).font(.caption2).foregroundStyle(.orange).lineLimit(2)
                    }
                }
            }
        }
    }

    private var categoryName: String {
        switch model.category {
        case "chat": return "文本生成"
        case "embedding": return "向量"
        case "reranker": return "重排序"
        case "speech": return "语音"
        case "vision-ocr": return "视觉 OCR"
        case "image": return "图像"
        default: return model.category
        }
    }
}

private struct AvailabilityPill: View {
    let status: RelayModelStatus?

    private var value: (String, Color, String) {
        guard let status else { return ("未检测", .secondary, "questionmark.circle.fill") }
        if status.executionReady { return ("可用", .green, "checkmark.circle.fill") }
        if status.chat || status.stream == true || status.toolCompatible == true {
            return ("部分兼容", .orange, "exclamationmark.circle.fill")
        }
        return ("不可用", .red, "xmark.circle.fill")
    }

    var body: some View {
        Label(value.0, systemImage: value.2)
            .font(.caption2.weight(.semibold))
            .foregroundStyle(value.1)
            .padding(.horizontal, 8).padding(.vertical, 4)
            .background(value.1.opacity(0.11), in: Capsule())
    }
}

private struct ModelDetailSheet: View {
    @Environment(\.dismiss) private var dismiss
    let model: DiscoveredModel
    let status: RelayModelStatus?
    let checkedAt: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack(spacing: 13) {
                Image(systemName: AgentDefinition(model: model).symbol)
                    .font(.system(size: 24, weight: .medium))
                    .foregroundStyle(AgentDefinition(model: model).tint)
                    .frame(width: 52, height: 52)
                    .background(AgentDefinition(model: model).tint.opacity(0.13), in: RoundedRectangle(cornerRadius: 13))
                VStack(alignment: .leading, spacing: 3) {
                    Text(model.id).font(.title2.weight(.semibold))
                    Text(model.description).foregroundStyle(.secondary)
                }
                Spacer()
            }

            GlassCard {
                VStack(spacing: 10) {
                    DetailRow(label: "Agent 别名", value: model.alias)
                    DetailRow(label: "模型家族", value: model.family)
                    DetailRow(label: "模型类型", value: categoryName)
                    DetailRow(label: "输入模态", value: model.inputModalities.joined(separator: "、"))
                    DetailRow(label: "Codex 子 Agent", value: model.agentEligible ? "可以选择" : "不适用")
                    if model.agentEligible {
                        DetailRow(label: "可用性", value: availabilityText)
                        DetailRow(label: "最近检测", value: checkedAt ?? "尚未检测")
                    }
                    DetailRow(label: "网关标记", value: model.ownedBy)
                }
            }

            if model.agentEligible {
                HStack(spacing: 8) {
                    MiniBadge("普通对话", active: status?.chat == true)
                    MiniBadge("SSE 流式", active: status?.stream == true)
                    MiniBadge("工具调用", active: status?.toolCompatible == true)
                    if let mode = status?.toolCallMode, mode != "unavailable" {
                        Text(mode == "native" ? "原生 function calling" : "JSON 兼容层")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
            }


            if let error = status?.error, !error.isEmpty {
                Text("检测详情：\(error)")
                    .font(.caption)
                    .foregroundStyle(.orange)
                    .textSelection(.enabled)
            }

            Text("PCL 的 /v1/models 当前未返回上下文窗口、速率限制或计费信息，因此应用不会猜测这些字段。")
                .font(.caption)
                .foregroundStyle(.secondary)

            HStack {
                Spacer()
                Button("完成") { dismiss() }
                    .keyboardShortcut(.defaultAction)
                    .buttonStyle(PrimaryButtonStyle())
            }
        }
        .padding(24)
        .frame(width: 620)
        .preferredColorScheme(.dark)
    }

    private var categoryName: String {
        switch model.category {
        case "chat": return "文本生成"
        case "embedding": return "向量"
        case "reranker": return "重排序"
        case "speech": return "语音识别"
        case "vision-ocr": return "视觉 OCR"
        case "image": return "图像生成/编辑"
        default: return model.category
        }
    }

    private var availabilityText: String {
        guard let status else { return "未检测" }
        if status.executionReady { return "可用" }
        if status.chat || status.stream == true || status.toolCompatible == true { return "部分兼容" }
        return "不可用"
    }
}

private struct DetailRow: View {
    let label: String
    let value: String
    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            Text(label).foregroundStyle(.secondary)
            Spacer()
            Text(value).font(.system(.body, design: label == "Agent 别名" ? .monospaced : .default))
                .textSelection(.enabled)
        }
    }
}

private struct AgentToggleCard: View {
    let agent: AgentDefinition
    let status: RelayModelStatus?
    let enabled: Bool
    let selected: Bool
    let onSelect: () -> Void
    let onToggle: (Bool) -> Void

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: agent.symbol)
                .foregroundStyle(agent.tint)
                .frame(width: 34, height: 34)
                .background(agent.tint.opacity(0.12), in: RoundedRectangle(cornerRadius: 9))
            VStack(alignment: .leading, spacing: 3) {
                HStack {
                    Text(agent.title).font(.subheadline.weight(.semibold))
                    StatusDot(active: status?.executionReady == true)
                }
                Text(agent.detail).font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            Toggle("", isOn: Binding(get: { enabled }, set: onToggle))
                .labelsHidden()
                .toggleStyle(.switch)
        }
        .padding(12)
        .background(selected ? Color.accentColor.opacity(0.12) : Color.white.opacity(0.035), in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(selected ? Color.accentColor.opacity(0.55) : Color.white.opacity(0.07)))
        .contentShape(Rectangle())
        .onTapGesture(perform: onSelect)
    }
}

private struct MetricCard: View {
    let title: String
    let value: String
    let detail: String
    let symbol: String
    let active: Bool

    var body: some View {
        GlassCard {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Image(systemName: symbol).foregroundStyle(active ? .green : .orange)
                    Spacer()
                    StatusDot(active: active)
                }
                Text(value).font(.title3.weight(.semibold))
                Text(title).font(.caption.weight(.medium)).foregroundStyle(.secondary)
                Text(detail).font(.caption2).foregroundStyle(.tertiary).lineLimit(1)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

private struct FlowNode: View {
    let symbol: String
    let title: String
    let detail: String
    let color: Color
    var body: some View {
        HStack(spacing: 9) {
            Image(systemName: symbol).foregroundStyle(color)
            VStack(alignment: .leading, spacing: 1) {
                Text(title).font(.subheadline.weight(.semibold))
                Text(detail).font(.caption2).foregroundStyle(.secondary)
            }
        }
    }
}

private struct FlowArrow: View {
    var body: some View {
        Image(systemName: "chevron.right")
            .font(.caption.bold())
            .foregroundStyle(.tertiary)
    }
}

private struct SectionHeader: View {
    let title: String
    let subtitle: String
    var actionTitle: String? = nil
    var actionSymbol: String = "arrow.clockwise"
    var action: (() -> Void)? = nil

    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: 3) {
                Text(title).font(.title3.weight(.semibold))
                Text(subtitle).font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            if let actionTitle, let action {
                Button(action: action) { Label(actionTitle, systemImage: actionSymbol) }
                    .buttonStyle(SecondaryButtonStyle())
            }
        }
    }
}

private struct GlassCard<Content: View>: View {
    @ViewBuilder let content: () -> Content
    var body: some View {
        content()
            .padding(16)
            .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 15, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 15, style: .continuous).stroke(Color.white.opacity(0.075)))
            .shadow(color: .black.opacity(0.12), radius: 14, y: 7)
    }
}

private struct ConsolePanel: View {
    let text: String
    let title: String
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(.caption.weight(.semibold)).foregroundStyle(.secondary)
            ScrollView([.horizontal, .vertical]) {
                Text(text.isEmpty ? "暂无输出" : text)
                    .font(.system(size: 11.5, design: .monospaced))
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .topLeading)
            }
            .frame(minHeight: 130, maxHeight: 280)
        }
        .padding(14)
        .background(Color.black.opacity(0.3), in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Color.white.opacity(0.07)))
    }
}

private struct StatusPill: View {
    let title: String
    let active: Bool
    let symbol: String
    var body: some View {
        Label(title, systemImage: symbol)
            .font(.caption.weight(.semibold))
            .foregroundStyle(active ? .green : .orange)
            .padding(.horizontal, 9)
            .padding(.vertical, 5)
            .background((active ? Color.green : Color.orange).opacity(0.11), in: Capsule())
    }
}

private struct StatusDot: View {
    let active: Bool
    var body: some View {
        Circle().fill(active ? Color.green : Color.gray.opacity(0.45)).frame(width: 7, height: 7)
    }
}

private struct MiniBadge: View {
    let title: String
    let active: Bool
    init(_ title: String, active: Bool) { self.title = title; self.active = active }
    var body: some View {
        HStack(spacing: 3) {
            Image(systemName: active ? "checkmark" : "minus")
            Text(title)
        }
        .font(.caption2.weight(.medium))
        .foregroundStyle(active ? .green : .secondary)
        .padding(.horizontal, 6)
        .padding(.vertical, 3)
        .background(Color.white.opacity(0.05), in: Capsule())
    }
}

private struct BannerView: View {
    let message: AppModel.BannerMessage
    var color: Color {
        switch message.kind {
        case .success: .green
        case .error: .red
        case .info: .blue
        }
    }
    var symbol: String {
        switch message.kind {
        case .success: "checkmark.circle.fill"
        case .error: "xmark.circle.fill"
        case .info: "info.circle.fill"
        }
    }
    var body: some View {
        Label(message.text, systemImage: symbol)
            .font(.subheadline.weight(.medium))
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
            .background(.ultraThickMaterial, in: Capsule())
            .overlay(Capsule().stroke(color.opacity(0.45)))
            .foregroundStyle(color)
            .shadow(color: .black.opacity(0.25), radius: 18, y: 8)
    }
}

private struct PrimaryButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.subheadline.weight(.semibold))
            .foregroundStyle(.white)
            .padding(.horizontal, 14)
            .padding(.vertical, 9)
            .background(Color.accentColor.opacity(configuration.isPressed ? 0.72 : 1), in: RoundedRectangle(cornerRadius: 9))
            .scaleEffect(configuration.isPressed ? 0.98 : 1)
    }
}

private struct SecondaryButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.subheadline.weight(.medium))
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .background(Color.white.opacity(configuration.isPressed ? 0.12 : 0.07), in: RoundedRectangle(cornerRadius: 9))
            .overlay(RoundedRectangle(cornerRadius: 9).stroke(Color.white.opacity(0.07)))
            .scaleEffect(configuration.isPressed ? 0.98 : 1)
    }
}

private struct QuietButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.caption.weight(.medium))
            .foregroundStyle(.secondary)
            .padding(.horizontal, 9)
            .padding(.vertical, 7)
            .background(Color.white.opacity(configuration.isPressed ? 0.09 : 0.035), in: RoundedRectangle(cornerRadius: 8))
            .scaleEffect(configuration.isPressed ? 0.97 : 1)
            .animation(.easeOut(duration: 0.12), value: configuration.isPressed)
    }
}

private func shortDeviceName(_ value: String) -> String {
    value.replacingOccurrences(of: "haichen-", with: "")
}

private func routeDisplayName(_ route: String) -> String {
    switch route {
    case "direct": return "中转站直连"
    case "local_pcl_direct": return "PCL 本机直连"
    case "bridge_via_local_mac": return "经 Mac 桥接"
    default: return "尚未接入"
    }
}

private func deviceSymbol(_ node: RelayCandidate) -> String {
    if node.isSelf || node.clientStatus?.system == "Darwin" { return "laptopcomputer" }
    if node.selected || node.feasibility?.relayCapable == true { return "server.rack" }
    return "shippingbox.fill"
}
