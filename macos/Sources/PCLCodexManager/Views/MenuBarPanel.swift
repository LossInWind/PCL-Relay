import AppKit
import SwiftUI

struct MenuBarPanel: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.openSettings) private var openSettings

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 11) {
                ZStack {
                    RoundedRectangle(cornerRadius: 10, style: .continuous)
                        .fill(Color.accentColor.gradient)
                    Image(systemName: "point.3.connected.trianglepath.dotted")
                        .font(.system(size: 18, weight: .semibold))
                        .foregroundStyle(.white)
                }
                .frame(width: 38, height: 38)

                VStack(alignment: .leading, spacing: 2) {
                    Text("PCL Relay")
                        .font(.headline)
                    Text(model.relayReady ? "中转站在线" : "网络需要检查")
                        .font(.caption)
                        .foregroundStyle(model.relayReady ? Color.green : Color.orange)
                }

                Spacer()

                if model.isRefreshing || model.isDiscoveringNodes {
                    ProgressView()
                        .controlSize(.small)
                } else {
                    Circle()
                        .fill(model.relayReady ? Color.green : Color.orange)
                        .frame(width: 9, height: 9)
                        .shadow(color: (model.relayReady ? Color.green : Color.orange).opacity(0.45), radius: 4)
                }
            }

            VStack(alignment: .leading, spacing: 8) {
                Label("当前中转站", systemImage: "server.rack")
                    .font(.caption.weight(.medium))
                    .foregroundStyle(.secondary)
                Text(shortDeviceName(model.currentRelay?.nodeName ?? model.relayNodeName))
                    .font(.subheadline.weight(.semibold))
                    .lineLimit(1)

                HStack(spacing: 8) {
                    compactMetric(value: "\(model.tailnetNodes.filter(\.online).count)", label: "在线设备")
                    compactMetric(value: "\(model.readyAgentCount)", label: "可用模型")
                    compactMetric(value: model.codexIntegrationReady ? "已接入" : "待检查", label: "Codex")
                }
            }
            .padding(12)
            .background(.quaternary.opacity(0.55), in: RoundedRectangle(cornerRadius: 12, style: .continuous))

            HStack(spacing: 8) {
                Button {
                    model.refreshAll()
                } label: {
                    Label("刷新", systemImage: "arrow.clockwise")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
                .disabled(model.isRefreshing)

                Button {
                    model.isDetecting ? model.cancelDetection() : model.detectModels()
                } label: {
                    Label(model.isDetecting ? "停止检测" : "检测模型", systemImage: model.isDetecting ? "stop.fill" : "waveform.path.ecg")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
            }

            HStack(spacing: 8) {
                Button {
                    model.openPortal(path: "/")
                } label: {
                    Label("PCL 门户", systemImage: "globe.asia.australia.fill")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)

                Button {
                    openFullSettings(.models)
                } label: {
                    Label("Agent 设置", systemImage: "person.3.sequence.fill")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
            }

            Divider()

            HStack(spacing: 7) {
                Image(systemName: model.launchAtLoginEnabled ? "checkmark.circle.fill" : "exclamationmark.circle.fill")
                    .foregroundStyle(model.launchAtLoginEnabled ? Color.green : Color.orange)
                Text(model.launchAtLoginStatusText)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Spacer()
            }

            if model.codexReloadRequired {
                Label("路由端口已变化，请退出并重新打开 Codex", systemImage: "arrow.triangle.2.circlepath")
                    .font(.caption.weight(.medium))
                    .foregroundStyle(.orange)
            }

            Button {
                openFullSettings(.network)
            } label: {
                Label("打开完整设置", systemImage: "macwindow")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)

            HStack {
                if let update = model.releaseUpdate, update.updateAvailable {
                    Button("发现版本 \(update.latestVersion)") {
                        openFullSettings(.network)
                    }
                    .buttonStyle(.link)
                }
                Spacer()
                Button("退出应用") {
                    NSApp.terminate(nil)
                }
                .buttonStyle(.plain)
                .foregroundStyle(.secondary)
            }
            .font(.caption)
        }
        .padding(16)
        .frame(width: 350)
        .background(.regularMaterial)
        .task { model.start() }
    }

    private func compactMetric(value: String, label: String) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(value)
                .font(.subheadline.weight(.semibold))
                .lineLimit(1)
            Text(label)
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func openFullSettings(_ section: AppSection) {
        UserDefaults.standard.set(section.rawValue, forKey: "selectedSection")
        openSettings()
        NSApp.activate(ignoringOtherApps: true)
    }
}
