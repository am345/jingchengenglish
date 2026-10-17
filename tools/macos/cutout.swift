import AVFoundation
import CoreImage
import Vision
import AppKit

guard CommandLine.arguments.count == 4 else { fatalError("Usage: cutout input.mp4 output_dir frames") }
let source = CommandLine.arguments[1]
let destination = CommandLine.arguments[2]
let expected = Int(CommandLine.arguments[3])!
try FileManager.default.createDirectory(atPath: destination, withIntermediateDirectories: true)
let asset = AVURLAsset(url: URL(fileURLWithPath: source))
let reader = try AVAssetReader(asset: asset)
let output = AVAssetReaderTrackOutput(track: asset.tracks(withMediaType: .video)[0], outputSettings: [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA])
reader.add(output)
guard reader.startReading() else { fatalError("Could not decode source") }
let colorSpace = CGColorSpace(name: CGColorSpace.sRGB)!
let ctx = CIContext(options: [.workingColorSpace: colorSpace])
let alphaKernel = CIColorKernel(source:"""
kernel vec4 alpha(__sample c, __sample m) {
  float a = smoothstep(0.08, 0.97, m.r);
  return vec4(c.rgb * a, a);
}
""")!
let request = VNGeneratePersonSegmentationRequest()
request.qualityLevel = .accurate
request.outputPixelFormat = kCVPixelFormatType_OneComponent8
var n = 0
while n < expected, let sample = output.copyNextSampleBuffer(), let pb = CMSampleBufferGetImageBuffer(sample) {
  try autoreleasepool {
    try VNImageRequestHandler(cvPixelBuffer: pb).perform([request])
    guard let maskBuffer = request.results?.first?.pixelBuffer else { fatalError("Missing mask at frame \(n)") }
    let frame = CIImage(cvPixelBuffer: pb)
    let low = CIImage(cvPixelBuffer: maskBuffer)
    let mask = low.transformed(by: CGAffineTransform(scaleX: frame.extent.width / low.extent.width, y: frame.extent.height / low.extent.height))
    let rgba = alphaKernel.apply(extent: frame.extent, arguments: [frame, mask])!
    let url = URL(fileURLWithPath: destination).appendingPathComponent(String(format: "%04d.png", n))
    try ctx.writePNGRepresentation(of: rgba, to: url, format: .RGBA8, colorSpace: colorSpace)
  }
  n += 1
  if n % 30 == 0 { print("Cutout: \(n)/\(expected)"); fflush(stdout) }
}
reader.cancelReading()
guard n == expected else { fatalError("Incomplete teacher sequence: \(n)/\(expected)") }
print("Cutout complete: \(n) full-frame PNGs.")
