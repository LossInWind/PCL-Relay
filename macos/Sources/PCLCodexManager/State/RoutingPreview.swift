#if DEBUG
import AppKit
import BridgeCore
import SwiftUI

/// Offline acceptance fixture. No services, login items or CLI calls are started.
@MainActor
func renderRoutingPreview(to path: String, dark: Bool) throws {
    let model = AppModel()
    model.relaySync = try BridgeDecode.value(RelaySyncCatalog.self, from: #"""
    {"protocol":"pcl-relay-topology/1","node_id":"mac","node_name":"PCL Mac","revision":{"counter":0,"origin":"mac"},"digest":"","peers":[],"count":0,"network_managed":false}
    """#)
    model.gatewayRoutes = try BridgeDecode.value(GatewayRouteCatalog.self, from: #"""
    {"selected_gateway":"http://relay:15722/v1","gateways":[{"id":"relay","name":"pcl-linux-3070ti","url":"http://relay:15722/v1","added_at":"","selected":true,"healthy":true,"model_count":4,"latency_ms":38,"error":""}],"count":1,"network_managed":false}
    """#)
    model.deploymentTargets = try BridgeDecode.value(DeploymentTargetCatalog.self, from: #"""
    {"schema":1,"targets":[
    {"id":"kai","name":"Kai Mac","ssh_target":"kai-mac","control_url":"http://kai:15726","added_at":"","receiver_online":true,"relay_version":"2.5.12","version":"2.5.12","relay_node_id":"kai"},
    {"id":"3070","name":"pcl-linux-3070ti","ssh_target":"pcl-linux-3070ti","control_url":"http://3070:15726","added_at":"","receiver_online":true,"relay_version":"2.5.10","version":"2.5.12","relay_node_id":"3070"},
    {"id":"a6000","name":"pcl-a6000x2-shared-pod","ssh_target":"pcl-a6000x2-shared-pod","control_url":"http://a6000:15726","added_at":"","receiver_online":true,"relay_version":"2.5.10","version":"2.5.12","relay_node_id":"a6000"},
    {"id":"bupt","name":"bupt-a100-shared","ssh_target":"bupt-a100-shared","control_url":"http://bupt:15726","added_at":"","receiver_online":false,"ssh":false,"error":"连接超时"}
    ],"count":4,"discovery":false,"credentials_synchronized":false,"network_managed":false}
    """#)
    model.selectedGatewayID = "relay"
    model.routingCheckedAt = Date()
    model.registry = try BridgeDecode.value(ModelRegistry.self, from: #"""
    {"gateway":"http://relay:15722/v1","selected_agents":["pcl_deepseek_pro","pcl_kimi"],"checked_at":"2026-09-08 16:01","catalog_checked_at":"2026-09-08 18:41","models":{},"available_models":{
    "DeepSeek-V4-Pro":{"id":"DeepSeek-V4-Pro","alias":"pcl_deepseek_pro","family":"DeepSeek","category":"chat","description":"复杂推理和执行任务","agent_eligible":true,"recommended":true,"owned_by":"PCL","input_modalities":["text"]},
    "Kimi-K3":{"id":"Kimi-K3","alias":"pcl_kimi","family":"Kimi","category":"chat","description":"长上下文阅读与分析","agent_eligible":true,"recommended":true,"owned_by":"PCL","input_modalities":["text"]}}}
    """#)
    model.selectedAgents = ["pcl_deepseek_pro", "pcl_kimi"]
    let sectionName = CommandLine.arguments.firstIndex(of: "--preview-section").flatMap {
        CommandLine.arguments.indices.contains($0 + 1) ? CommandLine.arguments[$0 + 1] : nil
    } ?? "routing"
    let section: AppSection = sectionName == "models" ? .models : sectionName == "portal" ? .portal : .routing
    let width: CGFloat = CommandLine.arguments.contains("--narrow") ? 960 : 1120
    let page: AnyView = section == .models ? AnyView(ModelsAgentsView()) : section == .portal ? AnyView(PortalView()) : AnyView(RoutingView())
    let host = NSHostingView(rootView: VStack(spacing: 0) {
        HeaderBar(section: .constant(section))
        page
    }.environmentObject(model)
        .frame(width: width, height: 760)
        .background(Color(nsColor: .windowBackgroundColor))
        .environment(\.colorScheme, dark ? .dark : .light))
    host.frame = NSRect(x: 0, y: 0, width: width, height: 760)
    let window = NSWindow(contentRect: host.frame, styleMask: [.borderless], backing: .buffered, defer: false)
    window.appearance = NSAppearance(named: dark ? .darkAqua : .aqua)
    window.contentView = host
    host.layoutSubtreeIfNeeded()
    RunLoop.current.run(until: Date().addingTimeInterval(0.2))
    guard let bitmap = host.bitmapImageRepForCachingDisplay(in: host.bounds) else { throw NSError(domain: "RoutingPreview", code: 1) }
    host.cacheDisplay(in: host.bounds, to: bitmap)
    guard let data = bitmap.representation(using: .png, properties: [:]) else {
        throw NSError(domain: "RoutingPreview", code: 1)
    }
    try data.write(to: URL(fileURLWithPath: path))
}
#endif
