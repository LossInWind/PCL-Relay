import AppKit
import BridgeCore
import Foundation
import ServiceManagement
import SwiftUI

struct AgentDefinition: Identifiable, Hashable {
    let id: String
    let model: String
    let title: String
    let detail: String
    let symbol: String
    let tint: Color
    let family: String
    let category: String
    let recommended: Bool

    var nativeRoleName: String { id.replacingOccurrences(of: "_", with: "-") }

    init(id: String, model: String, title: String, detail: String, symbol: String, tint: Color, family: String, category: String, recommended: Bool) {
        self.id = id
        self.model = model
        self.title = title
        self.detail = detail
        self.symbol = symbol
        self.tint = tint
        self.family = family
        self.category = category
        self.recommended = recommended
    }

    static let all: [AgentDefinition] = [
        .init(id: "pcl_deepseek_pro", model: "DeepSeek-V4-Pro", title: "DeepSeek Pro", detail: "复杂编码、调试与推理", symbol: "brain.head.profile", tint: .cyan, family: "DeepSeek", category: "chat", recommended: true),
        .init(id: "pcl_deepseek_flash", model: "DeepSeek-V4-Flash-0731", title: "DeepSeek Flash", detail: "快速修改、测试与检索", symbol: "bolt.fill", tint: .blue, family: "DeepSeek", category: "chat", recommended: true),
        .init(id: "pcl_glm", model: "GLM-5.2", title: "GLM", detail: "中文技术任务与独立审查", symbol: "text.bubble.fill", tint: .purple, family: "GLM", category: "chat", recommended: true),
        .init(id: "pcl_kimi", model: "Kimi-K3", title: "Kimi", detail: "长上下文阅读与综合分析", symbol: "moon.stars.fill", tint: .indigo, family: "Kimi", category: "chat", recommended: true),
    ]

    init(model: DiscoveredModel) {
        id = model.alias
        self.model = model.id
        title = model.id
        detail = model.description
        family = model.family
        category = model.category
        recommended = model.recommended
        switch model.family.lowercased() {
        case "deepseek": symbol = model.id.lowercased().contains("flash") ? "bolt.fill" : "brain.head.profile"; tint = .cyan
        case "glm": symbol = "text.bubble.fill"; tint = .purple
        case "kimi": symbol = "moon.stars.fill"; tint = .indigo
        case "qwen": symbol = "q.circle.fill"; tint = .orange
        case "pcl": symbol = "server.rack"; tint = .green
        case "bge": symbol = "square.stack.3d.up.fill"; tint = .mint
        case "whisper": symbol = "waveform"; tint = .pink
        case "paddleocr": symbol = "text.viewfinder"; tint = .teal
        default: symbol = model.category == "image" ? "photo.fill" : "sparkles"; tint = .blue
        }
    }
}
