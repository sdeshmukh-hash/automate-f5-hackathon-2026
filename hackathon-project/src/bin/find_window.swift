import CoreGraphics
import Foundation

let pattern = CommandLine.arguments.count > 1 ? CommandLine.arguments[1].lowercased() : "chrome"

let list = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] ?? []

var bestWid: Int? = nil

for w in list {
    let owner = (w[kCGWindowOwnerName as String] as? String ?? "").lowercased()
    let wid = w[kCGWindowNumber as String] as? Int ?? 0
    let title = (w[kCGWindowName as String] as? String ?? "").lowercased()
    let bounds = w[kCGWindowBounds as String] as? [String: CGFloat] ?? [:]
    let width = bounds["Width"] ?? 0
    let height = bounds["Height"] ?? 0

    // Skip tiny helper windows / popups
    if width < 300 || height < 200 {
        continue
    }

    if (owner.contains("chrome") || owner.contains("chromium")) && (title.contains("volterra") || title.contains("usage") || title.contains("requests")) {
        print(wid)
        exit(0)
    }

    if bestWid == nil && (owner.contains("chrome") || owner.contains("chromium")) {
        bestWid = wid
    }
}

if let wid = bestWid {
    print(wid)
} else {
    exit(1)
}
