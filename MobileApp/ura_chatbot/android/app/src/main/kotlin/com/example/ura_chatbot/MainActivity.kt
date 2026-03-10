package com.example.ura_chatbot

import android.os.Handler
import android.os.Looper
import com.google.mediapipe.tasks.genai.llminference.LlmInference
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel
import java.io.File
import java.util.concurrent.Executors

/**
 * MainActivity with MethodChannel handler for on-device Gemma-2B inference
 * via MediaPipe LLM Inference API (2026).
 *
 * Channel: "com.ura_chatbot/llm_inference"
 * Methods: initialize, generate, dispose
 */
class MainActivity : FlutterActivity() {

    companion object {
        private const val CHANNEL = "com.ura_chatbot/llm_inference"
        private const val TAG = "OnDeviceLlm"
    }

    private var llmInference: LlmInference? = null
    private val executor = Executors.newSingleThreadExecutor()
    private val mainHandler = Handler(Looper.getMainLooper())

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)

        MethodChannel(
            flutterEngine.dartExecutor.binaryMessenger,
            CHANNEL
        ).setMethodCallHandler { call, result ->
            when (call.method) {
                "initialize" -> handleInitialize(call, result)
                "generate" -> handleGenerate(call, result)
                "dispose" -> handleDispose(result)
                else -> result.notImplemented()
            }
        }
    }

    /**
     * Initialize the MediaPipe LLM Inference engine with the bundled GGUF model.
     *
     * Expects args: modelPath (String), maxTokens (Int), temperature (Double),
     *               topP (Double), contextLength (Int)
     */
    private fun handleInitialize(call: MethodCall, result: MethodChannel.Result) {
        executor.execute {
            try {
                val modelPath = call.argument<String>("modelPath")
                    ?: "models/ura-gemma-2b-q4_k_m.gguf"
                val maxTokens = call.argument<Int>("maxTokens") ?: 256
                val temperature = call.argument<Double>("temperature") ?: 0.2
                val topP = call.argument<Double>("topP") ?: 0.95

                // Resolve model from app assets
                val assetPath = resolveAssetPath(modelPath)
                if (assetPath == null) {
                    mainHandler.post {
                        result.success(
                            mapOf(
                                "success" to false,
                                "reason" to "Model file not found: $modelPath"
                            )
                        )
                    }
                    return@execute
                }

                val modelFile = File(assetPath)
                val modelSizeMb = modelFile.length() / (1024 * 1024)

                // Build LlmInference options
                val options = LlmInference.LlmInferenceOptions.builder()
                    .setModelPath(assetPath)
                    .setMaxTokens(maxTokens)
                    .setTemperature(temperature.toFloat())
                    .setTopP(topP.toFloat())
                    .build()

                llmInference = LlmInference.createFromOptions(context, options)

                mainHandler.post {
                    result.success(
                        mapOf(
                            "success" to true,
                            "modelSizeMb" to modelSizeMb
                        )
                    )
                }
            } catch (e: Exception) {
                android.util.Log.e(TAG, "Failed to initialize LLM", e)
                mainHandler.post {
                    result.success(
                        mapOf(
                            "success" to false,
                            "reason" to (e.message ?: "Unknown error")
                        )
                    )
                }
            }
        }
    }

    /**
     * Generate a response from the on-device LLM.
     *
     * Expects args: prompt (String), maxTokens (Int), temperature (Double), topP (Double)
     */
    private fun handleGenerate(call: MethodCall, result: MethodChannel.Result) {
        val inference = llmInference
        if (inference == null) {
            result.error("NOT_INITIALIZED", "LLM not initialized", null)
            return
        }

        val prompt = call.argument<String>("prompt") ?: ""
        if (prompt.isEmpty()) {
            result.error("INVALID_PROMPT", "Prompt cannot be empty", null)
            return
        }

        executor.execute {
            try {
                val startMs = System.currentTimeMillis()
                val response = inference.generateResponse(prompt)
                val latencyMs = System.currentTimeMillis() - startMs

                // Estimate tokens (rough: ~4 chars per token)
                val tokensGenerated = (response.length + 3) / 4

                mainHandler.post {
                    result.success(
                        mapOf(
                            "text" to response,
                            "tokensGenerated" to tokensGenerated,
                            "latencyMs" to latencyMs.toDouble()
                        )
                    )
                }
            } catch (e: Exception) {
                android.util.Log.e(TAG, "Generation failed", e)
                mainHandler.post {
                    result.error("GENERATION_FAILED", e.message, null)
                }
            }
        }
    }

    /**
     * Release on-device model resources.
     */
    private fun handleDispose(result: MethodChannel.Result) {
        try {
            llmInference?.close()
            llmInference = null
            result.success(null)
        } catch (e: Exception) {
            result.error("DISPOSE_FAILED", e.message, null)
        }
    }

    /**
     * Resolve an asset-relative model path to an absolute file path.
     *
     * For bundled assets, copies from APK assets to the app's files directory
     * on first access (GGUF files are too large for direct asset streaming).
     */
    private fun resolveAssetPath(assetRelative: String): String? {
        // First check if already extracted to files dir
        val filesDir = File(context.filesDir, assetRelative)
        if (filesDir.exists()) return filesDir.absolutePath

        // Try to extract from APK assets
        return try {
            val inputStream = context.assets.open(assetRelative)
            filesDir.parentFile?.mkdirs()
            inputStream.use { input ->
                filesDir.outputStream().use { output ->
                    input.copyTo(output, bufferSize = 1024 * 1024) // 1 MB buffer for large GGUF
                }
            }
            filesDir.absolutePath
        } catch (e: Exception) {
            // Model not bundled in APK assets — expected in dev builds
            android.util.Log.d(TAG, "Asset not found: $assetRelative")
            null
        }
    }

    override fun onDestroy() {
        llmInference?.close()
        llmInference = null
        executor.shutdown()
        super.onDestroy()
    }
}
