import AppKit
import BridgeCore
import SwiftUI

enum AppSection: String, CaseIterable, Identifiable {
    case routing = "路由"
    case models = "模型与 Agent"
    case portal = "PCL 门户"
    var id: String { rawValue }
    var symbol: String {
        switch self {
        case .routing: return "arrow.triangle.branch"
        case .models: return "person.3.sequence.fill"
        case .portal: return "globe.asia.australia.fill"
        }
    }
}
struct RootView: View {
    @EnvironmentObject private var model: AppModel
    @AppStorage("selectedSection") private var sectionRaw = AppSection.routing.rawValue

    private var section: AppSection {
        AppSection(rawValue: sectionRaw) ?? .routing
    }

    private var sectionBinding: Binding<AppSection> {
        Binding(
            get: { section },
            set: { sectionRaw = $0.rawValue }
        )
    }

    var body: some View {
        ZStack(alignment: .top) {
            LinearGradient(
                colors: [Color(nsColor: .windowBackgroundColor), Color.accentColor.opacity(0.055)],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
            .ignoresSafeArea()

            VStack(spacing: 0) {
                HeaderBar(section: sectionBinding)
                Group {
                    switch section {
                    case .routing: RoutingView()
                    case .models: ModelsAgentsView()
                    case .portal: PortalView()
                    }
                }
            }

            if let banner = model.banner {
                BannerView(message: banner)
                    .padding(.top, 62)
                    .transition(.move(edge: .top).combined(with: .opacity))
                    .zIndex(4)
            }
        }
        .preferredColorScheme(.dark)
    }
}
