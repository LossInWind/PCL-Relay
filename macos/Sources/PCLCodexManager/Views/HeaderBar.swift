import SwiftUI

struct HeaderBar: View {
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
