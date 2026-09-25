import Foundation

@main struct VerifyAPIConnectionInfo {
    static func main() throws {
        let info = APIConnectionInfo(gateway: "http://100.1.2.3:15722/v1", modelID: "GLM-5.2")!
        let data = Data(info.openCodeConfiguration(modelIDs: ["GLM-5.2", "DeepSeek-V4-Pro", "unknown"]).utf8)
        let config = try JSONSerialization.jsonObject(with: data) as! [String: Any]
        let providers = config["providers"] as! [String: [String: Any]]
        let models = providers["pcl-relay"]!["models"] as! [String: [String: Any]]
        for id in ["GLM-5.2", "DeepSeek-V4-Pro"] {
            let caps = models[id]!["capabilities"] as! [String: Any]
            precondition(caps["input"] as! [String] == ["text"])
            precondition(caps["tools"] as! Bool)
            precondition(models[id]!["settings"] == nil)
        }
        precondition(models["unknown"]!["disabled"] as! Bool)
        precondition(config["model"] == nil)
        precondition(!String(decoding: data, as: UTF8.self).contains("limit"))
        precondition(info.setupGuide(modelIDs: ["GLM-5.2"]).contains("Relay 不删除图片"))
        print("API configuration export checks passed")
    }
}
