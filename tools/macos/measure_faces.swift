import Foundation
import Vision
let root = URL(fileURLWithPath: CommandLine.arguments[1])
let step = Int(CommandLine.arguments.dropFirst(2).first ?? "6") ?? 6
let files = try FileManager.default.contentsOfDirectory(at: root, includingPropertiesForKeys: nil).filter {$0.pathExtension == "png"}.sorted {$0.lastPathComponent < $1.lastPathComponent}
for (index,file) in files.enumerated() where index % step == 0 || index == files.count-1 {
 let request = VNDetectFaceLandmarksRequest()
 try VNImageRequestHandler(url:file,options:[:]).perform([request])
 var row:[String:Any] = ["frame":index]
 if let face=(request.results ?? []).max(by:{$0.boundingBox.width < $1.boundingBox.width}) {
  let b=face.boundingBox
  row["bbox"]=[Double(b.minX)*1280,Double(1-b.maxY)*720,Double(b.width)*1280,Double(b.height)*720]
  row["yaw"]=face.yaw?.doubleValue ?? 0
  if let lips=face.landmarks?.innerLips {
   row["inner_lips"]=lips.normalizedPoints.map { [Double($0.x), Double($0.y)] }
  }
 }
 let data=try JSONSerialization.data(withJSONObject:row,options:[.sortedKeys]);print(String(data:data,encoding:.utf8)!)
}
