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
    public func setupGuide(modelIDs: [String]) -> String {
        let rows = Array(Set(modelIDs)).sorted().map { "模型 ID：\($0)；显示名称：\($0)" }.joined(separator: "\n")
        return """
        OpenAI 兼容提供商填写指引
        提供商类型：OpenAI 兼容（Chat Completions，不是官方 OpenAI 登录）
        提供商 ID：pcl-relay
        显示名称：PCL Relay
        基础 URL：\(baseURL)
        API 密钥：pcl-tailnet（占位，不是真实密钥）
        请求头：留空
        模型目录：\(baseURL)/models
        模型：自动获取目录，或逐项添加下面的原始 ID；显示名称可与 ID 相同，不加 pcl/。
        \(rows)

        以上是通用接入字段，不是客户端专用配置文件。请填写到当前客户端的对应设置，不覆盖其他提供商。

        保存后验收：提供商列表出现 PCL Relay → 在‘管理模型’开启 PCL Relay 分组（新模型可能默认隐藏）→ 明确选择 PCL 模型，发送一次短消息确认回复（消耗少量额度）。复制成功、目录可读和实际生成成功是三个不同状态。
        超时或无法解析地址：检查当前客户端的 Tailnet 连通性。
        401/403：检查返回错误的来源及网关认证，不要粘贴 Codex 登录凭据。
        模型不存在：刷新目录，使用精确模型 ID。
        """
    }
    public var summary: String {
        "协议：OpenAI-compatible Chat Completions\nBase URL: \(baseURL)\n模型目录：\(baseURL)/models\nAPI Key: pcl-tailnet（仅占位，无认证作用）\n同一接入配置可使用网关提供的全部兼容模型；不受 Codex Agent 勾选限制。\n\(localOnly ? "地址仅限当前设备使用" : "默认客户端已加入 Tailnet")\n不要公开暴露此接口。"
    }
    public var curlExample: String {
        let body: [String: Any] = ["model": modelID, "messages": [["role": "user", "content": "Reply OK."]], "stream": true]
        let data = try! JSONSerialization.data(withJSONObject: body, options: [.sortedKeys])
        func quote(_ value: String) -> String { "'" + value.replacingOccurrences(of: "'", with: "'\\''") + "'" }
        return "curl -N \(quote(baseURL + "/chat/completions")) \\\n  -H 'Content-Type: application/json' \\\n  -d \(quote(String(decoding: data, as: UTF8.self)))"
    }
}
