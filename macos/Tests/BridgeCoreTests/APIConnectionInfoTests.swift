import XCTest
@testable import BridgeCore

final class APIConnectionInfoTests: XCTestCase {
    func testGuideMatchesFormAndDoesNotPromiseClientSupport() {
        let info = APIConnectionInfo(gateway: "http://100.1.2.3:15722/v1", modelID: "Kimi-K3")!
        let guide = info.setupGuide(modelIDs: ["Kimi-K3", "GLM-5.2"])
        for text in [info.baseURL, "请求头：留空", "模型 ID：Kimi-K3", "显示名称：GLM-5.2", "2.0.15", "不代表所有版本支持", "消耗少量额度"] {
            XCTAssertTrue(guide.contains(text), text)
        }
        XCTAssertFalse(guide.contains("pcl/Kimi-K3"))
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
    func testOneProviderContainsEveryModelWithoutSelectionFilter() throws {
        let info = APIConnectionInfo(gateway: "http://100.1.2.3:15722/v1", modelID: "Kimi-K3")!
        let ids = ["Kimi-K3", "GLM-5.2", "DeepSeek-V4-Pro", "Kimi-K3"]
        let pi = try JSONSerialization.jsonObject(with: Data(info.clientConfiguration("pi", modelIDs: ids).utf8)) as! [String: Any]
        let providers = pi["providers"] as! [String: [String: Any]]
        XCTAssertEqual(providers.count, 1)
        XCTAssertEqual((providers["pcl-relay"]!["models"] as! [[String: String]]).count, 3)
        let oc = try JSONSerialization.jsonObject(with: Data(info.clientConfiguration("opencode", modelIDs: ids).utf8)) as! [String: Any]
        let models = ((oc["provider"] as! [String: [String: Any]])["pcl-relay"]!["models"] as! [String: Any])
        XCTAssertEqual(Set(models.keys), Set(ids))
        XCTAssertTrue(info.summary.contains("全部兼容模型"))
    }
}
