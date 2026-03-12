import Flutter
import UIKit
import MediaPipeTasksGenAI

/// AppDelegate with MethodChannel handler for on-device Gemma-2B inference
/// via MediaPipe LLM Inference API (2026).
///
/// Channel: "com.ura_chatbot/llm_inference"
/// Methods: initialize, generate, dispose
@main
@objc class AppDelegate: FlutterAppDelegate {

    private var llmInference: LlmInference?
    private let inferenceQueue = DispatchQueue(
        label: "com.ura_chatbot.llm_inference",
        qos: .userInitiated
    )

    override func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?
    ) -> Bool {
        let controller = window?.rootViewController as! FlutterViewController

        let channel = FlutterMethodChannel(
            name: "com.ura_chatbot/llm_inference",
            binaryMessenger: controller.binaryMessenger
        )

        channel.setMethodCallHandler { [weak self] call, result in
            guard let self = self else { return }
            switch call.method {
            case "initialize":
                self.handleInitialize(call: call, result: result)
            case "generate":
                self.handleGenerate(call: call, result: result)
            case "dispose":
                self.handleDispose(result: result)
            default:
                result(FlutterMethodNotImplemented)
            }
        }

        GeneratedPluginRegistrant.register(with: self)
        return super.application(application, didFinishLaunchingWithOptions: launchOptions)
    }

    // MARK: - Initialize

    private func handleInitialize(call: FlutterMethodCall, result: @escaping FlutterResult) {
        inferenceQueue.async { [weak self] in
            guard let self = self else { return }

            let args = call.arguments as? [String: Any] ?? [:]
            let modelPath = args["modelPath"] as? String ?? "models/ura-gemma-2b-q4_k_m.gguf"
            let maxTokens = args["maxTokens"] as? Int ?? 512
            let temperature = args["temperature"] as? Double ?? 0.2
            let topP = args["topP"] as? Double ?? 0.95

            // Resolve model path from bundle
            guard let resolvedPath = self.resolveModelPath(modelPath) else {
                DispatchQueue.main.async {
                    result([
                        "success": false,
                        "reason": "Model file not found: \(modelPath)"
                    ])
                }
                return
            }

            do {
                let options = LlmInference.Options(modelPath: resolvedPath)
                options.maxTokens = maxTokens
                options.temperature = Float(temperature)
                options.topP = Float(topP)

                self.llmInference = try LlmInference(options: options)

                let fileSize = (try? FileManager.default
                    .attributesOfItem(atPath: resolvedPath)[.size] as? Int64) ?? 0
                let sizeMb = fileSize / (1024 * 1024)

                DispatchQueue.main.async {
                    result([
                        "success": true,
                        "modelSizeMb": sizeMb
                    ])
                }
            } catch {
                NSLog("[OnDeviceLlm] Init failed: \(error.localizedDescription)")
                DispatchQueue.main.async {
                    result([
                        "success": false,
                        "reason": error.localizedDescription
                    ])
                }
            }
        }
    }

    // MARK: - Generate

    private func handleGenerate(call: FlutterMethodCall, result: @escaping FlutterResult) {
        guard let inference = llmInference else {
            result(FlutterError(
                code: "NOT_INITIALIZED",
                message: "LLM not initialized",
                details: nil
            ))
            return
        }

        let args = call.arguments as? [String: Any] ?? [:]
        let prompt = args["prompt"] as? String ?? ""

        guard !prompt.isEmpty else {
            result(FlutterError(
                code: "INVALID_PROMPT",
                message: "Prompt cannot be empty",
                details: nil
            ))
            return
        }

        inferenceQueue.async {
            do {
                let startTime = CFAbsoluteTimeGetCurrent()
                let response = try inference.generateResponse(inputText: prompt)
                let latencyMs = (CFAbsoluteTimeGetCurrent() - startTime) * 1000

                // Rough token estimate (~4 chars per token)
                let tokensGenerated = (response.count + 3) / 4

                DispatchQueue.main.async {
                    result([
                        "text": response,
                        "tokensGenerated": tokensGenerated,
                        "latencyMs": latencyMs
                    ])
                }
            } catch {
                NSLog("[OnDeviceLlm] Generate failed: \(error.localizedDescription)")
                DispatchQueue.main.async {
                    result(FlutterError(
                        code: "GENERATION_FAILED",
                        message: error.localizedDescription,
                        details: nil
                    ))
                }
            }
        }
    }

    // MARK: - Dispose

    private func handleDispose(result: @escaping FlutterResult) {
        // Deallocate on inference queue to avoid blocking main thread with ~1.5 GB free
        inferenceQueue.async { [weak self] in
            self?.llmInference = nil
            DispatchQueue.main.async { result(nil) }
        }
    }

    // MARK: - Helpers

    /// Resolve a relative model path to an absolute path in the app bundle.
    private func resolveModelPath(_ relativePath: String) -> String? {
        // Check app's Documents directory first (for sideloaded models)
        let docsDir = FileManager.default.urls(
            for: .documentDirectory, in: .userDomainMask
        ).first
        if let docsPath = docsDir?.appendingPathComponent(relativePath).path,
           FileManager.default.fileExists(atPath: docsPath) {
            return docsPath
        }

        // Check the main bundle
        let filename = (relativePath as NSString).lastPathComponent
        let name = (filename as NSString).deletingPathExtension
        let ext = (filename as NSString).pathExtension
        if let bundlePath = Bundle.main.path(forResource: name, ofType: ext) {
            return bundlePath
        }

        return nil
    }
}
