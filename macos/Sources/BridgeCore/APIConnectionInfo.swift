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
    public static func capabilityNotice(_ id: String) -> String {
        switch id {
        case "GLM-5.2", "DeepSeek-V4-Pro":
            return "文本输入／输出；不支持图片（PCL 接口 2026-09-25 实测）。工具调用与推理设置保留；High 已验证可提交，不代表已证明推理质量变化。"
        case "Kimi-K3":
            return "文本与工具调用已实测（2026-09-25）；图片能力未独立验证。长任务可能正常结束但未调用工具，并非都属于网络故障。"
        default:
            return "输入模态、工具与推理能力未在本次逐项验证；目录存在不等于能力通过。"
        }
    }
    /// Client-specific supplement, not a replacement for an existing config.
    /// Unknown models remain selectable but are disabled in the generated template
    /// to prevent OpenCode's implicit image capability from being mistaken for proof.
    public func openCodeConfiguration(modelIDs: [String]) -> String {
        var models: [String: Any] = [:]
        for id in Set(modelIDs) {
            if ["GLM-5.2", "DeepSeek-V4-Pro"].contains(id) {
                models[id] = ["name": id, "capabilities": ["tools": true, "input": ["text"], "output": ["text"]]]
            } else {
                models[id] = ["name": id, "disabled": true]
            }
        }
        let config: [String: Any] = ["providers": ["pcl-relay": [
            "name": "PCL Relay", "package": "@opencode/ai/providers/openai-compatible",
            "settings": ["baseURL": baseURL, "apiKey": "pcl-tailnet"], "models": models
        ]]]
        guard let data = try? JSONSerialization.data(withJSONObject: config, options: [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]) else { return "" }
        return String(decoding: data, as: UTF8.self)
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
        带图片历史切换到文本模型：必须声明模型输入能力；不要只填写模型名称。Relay 不删除图片、不修改原会话。
        GLM-5.2、DeepSeek-V4-Pro：当前 PCL 部署仅文本输入／输出。其他模型能力未验证时不要按名称推测。
        Kimi 只回复执行意图却结束：先检查结束原因；保留原会话，以已完成内容、实际文件和剩余任务整理交接，在独立会话验证小任务。不自动补“继续”或强制工具调用。
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
