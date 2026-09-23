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
    public static let openCodeCompatibilityNotice = "兼容提示：2026-09-23 本机 OpenCode 2.0.15 的自定义提供商提交入口直接返回‘此服务器上无法使用自定义提供商’。这是客户端保存入口的限制，不是 PCL 网关连接失败。下方 JSON 对应官网 provider 格式，不代表所有版本支持；遇到此提示请停止重复提交，核对客户端版本和上游支持，不要改代理或重启中转站。"
    public func setupGuide(modelIDs: [String]) -> String {
        let rows = Array(Set(modelIDs)).sorted().map { "模型 ID：\($0)；显示名称：\($0)" }.joined(separator: "\n")
        return """
        OpenAI 兼容提供商填写指引
        提供商 ID：pcl-relay（若已有 pcl 可保留，配置和 /connect 必须一致）
        显示名称：PCL Relay
        基础 URL：\(baseURL)
        API 密钥：pcl-tailnet（占位，不是真实密钥）
        请求头：留空
        模型：每点一次‘添加模型’增加一行，左右两栏均可填写下面的原始 ID，不加 pcl/。
        \(rows)

        \(Self.openCodeCompatibilityNotice)

        保存后验收：提供商列表出现 PCL Relay → 模型列表出现所填模型 → 明确选择 PCL 模型，发送一次短消息确认回复（消耗少量额度）。复制成功、目录可读和实际生成成功是三个不同状态。
        超时或无法解析地址：检查当前客户端的 Tailnet 连通性。
        401/403：检查返回错误的来源及网关认证，不要粘贴 Codex 登录凭据。
        模型不存在：刷新目录，使用精确模型 ID。
        """
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
