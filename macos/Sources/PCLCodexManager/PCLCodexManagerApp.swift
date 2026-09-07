import SwiftUI

@main
struct PCLCodexManagerApp: App {
    @StateObject private var model = AppModel()

    var body: some Scene {
        MenuBarExtra {
            MenuBarPanel()
                .environmentObject(model)
        } label: {
            Image(systemName: model.networkReady ? "point.3.connected.trianglepath.dotted" : "point.3.filled.connected.trianglepath.dotted")
                .accessibilityLabel("PCL Relay，\(model.networkStatusTitle)")
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
