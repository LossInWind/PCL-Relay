import SwiftUI

@main
struct PCLCodexManagerApp: App {
    @StateObject private var model = AppModel()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(model)
                .frame(minWidth: 960, minHeight: 640)
                .task {
                    model.refreshAll()
                    model.startConsensusMonitoring()
                }
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
