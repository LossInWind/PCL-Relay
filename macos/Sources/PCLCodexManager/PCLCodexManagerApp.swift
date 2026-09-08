import SwiftUI

@main
struct PCLCodexManagerApp: App {
    @NSApplicationDelegateAdaptor(RelayAppDelegate.self) private var appDelegate
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

        .commands {
            CommandGroup(replacing: .newItem) { }
            CommandGroup(replacing: .appSettings) {
                Button("打开完整设置") { SettingsWindowPresenter.shared.show(model) }
                    .keyboardShortcut(",", modifiers: .command)
            }
            CommandMenu("中转站") {
                Button("刷新状态") { model.refreshAll() }
                    .keyboardShortcut("r", modifiers: .command)
                Button("能力实测…") {
                    SettingsWindowPresenter.shared.show(model)
                    model.showDetectionConfirmation = true
                }
                    .keyboardShortcut("d", modifiers: [.command, .shift])
            }
        }
    }
}
