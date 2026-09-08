import BridgeCore
import SwiftUI

struct RelayTopologyCanvas: View {
    @EnvironmentObject private var model: AppModel

    private var gateways: [GatewayRouteRecord] { model.gatewayRoutes?.gateways ?? [] }
    private var peers: [RelaySyncPeer] { model.relaySync?.peers ?? [] }
    private var deploymentTargets: [DeploymentTarget] { model.deploymentTargets?.targets ?? [] }

    private var contentWidth: CGFloat {
        max(980, 760 + CGFloat(max(gateways.count, max(peers.count, deploymentTargets.count))) * 190)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 18) {
                TopologyLegend(color: .green, title: "官方数据流", dashed: false)
                TopologyLegend(color: .purple, title: "PCL 数据流", dashed: false)
                TopologyLegend(color: .blue, title: "同步/部署控制流", dashed: true)
                Spacer()
                Text("点击 gateway 节点可检查或切换")
                    .font(.caption2).foregroundStyle(.secondary)
            }
            ScrollView(.horizontal, showsIndicators: true) {
                GeometryReader { geometry in
                    let points = layout(size: geometry.size)
                    ZStack {
                        Canvas { context, _ in
                            drawEdge(context: &context, from: points["codex"]!, to: points["opencodex"]!, color: .green)
                            drawEdge(context: &context, from: points["opencodex"]!, to: points["official"]!, color: .green)
                            drawEdge(context: &context, from: points["opencodex"]!, to: points["pcl"]!, color: .purple)
                            drawEdge(context: &context, from: points["relay"]!, to: points["pcl"]!, color: .blue, dashed: true)
                            for route in gateways {
                                if let endpoint = points["gateway:\(route.id)"] {
                                    drawEdge(
                                        context: &context,
                                        from: points["pcl"]!,
                                        to: endpoint,
                                        color: route.selected ? .purple : .gray,
                                        dashed: !route.selected
                                    )
                                }
                            }
                            for peer in peers {
                                if let endpoint = points["peer:\(peer.id)"] {
                                    drawEdge(context: &context, from: points["relay"]!, to: endpoint, color: .blue, dashed: true)
                                }
                            }
                            for target in deploymentTargets {
                                if let endpoint = points["target:\(target.id)"] {
                                    drawEdge(context: &context, from: points["relay"]!, to: endpoint, color: .blue, dashed: true)
                                }
                            }
                        }

                        TopologyNodeCard(symbol: "terminal.fill", title: "Codex", subtitle: "官方客户端", color: .green)
                            .position(points["codex"]!)
                        TopologyNodeCard(symbol: "arrow.triangle.branch", title: "OpenCodex", subtitle: "固定完整上游", color: .blue)
                            .position(points["opencodex"]!)
                        TopologyNodeCard(symbol: "sparkles", title: "official/*", subtitle: "OpenAI 原生通道", color: .green)
                            .position(points["official"]!)
                        TopologyNodeCard(symbol: "point.3.connected.trianglepath.dotted", title: "pcl/*", subtitle: "按模型前缀分发", color: .purple)
                            .position(points["pcl"]!)
                        TopologyNodeCard(
                            symbol: "desktopcomputer",
                            title: model.relaySync?.nodeName ?? "当前 Relay",
                            subtitle: "本机 · r\(model.relaySync?.revision.counter ?? 0)",
                            color: .blue,
                            badge: "CURRENT"
                        )
                        .position(points["relay"]!)

                        ForEach(gateways) { route in
                            Button {
                                model.selectedGatewayID = route.id
                            } label: {
                                TopologyNodeCard(
                                    symbol: "server.rack",
                                    title: route.name,
                                    subtitle: gatewayDetail(route),
                                    color: route.selected ? .purple : (route.healthy == false ? .orange : .gray),
                                    badge: route.selected ? "ACTIVE" : nil
                                )
                            }
                            .buttonStyle(.plain)
                            .position(points["gateway:\(route.id)"]!)
                        }

                        ForEach(peers) { peer in
                            TopologyNodeCard(
                                symbol: "desktopcomputer.and.macbook",
                                title: peer.name,
                                subtitle: peer.online == true ? "\(peer.version ?? "未知版本") · \(peer.latencyMS ?? 0) ms" : "心跳不可达",
                                color: peer.online == true ? .blue : .orange,
                                badge: peer.online == true ? "ONLINE" : "OFFLINE"
                            )
                            .position(points["peer:\(peer.id)"]!)
                        }

                        ForEach(deploymentTargets) { target in
                            TopologyNodeCard(
                                symbol: "shippingbox.and.arrow.forward",
                                title: target.name,
                                subtitle: target.receiverOnline == true ? "已接入 Relay" : (target.ssh == true ? "可首次安装" : "等待可达"),
                                color: target.receiverOnline == true ? .blue : (target.ssh == true ? .purple : .orange),
                                badge: "REGISTERED"
                            )
                            .position(points["target:\(target.id)"]!)
                        }
                    }
                }
                .frame(width: contentWidth, height: 540)
            }
            .background(Color.black.opacity(0.12), in: RoundedRectangle(cornerRadius: 16))
            .overlay(RoundedRectangle(cornerRadius: 16).stroke(Color.white.opacity(0.055)))
        }
    }

    private func layout(size: CGSize) -> [String: CGPoint] {
        var result: [String: CGPoint] = [
            "codex": CGPoint(x: 95, y: 145),
            "opencodex": CGPoint(x: 285, y: 145),
            "official": CGPoint(x: 485, y: 78),
            "pcl": CGPoint(x: 485, y: 220),
            "relay": CGPoint(x: 285, y: 430),
        ]
        for (index, route) in gateways.enumerated() {
            result["gateway:\(route.id)"] = CGPoint(x: 700 + CGFloat(index) * 190, y: 220)
        }
        for (index, peer) in peers.enumerated() {
            result["peer:\(peer.id)"] = CGPoint(x: 500 + CGFloat(index) * 190, y: 370)
        }
        for (index, target) in deploymentTargets.enumerated() {
            result["target:\(target.id)"] = CGPoint(x: 500 + CGFloat(index) * 190, y: 475)
        }
        return result
    }

    private func drawEdge(
        context: inout GraphicsContext,
        from: CGPoint,
        to: CGPoint,
        color: Color,
        dashed: Bool = false
    ) {
        var path = Path()
        path.move(to: CGPoint(x: from.x + 78, y: from.y))
        let end = CGPoint(x: to.x - 78, y: to.y)
        let middle = (from.x + to.x) / 2
        path.addCurve(
            to: end,
            control1: CGPoint(x: middle, y: from.y),
            control2: CGPoint(x: middle, y: to.y)
        )
        context.stroke(
            path,
            with: .color(color.opacity(0.82)),
            style: StrokeStyle(lineWidth: 2.2, lineCap: .round, dash: dashed ? [7, 6] : [])
        )
        var arrow = Path()
        arrow.move(to: end)
        arrow.addLine(to: CGPoint(x: end.x - 8, y: end.y - 5))
        arrow.move(to: end)
        arrow.addLine(to: CGPoint(x: end.x - 8, y: end.y + 5))
        context.stroke(arrow, with: .color(color.opacity(0.82)), lineWidth: 2)
    }

    private func gatewayDetail(_ route: GatewayRouteRecord) -> String {
        if route.healthy == false { return "协议异常" }
        if let count = route.modelCount { return "\(count) 模型 · \(route.latencyMS ?? 0) ms" }
        return route.selected ? "当前 PCL gateway" : "候选 gateway"
    }
}

