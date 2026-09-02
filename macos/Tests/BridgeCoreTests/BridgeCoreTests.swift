import XCTest
@testable import BridgeCore

final class BridgeCoreTests: XCTestCase {
    func testDecodesRelayRegistry() throws {
        let json = #"""
        {
          "gateway":"http://tailnet:15722/v1",
          "checked_at":"2026-08-31T14:50:17+0800",
          "selected_agents":["pcl_glm"],
          "models":{"pcl_glm":{"agent":"pcl_glm","model":"GLM-5.2","advertised":true,"chat":true,"stream":true,"tool_call":true,"tool_compatible":true,"tool_call_mode":"native","execution_ready":true,"error":""}}
        }
        """#
        let registry = try BridgeDecode.value(ModelRegistry.self, from: json)
        XCTAssertEqual(registry.selectedAgents, ["pcl_glm"])
        XCTAssertEqual(registry.models["pcl_glm"]?.toolCallMode, "native")
        XCTAssertTrue(registry.models["pcl_glm"]?.executionReady == true)
    }

    func testDecodesDoctorWithoutCredentialMaterial() throws {
        let json = #"{"gateway":true,"tailscale":true,"codex":true,"config_managed":true,"profile":true,"catalog":true,"registry":true,"unsandboxed_fallback":false}"#
        let doctor = try BridgeDecode.value(DoctorStatus.self, from: json)
        XCTAssertTrue(doctor.gateway)
        XCTAssertFalse(doctor.unsandboxedFallback)
    }

    func testDecodesDiscoveredModelDetails() throws {
        let json = #"{"gateway":"http://tailnet/v1","models":{},"available_models":{"Qwen3.6-35B":{"id":"Qwen3.6-35B","alias":"pcl_qwen3_6_35b","family":"Qwen","category":"chat","description":"通用文本模型","agent_eligible":true,"recommended":false,"owned_by":"openai","input_modalities":["text"]}}}"#
        let registry = try BridgeDecode.value(ModelRegistry.self, from: json)
        let model = try XCTUnwrap(registry.availableModels?["Qwen3.6-35B"])
        XCTAssertTrue(model.agentEligible)
        XCTAssertEqual(model.family, "Qwen")
    }

    func testDecodesRelayAndCrossPlatformClientDiscovery() throws {
        let json = #"{"selected_gateway":"http://relay.tail.test:15722/v1","remote_gateway":"http://100.64.0.2:15722/v1","checked_at":"2026-08-31T17:00:00+0800","ready_count":1,"consensus":{"interval_seconds":30,"report_count":4,"expected_count":4,"round_id":42,"complete":true,"source":"endpoint_heartbeats"},"nodes":[{"node_name":"linux-node","magic_dns":"linux-node.tail.test","tailscale_ip":"100.64.0.3","online":true,"self":false,"gateway_url":"http://linux-node.tail.test:15722/v1","gateway":false,"pcl_auth":"not_checked","model_count":0,"latency_ms":12,"selected":false,"error":"","ssh_target":"linux-node","client_status":{"ssh":true,"ssh_target":"linux-node","ready":true,"system":"Linux","architecture":"x86_64","python_version":"3.12.3","supported_system":true,"config_managed":true,"client_installed":true,"gateway":"http://100.64.0.2:15722/v1","gateway_reachable":true,"error":""}}]}"#
        let discovery = try BridgeDecode.value(RelayDiscovery.self, from: json)
        XCTAssertEqual(discovery.remoteGateway, "http://100.64.0.2:15722/v1")
        XCTAssertEqual(discovery.nodes.first?.clientStatus?.system, "Linux")
        XCTAssertTrue(discovery.nodes.first?.clientStatus?.ready == true)
        XCTAssertEqual(discovery.consensus?.roundID, 42)
        XCTAssertEqual(discovery.consensus?.expectedCount, 4)
        XCTAssertTrue(discovery.consensus?.complete == true)
    }

    func testDecodesPerDeviceConnectivityTest() throws {
        let json = #"{"target":"pcl-node","node_id":"100.64.0.3","checked_at":"2026-08-31T18:54:08+0800","status":"ready","summary":"本机 PCL 直连可用","route":"local_pcl_direct","tailnet_online":true,"tailnet_last_seen":"","ssh":true,"gateway_reachable":true,"catalog_reachable":true,"model_count":13,"latency_ms":53,"error":"","checks":[{"name":"Tailnet","passed":true,"detail":"设备在线"}]}"#
        let result = try BridgeDecode.value(DeviceConnectivityTest.self, from: json)
        XCTAssertEqual(result.modelCount, 13)
        XCTAssertEqual(result.route, "local_pcl_direct")
        XCTAssertTrue(result.checks.first?.passed == true)
    }

    func testDecodesPortalForwardingStatus() throws {
        let json = #"{"available":true,"portal_url":"https://llmapi.pcl.ac.cn","proxy_url":"http://relay:15722","pac_url":"http://relay:15722/admin/portal.pac","latency_ms":82,"http_status":200,"content_type":"text/html","error":"","system_proxy_changed":false}"#
        let status = try BridgeDecode.value(PortalStatus.self, from: json)
        XCTAssertTrue(status.available)
        XCTAssertEqual(status.latencyMS, 82)
        XCTAssertFalse(status.systemProxyChanged ?? true)
    }

    func testDecodesGitHubReleaseUpdateStatus() throws {
        let json = #"{"available":true,"source":"github:LossInWind/PCL-Relay","current_version":"2.1.0","latest_version":"2.2.0","update_available":true,"release_url":"https://github.com/LossInWind/PCL-Relay/releases/tag/v2.2.0","published_at":"2026-09-01T00:00:00Z","asset_name":"PCL-Relay-macOS.zip","asset_size":42,"checked_at":"2026-09-01T08:00:00+0800","error":""}"#
        let status = try BridgeDecode.value(ReleaseUpdateStatus.self, from: json)
        XCTAssertTrue(status.updateAvailable)
        XCTAssertEqual(status.latestVersion, "2.2.0")
        XCTAssertEqual(status.assetName, "PCL-Relay-macOS.zip")
    }
}
