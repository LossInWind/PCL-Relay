import BridgeCore
import SwiftUI

struct TopologyConsensusRipple: View {
    let origin: CGPoint
    let progress: CGFloat
    let reduceMotion: Bool

    var body: some View {
        ZStack {
            ForEach(0..<3, id: \.self) { index in
                Circle()
                    .stroke(Color.green.opacity(max(0, 0.28 - Double(progress) * 0.28)), lineWidth: index == 0 ? 2 : 1)
                    .frame(width: 92, height: 92)
                    .scaleEffect(reduceMotion ? 1.08 : 1 + progress * CGFloat(5 + index * 3))
            }
        }
        .position(origin)
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }
}

struct TopologyGrid: View {
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

struct TopologyConnection: View {
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
        case "local_pcl_direct": return "PCL 直连"
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

struct TopologyDeviceNode: View {
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

struct TopologyInspector: View {
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
