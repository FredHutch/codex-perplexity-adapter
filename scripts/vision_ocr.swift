import Foundation
import Vision

var results: [[String: Any]] = []
for path in CommandLine.arguments.dropFirst() {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = false
    request.recognitionLanguages = ["en-US"]
    try VNImageRequestHandler(url: URL(fileURLWithPath: path)).perform([request])
    results.append(["file": URL(fileURLWithPath: path).lastPathComponent,
                    "lines": (request.results ?? []).compactMap { $0.topCandidates(1).first?.string }])
}
let data = try JSONSerialization.data(withJSONObject: results, options: [.prettyPrinted, .sortedKeys])
print(String(data: data, encoding: .utf8)!)
