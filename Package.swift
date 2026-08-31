// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "PCLCodexManager",
    platforms: [.macOS(.v14)],
    products: [
        .library(name: "BridgeCore", targets: ["BridgeCore"]),
        .executable(name: "PCLCodexManager", targets: ["PCLCodexManager"]),
    ],
    targets: [
        .target(name: "BridgeCore", path: "macos/Sources/BridgeCore"),
        .executableTarget(
            name: "PCLCodexManager",
            dependencies: ["BridgeCore"],
            path: "macos/Sources/PCLCodexManager"
        ),
        .testTarget(
            name: "BridgeCoreTests",
            dependencies: ["BridgeCore"],
            path: "macos/Tests/BridgeCoreTests"
        ),
    ],
    swiftLanguageModes: [.v5]
)
