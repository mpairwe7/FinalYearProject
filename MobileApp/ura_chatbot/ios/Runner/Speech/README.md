# sherpa-onnx iOS Integration (2026)

This directory is the iOS counterpart of `android/.../cpp/sherpa/`.
Dart talks to `SherpaChannel` which dispatches to a Swift bridge that
wraps the sherpa-onnx Objective-C framework.

## Integration steps

1. **Add the pod** to `ios/Podfile`:
   ```ruby
   pod 'sherpa-onnx', '~> 1.11'
   ```
   (Upstream: https://github.com/k2-fsa/sherpa-onnx — Apache-2.0, ships
   pre-built XCFramework for iOS.)

2. **Register the MethodChannel** in `AppDelegate.swift`:
   ```swift
   import Flutter
   import UIKit
   import sherpa_onnx

   @UIApplicationMain
   @objc class AppDelegate: FlutterAppDelegate {
       override func application(
           _ application: UIApplication,
           didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?
       ) -> Bool {
           GeneratedPluginRegistrant.register(with: self)

           let controller = window?.rootViewController as! FlutterViewController
           let channel = FlutterMethodChannel(
               name: "app.ura.sherpa_onnx",
               binaryMessenger: controller.binaryMessenger
           )
           channel.setMethodCallHandler(SherpaOnnxBridge.shared.handle)
           return super.application(application, didFinishLaunchingWithOptions: launchOptions)
       }
   }
   ```

3. **Load assets** in `SherpaOnnxBridge`:
   ```swift
   let asrEnConfig = SherpaOnnxOfflineRecognizerConfig(
       featConfig: SherpaOnnxFeatureConfig(sampleRate: 16000, featureDim: 80),
       modelConfig: SherpaOnnxOfflineModelConfig(
           whisper: SherpaOnnxOfflineWhisperModelConfig(
               encoder: Bundle.main.path(forResource: "encoder.int8", ofType: "onnx", inDirectory: "speech/asr/whisper-small-en")!,
               decoder: Bundle.main.path(forResource: "decoder.int8", ofType: "onnx", inDirectory: "speech/asr/whisper-small-en")!
           ),
           tokens: Bundle.main.path(forResource: "tokens", ofType: "txt", inDirectory: "speech/asr/whisper-small-en")!,
           numThreads: 2
       )
   )
   ```

4. **Assets** are deployed by
   `ml/scripts/speech/export_mobile_speech.py` into
   `ios/Runner/speech/{asr,tts,mt}/<component_id>/`.
   Each component subdirectory must be **added to the Xcode project as a
   folder reference (blue folder)**, otherwise Xcode does not bundle the
   nested files. Drag the `speech/` folder into the Xcode navigator and
   choose "Create folder references" in the dialog.

## Dart channel contract

Identical to the Android side — see
`lib/core/speech/sherpa_channel.dart` and
`android/app/src/main/cpp/sherpa/README.md` for the full method table.

## Status

**Scaffolded.** The Dart layer is runnable; the Swift bridge ships in a
follow-up task. Until then, `SherpaChannel` methods return mock values
so the Flutter app builds and runs normally.
