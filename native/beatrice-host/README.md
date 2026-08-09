# CharaDock Beatrice host

This is a minimal headless VST3 host used to process CharaDock's Codex Realtime audio through the official Beatrice 2 VST3 plug-in. It does not contain or redistribute Beatrice, its inference library, or any voice model.

The process receives length-prefixed mono Float32 PCM frames on stdin and returns length-prefixed Float32 PCM frames on stdout. Runtime status and errors are written to stderr.

Build requirements:

- CMake 3.25+
- a Windows C++20 compiler, or Apple Clang on macOS
- the MIT-licensed Steinberg VST3 SDK

### Windows

```powershell
cmake -S native/beatrice-host -B build/beatrice-host -DVST3_SDK_ROOT=C:\src\vst3sdk
cmake --build build/beatrice-host --config Release
```

### macOS arm64

Build on macOS with Xcode Command Line Tools installed. The host target defaults
to arm64 and macOS 13 or later; both values can still be overridden explicitly.

```bash
cmake -S native/beatrice-host -B build/beatrice-host-macos-arm64 \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_OSX_ARCHITECTURES=arm64 \
  -DVST3_SDK_ROOT=/path/to/vst3sdk
cmake --build build/beatrice-host-macos-arm64 --parallel 3
cmake --install build/beatrice-host-macos-arm64 --prefix native/bin
lipo -archs native/bin/charadock-beatrice-host
```

The `CharaDock macOS arm64 experimental package` workflow performs the same
build, places the helper inside CharaDock, and publishes unsigned `.dmg` and
`.zip` workflow artifacts. People using that experimental app package do not
need to install the host separately. The helper is not Beatrice itself and does
not include Beatrice or any voice model.

At runtime pass the installed official `.vst3` package and a compatible model TOML:

```powershell
charadock-beatrice-host.exe --plugin C:\Beatrice\beatrice_2.0.0-rc.2.vst3 --model C:\Beatrice\model\model.toml --voice 0 --pitch-shift 0 --formant-shift 0 --input-gain 0 --output-gain 0 --intonation 1 --pitch-correction 0 --pitch-correction-type 0
```

On macOS use the extensionless `charadock-beatrice-host` executable and pass
the official Beatrice `.vst3` bundle path in the same way.

The optional tuning arguments use the parameter IDs and ranges published by
Beatrice's VST parameter schema. CharaDock supplies all arguments when it starts
the helper, so end users configure these values in the app rather than on the
command line.
