import Foundation

/// Export only the configured PCL gateway, never the OpenAI login/router or keys.
public struct APIConnectionInfo {
    public let baseURL: String
    public let modelID: String
    public let localOnly: Bool
    public init?(gateway: String, modelID: String) {
        guard let url = URL(string: gateway), ["http", "https"].contains(url.scheme ?? ""),
              let host = url.host, url.user == nil, url.password == nil,
              url.query == nil, url.fragment == nil, !modelID.isEmpty else { return nil }
        let root = gateway.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        self.baseURL = root.hasSuffix("/v1") ? root : root + "/v1"
        self.modelID = modelID
        self.localOnly = ["localhost", "127.0.0.1", "::1", "[::1]"].contains(host)
    }
    public var credentialNotice: String {
        "当前中转站依赖网络访问权限，不校验客户端 API Key。客户端强制要求时填写 pcl-tailnet；这是占位文本，不是密钥。上游密钥仍保存在中转站。"
    }
    public var summary: String {
        "协议：OpenAI-compatible Chat Completions\nBase URL: \(baseURL)\n模型目录：\(baseURL)/models\nAPI Key: pcl-tailnet（仅占位，无认证作用）\n同一接入配置可使用网关提供的全部兼容模型；不受 Codex Agent 勾选限制。\n\(localOnly ? "地址仅限当前设备使用" : "默认客户端已加入 Tailnet")\n不要公开暴露此接口。"
    }
    public func clientConfiguration(_ client: String, modelIDs: [String]) -> String {
        let ids = Array(Set(modelIDs)).sorted()
        let configuration: [String: Any]
        if client == "pi" {
            configuration = ["providers": ["pcl-relay": ["baseUrl": baseURL,
                "api": "openai-completions", "apiKey": "pcl-tailnet",
                "models": ids.map { ["id": $0] }]]]
        } else {
            configuration = ["$schema": "https://opencode.ai/config.json",
                "provider": ["pcl-relay": ["npm": "@ai-sdk/openai-compatible", "name": "PCL Relay",
                    "options": ["baseURL": baseURL, "apiKey": "pcl-tailnet"],
                    "models": Dictionary(uniqueKeysWithValues: ids.map { ($0, ["name": $0]) })]]]
        }
        let data = try! JSONSerialization.data(withJSONObject: configuration, options: [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes])
        return String(decoding: data, as: UTF8.self)
    }
    public var curlExample: String {
        let body: [String: Any] = ["model": modelID, "messages": [["role": "user", "content": "Reply OK."]], "stream": true]
        let data = try! JSONSerialization.data(withJSONObject: body, options: [.sortedKeys])
        func quote(_ value: String) -> String { "'" + value.replacingOccurrences(of: "'", with: "'\\''") + "'" }
        return "curl -N \(quote(baseURL + "/chat/completions")) \\\n  -H 'Content-Type: application/json' \\\n  -d \(quote(String(decoding: data, as: UTF8.self)))"
    }
}
