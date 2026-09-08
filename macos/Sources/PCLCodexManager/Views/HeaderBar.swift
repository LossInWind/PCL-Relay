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
                    Text("Codex Model Router")
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
                            .background(section == item ? Color.accentColor.opacity(0.12) : .clear, in: RoundedRectangle(cornerRadius: 9, style: .continuous))
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(4)
            .background(Color.primary.opacity(0.04), in: RoundedRectangle(cornerRadius: 12, style: .continuous))

            Spacer()

            Toggle(
                "PCL Agent",
                isOn: Binding(
                    get: { model.integrationEnabled },
                    set: { model.setIntegrationEnabled($0) }
                )
            )
            .toggleStyle(.switch)
            .controlSize(.small)
            .disabled(model.isTogglingIntegration)

            StatusPill(
                title: model.routeStatusTitle,
                active: model.routeReady,
                symbol: model.routeReady ? "checkmark.circle.fill" : "exclamationmark.circle.fill"
            ).help("仅表示接入配置与接口状态，不代表模型生成请求已通过实测")
            Button {
                model.refreshAll()
            } label: {
                Image(systemName: "arrow.clockwise")
                    .frame(width: 30, height: 30)
            }
            .buttonStyle(.plain)
            .background(Color.white.opacity(0.07), in: RoundedRectangle(cornerRadius: 9))
            .disabled(model.isRefreshing)
            .help(model.isRefreshing ? "各区域正在检查" : "刷新状态，不修改配置")
            .accessibilityLabel("刷新状态")
        }
        .padding(.horizontal, 20)
        .padding(.top, 12)
        .padding(.bottom, 10)
        .background(.ultraThinMaterial)
        .overlay(alignment: .bottom) { Divider().opacity(0.45) }
    }
}
