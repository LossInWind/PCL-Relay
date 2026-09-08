import SwiftUI

@main
struct PCLCodexManagerApp: App {
    @StateObject private var model = AppModel()

    init() {
        #if DEBUG
        if let index = CommandLine.arguments.firstIndex(of: "--render-routing-preview"),
           CommandLine.arguments.count > index + 1 {
            do {
                try renderRoutingPreview(to: CommandLine.arguments[index + 1], dark: CommandLine.arguments.contains("--dark"))
                exit(0)
            } catch { fputs("Routing preview failed: \(error)\n", stderr); exit(1) }
        }
        #endif
    }

    var body: some Scene {
        MenuBarExtra {
            MenuBarPanel()
                .environmentObject(model)
        } label: {
            Image(systemName: model.routeReady ? "arrow.triangle.branch" : "exclamationmark.arrow.triangle.2.circlepath")
                .accessibilityLabel("PCL Relay，\(model.routeStatusTitle)")
                .task { model.start() }
        }
        .menuBarExtraStyle(.window)

        Settings {
            RootView()
                .environmentObject(model)
                .frame(minWidth: 960, minHeight: 640)
                .task { model.start() }
        }
        .windowStyle(.hiddenTitleBar)
        .defaultSize(width: 1120, height: 760)
        .commands {
            CommandGroup(replacing: .newItem) { }
            CommandMenu("中转站") {
                Button("刷新状态") { model.refreshAll() }
                    .keyboardShortcut("r", modifiers: .command)
                Button("检测模型") { model.detectModels() }
                    .keyboardShortcut("d", modifiers: [.command, .shift])
            }
        }
    }
}
