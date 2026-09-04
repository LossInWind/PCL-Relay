import BridgeCore
import SwiftUI

struct FlowNode: View {
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

struct FlowArrow: View {
    var body: some View {
        Image(systemName: "chevron.right")
            .font(.caption.bold())
            .foregroundStyle(.tertiary)
    }
}

struct SectionHeader: View {
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

struct GlassCard<Content: View>: View {
    @ViewBuilder let content: () -> Content
    var body: some View {
        content()
            .padding(16)
            .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 15, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 15, style: .continuous).stroke(Color.white.opacity(0.075)))
            .shadow(color: .black.opacity(0.12), radius: 14, y: 7)
    }
}

struct ConsolePanel: View {
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

struct StatusPill: View {
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

struct StatusDot: View {
    let active: Bool
    var body: some View {
        Circle().fill(active ? Color.green : Color.gray.opacity(0.45)).frame(width: 7, height: 7)
    }
}

struct MiniBadge: View {
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

struct BannerView: View {
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

struct PrimaryButtonStyle: ButtonStyle {
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

struct SecondaryButtonStyle: ButtonStyle {
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

struct QuietButtonStyle: ButtonStyle {
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

func shortDeviceName(_ value: String) -> String {
    value.replacingOccurrences(of: "haichen-", with: "")
}

func routeDisplayName(_ route: String) -> String {
    switch route {
    case "direct": return "中转站直连"
    case "local_pcl_direct": return "PCL 本机直连"
    case "bridge_via_local_mac": return "经 Mac 桥接"
    default: return "尚未接入"
    }
}

func deviceSymbol(_ node: RelayCandidate) -> String {
    if node.isSelf || node.clientStatus?.system == "Darwin" { return "laptopcomputer" }
    if node.selected || node.feasibility?.relayCapable == true { return "server.rack" }
    return "shippingbox.fill"
}
