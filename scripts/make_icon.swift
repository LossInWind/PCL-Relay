import AppKit
import CoreGraphics
import Foundation

guard CommandLine.arguments.count == 2 else {
    fputs("usage: make_icon.swift OUTPUT.iconset\n", stderr)
    exit(2)
}

let output = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)

let variants: [(String, CGFloat)] = [
    ("icon_16x16.png", 16),
    ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32),
    ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128),
    ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256),
    ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512),
    ("icon_512x512@2x.png", 1024),
]

for (name, size) in variants {
    let pixels = Int(size)
    guard let context = CGContext(
        data: nil,
        width: pixels,
        height: pixels,
        bitsPerComponent: 8,
        bytesPerRow: 0,
        space: CGColorSpaceCreateDeviceRGB(),
        bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
    ) else { throw NSError(domain: "Icon", code: 1) }

    let rect = CGRect(x: 0, y: 0, width: size, height: size)
    context.clear(rect)
    let inset = size * 0.07
    let tileRect = rect.insetBy(dx: inset, dy: inset)
    let tile = CGPath(roundedRect: tileRect, cornerWidth: size * 0.22, cornerHeight: size * 0.22, transform: nil)
    context.saveGState()
    context.addPath(tile)
    context.clip()
    let colors = [
        CGColor(red: 0.12, green: 0.48, blue: 0.98, alpha: 1),
        CGColor(red: 0.05, green: 0.82, blue: 0.87, alpha: 1),
    ] as CFArray
    let gradient = CGGradient(colorsSpace: CGColorSpaceCreateDeviceRGB(), colors: colors, locations: [0, 1])!
    context.drawLinearGradient(
        gradient,
        start: CGPoint(x: tileRect.minX, y: tileRect.maxY),
        end: CGPoint(x: tileRect.maxX, y: tileRect.minY),
        options: []
    )
    context.restoreGState()
    context.addPath(tile)
    context.setStrokeColor(CGColor(gray: 1, alpha: 0.22))
    context.setLineWidth(max(1, size * 0.012))
    context.strokePath()

    let configuration = NSImage.SymbolConfiguration(pointSize: size * 0.48, weight: .semibold)
        .applying(.init(paletteColors: [.white]))
    if let symbol = NSImage(systemSymbolName: "point.3.connected.trianglepath.dotted", accessibilityDescription: nil)?
        .withSymbolConfiguration(configuration) {
        let symbolSize = NSSize(width: size * 0.58, height: size * 0.58)
        let symbolRect = NSRect(
            x: (size - symbolSize.width) / 2,
            y: (size - symbolSize.height) / 2,
            width: symbolSize.width,
            height: symbolSize.height
        )
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(cgContext: context, flipped: false)
        symbol.draw(in: symbolRect)
        NSGraphicsContext.restoreGraphicsState()
    }

    guard let cgImage = context.makeImage(),
          let bitmap = Optional(NSBitmapImageRep(cgImage: cgImage)),
          let png = bitmap.representation(using: .png, properties: [:]) else {
        throw NSError(domain: "Icon", code: 2)
    }
    try png.write(to: output.appendingPathComponent(name))
}
