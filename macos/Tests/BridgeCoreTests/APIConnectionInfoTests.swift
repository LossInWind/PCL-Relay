import XCTest
@testable import BridgeCore

final class APIConnectionInfoTests: XCTestCase {
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
