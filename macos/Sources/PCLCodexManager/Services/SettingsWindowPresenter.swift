import AppKit
import SwiftUI

/// One window for menu-bar, reopen and keyboard entry points; close is not quit.
@MainActor
final class SettingsWindowPresenter {
    static let shared = SettingsWindowPresenter()
    weak var model: AppModel?
    private var controller: NSWindowController?
    func show(_ model: AppModel) {
        self.model = model
        if controller == nil {
            let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1120, height: 760),
                                  styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
            window.title = "PCL Relay"
            window.identifier = NSUserInterfaceItemIdentifier("pcl-relay-settings")
            window.contentView = NSHostingView(rootView: RootView().environmentObject(model))
            window.minSize = NSSize(width: 960, height: 640)
            window.isReleasedWhenClosed = false
            window.center()
            window.setFrameAutosaveName("PCLRelaySettings")
            controller = NSWindowController(window: window)
        }
        controller?.showWindow(nil)
        controller?.window?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }
}

@MainActor
final class RelayAppDelegate: NSObject, NSApplicationDelegate {
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if let model = SettingsWindowPresenter.shared.model { SettingsWindowPresenter.shared.show(model) }
        return true
    }
}
