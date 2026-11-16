// render.swift FONT SIZE TEXT OUT.pgm [TRACKING] [STROKE]
import Foundation
import CoreGraphics
import CoreText
let a = CommandLine.arguments
let fontName = a[1]; let size = CGFloat(Double(a[2])!); let text = a[3]; let out = a[4]
let tracking = a.count > 5 ? CGFloat(Double(a[5])!) : 0
let stroke = a.count > 6 ? CGFloat(Double(a[6])!) : 0
let font = CTFontCreateWithName(fontName as CFString, size, nil)
var attrs: [NSAttributedString.Key: Any] = [.init(kCTFontAttributeName as String): font, .init(kCTKernAttributeName as String): tracking,
    .init(kCTForegroundColorAttributeName as String): CGColor(gray: 1, alpha: 1)]
if stroke != 0 { attrs[.init(kCTStrokeWidthAttributeName as String)] = stroke; attrs[.init(kCTStrokeColorAttributeName as String)] = CGColor(gray: 1, alpha: 1) }
let line = CTLineCreateWithAttributedString(NSAttributedString(string: text, attributes: attrs))
var asc: CGFloat = 0, desc: CGFloat = 0, lead: CGFloat = 0
let w = CTLineGetTypographicBounds(line, &asc, &desc, &lead)
let W = Int(ceil(w)) + 8, H = Int(ceil(asc + desc)) + 8
let ctx = CGContext(data: nil, width: W, height: H, bitsPerComponent: 8, bytesPerRow: W, space: CGColorSpaceCreateDeviceGray(), bitmapInfo: CGImageAlphaInfo.none.rawValue)!
ctx.setFillColor(gray: 0, alpha: 1); ctx.fill(CGRect(x: 0, y: 0, width: W, height: H))
ctx.setShouldAntialias(true); ctx.setAllowsFontSmoothing(false)
ctx.textPosition = CGPoint(x: 4, y: 4 + desc)
CTLineDraw(line, ctx)
let data = ctx.data!.assumingMemoryBound(to: UInt8.self)
var s = "P2\n\(W) \(H)\n255\n# baseline \(Int(round(4 + desc)))\n"
for y in 0..<H { var row: [String] = []; for x in 0..<W { row.append(String(data[y * W + x])) }; s += row.joined(separator: " ") + "\n" }
try! s.write(toFile: out, atomically: true, encoding: .utf8)
