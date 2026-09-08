import Foundation

/// Read-only observations, not a claim that a model request has succeeded.
public struct CheckEvidence: Equatable, Sendable {
    public enum Phase: String, Sendable { case unknown, checking, succeeded, failed }
    public private(set) var phase: Phase = .unknown
    public private(set) var checkedAt: Date?
    public private(set) var lastSuccessAt: Date?
    public private(set) var error: String?
    public private(set) var generation = 0
    public init() {}
    @discardableResult public mutating func begin() -> Int {
        generation += 1
        phase = .checking
        error = nil
        return generation
    }
    public mutating func finish(_ generation: Int, error: String? = nil, at date: Date = Date()) {
        guard generation == self.generation else { return }
        checkedAt = date
        self.error = error
        phase = error == nil ? .succeeded : .failed
        if error == nil { lastSuccessAt = date }
    }
    public var summary: String {
        switch phase {
        case .unknown: return "尚未检查"
        case .checking: return "正在检查；已有结果仅供参考"
        case .failed: return "检查失败：\(error ?? "未知错误")" + (lastSuccessAt == nil ? "" : " · 保留历史结果")
        case .succeeded: return "最近检查 \(checkedAt?.formatted(date: .omitted, time: .standard) ?? "未知")"
        }
    }
}

/// Latest-intent queue. A failed write never leaves an unconfirmed switch on screen.
public struct AgentSelectionState: Equatable, Sendable {
    public private(set) var confirmed: Set<String>
    public private(set) var displayed: Set<String>
    public private(set) var pending: Set<String>?
    public private(set) var failed: Set<String>?
    public private(set) var revision = 0
    public init(_ initial: Set<String>) { confirmed = initial; displayed = initial }
    public mutating func request(_ value: Set<String>) {
        revision += 1; displayed = value; pending = value; failed = nil
    }
    public mutating func takeNext() -> Set<String>? {
        let value = pending; pending = nil; return value
    }
    public mutating func succeed(_ value: Set<String>) {
        confirmed = value
        if pending == nil { displayed = value }
    }
    public mutating func fail(_ attempted: Set<String>) {
        failed = pending ?? attempted; pending = nil; displayed = confirmed
    }
    public mutating func adopt(_ value: Set<String>, revision: Int) {
        guard revision == self.revision, pending == nil else { return }
        confirmed = value; displayed = value
    }
}
