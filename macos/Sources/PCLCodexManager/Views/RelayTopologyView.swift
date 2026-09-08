import BridgeCore
import SwiftUI

/// No heartbeat or deployment edges: these are not model traffic.
struct RelayTopologyCanvas: View {
    @EnvironmentObject private var model: AppModel
    private var current: GatewayRouteRecord? { model.gatewayRoutes?.gateways.first(where: \.selected) }
    private var verifiedEndpointRoute: GatewayRouteRecord? {
        guard model.checks["routes"]?.phase == .succeeded,
              let configured = model.routingRuntime?.configuredEndpoint, let current,
              RoutingSnapshot.sameEndpoint(configured, current.url) else { return nil }
        return current
    }
    private var inspected: GatewayRouteRecord? {
        model.gatewayRoutes?.gateways.first { $0.id == model.selectedGatewayID } ?? current
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Text("模型请求路径").font(.headline)
                Spacer()
                Text("本机配置 · 调用状态单独验证").font(.caption).foregroundStyle(.secondary)
            }
            ScrollView([.horizontal, .vertical], showsIndicators: true) {
                VStack(alignment: .leading, spacing: 16) {
                    HStack(spacing: 18) {
                        node("当前 Mac", detail: "Codex / OpenCodex", symbol: "laptopcomputer", color: .blue) {
                            model.selectedRoutingDeviceID = model.routingDevices.first?.id
                        }
                        Image(systemName: "arrow.right").foregroundStyle(.secondary)
                        VStack(alignment: .leading, spacing: 16) {
                            HStack {
                                node("官方 GPT", detail: "保留既有官方通道", symbol: "sparkles", color: .secondary) {}
                                Text("连接状态独立验证").font(.caption).foregroundStyle(.secondary)
                            }
                            HStack {
                                node(current?.name ?? "PCL 接入点", detail: model.routingRuntime?.configuredEndpoint ?? current?.url ?? "尚未读取配置",
                                     symbol: "server.rack", color: verifiedEndpointRoute?.healthy == false ? .orange : .purple) {
                                    model.selectedGatewayID = current?.id
                                }
                                VStack(alignment: .leading, spacing: 5) {
                                    Text(verifiedEndpointRoute?.healthy == true ? "接口可达" : verifiedEndpointRoute?.healthy == false ? "接口检查失败" : "此路径状态未确认")
                                    Text("模型调用未验证").foregroundStyle(.secondary)
                                    if let route = verifiedEndpointRoute, let checked = model.routeLastSuccess[route.id] {
                                        TimelineView(.periodic(from: .now, by: 30)) { context in
                                            Text("\(context.date.timeIntervalSince(checked) > 120 ? "上次成功（已过期）" : "最近接口成功") \(checked.formatted(date: .omitted, time: .standard))")
                                                .font(.caption2).foregroundStyle(.secondary)
                                        }
                                    }
                                }.font(.caption)
                            }
                        }
                    }
                    ForEach(model.routingDevices.filter { !$0.isLocal && $0.runtime?.configuredEndpoint != nil }) { device in
                        HStack(spacing: 18) {
                            node(device.name, detail: device.connectionTitle, symbol: "server.rack", color: .secondary) {
                                model.selectedRoutingDeviceID = device.id
                            }
                            if let endpoint = device.runtime?.configuredEndpoint {
                                Image(systemName: "arrow.right").foregroundStyle(.secondary)
                                node("PCL 接入点", detail: endpoint, symbol: "arrow.triangle.branch", color: .purple) {
                                    model.selectedRoutingDeviceID = device.id
                                }
                                Text("设备报告的配置 · \(device.runtime?.checkedAt ?? "时间未知")\n模型调用未验证")
                                    .font(.caption).foregroundStyle(.secondary)
                            } else {
                                Text("路径未确认 · 设备未报告模型接入点").font(.caption).foregroundStyle(.secondary)
                            }
                        }
                    }
                    Text("远端只报告心跳时，模型路径保持未确认；同步关系不是转发路线。")
                        .font(.caption).foregroundStyle(.secondary)
                }.padding(20).frame(minWidth: 840, alignment: .leading)
            }
            .frame(height: model.routingDevices.contains(where: { !$0.isLocal && $0.runtime?.configuredEndpoint != nil }) ? 400 : 250)
            .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(Color.primary.opacity(0.08)))
            HStack {
                Text("接入点").font(.subheadline)
                Picker("查看接入点", selection: $model.selectedGatewayID) {
                    Text("选择接入点").tag(String?.none)
                    ForEach(model.gatewayRoutes?.gateways ?? []) { route in
                        Text(route.name + (route.selected ? " · 当前" : "")).tag(Optional(route.id))
                    }
                }.labelsHidden().frame(maxWidth: 300)
                Spacer()
                if let route = inspected {
                    if route.selected {
                        Text("当前配置").font(.caption).foregroundStyle(.secondary)
                    } else {
                        Button("移除接入点") { Task { await model.removeGatewayRoute(route) } }.disabled(model.isSwitchingGateway)
                        Button(model.isSwitchingGateway ? "应用中…" : "应用到本机") { Task { await model.selectGatewayRoute(route) } }
                            .disabled(model.isSwitchingGateway || route.healthy != true)
                    }
                }
            }
            if let route = inspected, !route.error.isEmpty {
                Label(route.error, systemImage: "exclamationmark.triangle").font(.caption).foregroundStyle(.orange).textSelection(.enabled)
            }
        }
    }

    private func node(_ title: String, detail: String, symbol: String, color: Color, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 10) {
                Image(systemName: symbol).font(.title3).foregroundStyle(color)
                VStack(alignment: .leading, spacing: 6) {
                    Text(title).font(.subheadline.weight(.semibold)).foregroundStyle(.primary).lineLimit(1)
                    Text(detail).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                }
            }.padding(14).frame(width: 230, height: 76, alignment: .leading)
                .background(Color.primary.opacity(0.035), in: RoundedRectangle(cornerRadius: 10))
                .overlay(RoundedRectangle(cornerRadius: 10).stroke(color.opacity(0.25)))
                .contentShape(Rectangle())
        }.buttonStyle(.plain).help(detail)
    }
}