private struct TopologyNodeCard: View {
    let symbol: String
    let title: String
    let subtitle: String
    let color: Color
    var badge: String? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack(spacing: 7) {
                Image(systemName: symbol).foregroundStyle(color)
                Text(title).font(.caption.weight(.semibold)).lineLimit(1)
                Spacer(minLength: 2)
            }
            Text(subtitle).font(.caption2.monospaced()).foregroundStyle(.secondary).lineLimit(1)
            if let badge {
                Text(badge).font(.system(size: 8, weight: .bold, design: .rounded))
                    .foregroundStyle(color)
                    .padding(.horizontal, 6).padding(.vertical, 2)
                    .background(color.opacity(0.12), in: Capsule())
            }
        }
        .padding(11)
        .frame(width: 158, height: 82, alignment: .leading)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 13))
        .overlay(RoundedRectangle(cornerRadius: 13).stroke(color.opacity(0.48), lineWidth: 1.2))
        .shadow(color: color.opacity(0.08), radius: 8, y: 3)
    }
}

private struct TopologyLegend: View {
    let color: Color
    let title: String
    let dashed: Bool
    var body: some View {
        HStack(spacing: 6) {
            Canvas { context, size in
                var path = Path()
                path.move(to: CGPoint(x: 0, y: size.height / 2))
                path.addLine(to: CGPoint(x: size.width, y: size.height / 2))
                context.stroke(path, with: .color(color), style: StrokeStyle(lineWidth: 2, dash: dashed ? [5, 4] : []))
            }
            .frame(width: 24, height: 8)
            Text(title).font(.caption2).foregroundStyle(.secondary)
        }
    }
}
