import XCTest
@testable import BridgeCore

final class APIConnectionInfoTests: XCTestCase {
    func testGenericGuideContainsEveryModelAndNoClientSpecificFormat() {
        let info = APIConnectionInfo(gateway: "http://100.1.2.3:15722/v1", modelID: "Kimi-K3")!
        let guide = info.setupGuide(modelIDs: ["Kimi-K3", "GLM-5.2", "Kimi-K3"])
        for text in [info.baseURL, "请求头：留空", "模型 ID：Kimi-K3", "显示名称：GLM-5.2", "OpenAI 兼容", "不是客户端专用配置文件", "消耗少量额度"] {
            XCTAssertTrue(guide.contains(text), text)
        }
        for text in ["OpenCode", "opencode", "Pi 配置", "1.x", "pcl/Kimi-K3"] {
            XCTAssertFalse(guide.contains(text), text)
        }
        XCTAssertEqual(guide.components(separatedBy: "模型 ID：Kimi-K3").count, 2)
    }
    func testExportUsesRawModelAndDoesNotClaimSecret() {
        let info = APIConnectionInfo(gateway: "http://100.1.2.3:15722/v1/", modelID: "Kimi-K3")!
        XCTAssertEqual(info.baseURL, "http://100.1.2.3:15722/v1")
        XCTAssertTrue(info.curlExample.contains("/v1/chat/completions"))
        XCTAssertFalse(info.curlExample.contains("pcl/Kimi"))
        XCTAssertTrue(info.summary.contains("无认证作用"))
        XCTAssertFalse(info.localOnly)
    }
    func testLocalScopeAndUnsafeURLs() {
        XCTAssertTrue(APIConnectionInfo(gateway: "http://127.0.0.1:15722/v1", modelID: "model")!.localOnly)
        for url in ["file:///tmp/config", "http://user:secret@host/v1", "http://host/v1?key=secret"] {
            XCTAssertNil(APIConnectionInfo(gateway: url, modelID: "model"))
        }
    }
}
