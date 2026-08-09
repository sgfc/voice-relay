// SPDX-License-Identifier: Apache-2.0
#include "base/source/fstreamer.h"
#include "pluginterfaces/base/fstrdefs.h"
#include "pluginterfaces/vst/ivstaudioprocessor.h"
#include "pluginterfaces/vst/ivstcomponent.h"
#include "pluginterfaces/vst/vstspeaker.h"
#include "public.sdk/source/common/memorystream.h"
#include "public.sdk/source/vst/hosting/hostclasses.h"
#include "public.sdk/source/vst/hosting/module.h"
#include "public.sdk/source/vst/hosting/plugprovider.h"
#include "public.sdk/source/vst/hosting/processdata.h"
#include "public.sdk/source/vst/utility/stringconvert.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <filesystem>
#ifdef _WIN32
#include <fcntl.h>
#endif
#include <iostream>
#ifdef _WIN32
#include <io.h>
#endif
#include <memory>
#include <string>
#include <vector>

namespace Steinberg {
FUnknown* gStandardPluginContext = new Vst::HostApplication();
}

namespace {
using Steinberg::FUnknownPtr;
using Steinberg::IPtr;
using Steinberg::MemoryStream;
using Steinberg::Vst::HostApplication;
using Steinberg::Vst::HostProcessData;
using Steinberg::Vst::IAudioProcessor;
using Steinberg::Vst::IComponent;
using Steinberg::Vst::PlugProvider;
using Steinberg::Vst::ProcessSetup;
using Steinberg::Vst::SpeakerArrangement;
namespace SpeakerArr = Steinberg::Vst::SpeakerArr;

constexpr std::uint32_t kMaximumFrameSamples = 4096;
constexpr std::uint32_t kDefaultBlockSamples = 480;

struct Arguments {
  std::string plugin;
  std::string model;
  int voice = 0;
  double pitch_shift = 0.0;
  double formant_shift = 0.0;
  double input_gain = 0.0;
  double output_gain = 0.0;
  double intonation = 1.0;
  double pitch_correction = 0.0;
  int pitch_correction_type = 0;
  double sample_rate = 48000.0;
  std::uint32_t block_samples = kDefaultBlockSamples;
};

#ifdef _WIN32
std::string utf8(const wchar_t* value) {
  return Steinberg::Vst::StringConvert::convert(Steinberg::wscast(value));
}
#endif

std::string utf8(const char* value) {
  return value;
}

std::filesystem::path local_path(const std::string& value) {
#ifdef _WIN32
  return std::filesystem::u8path(value);
#else
  return std::filesystem::path(value);
#endif
}

template <typename Character>
Arguments parse_arguments(int argc, Character** argv) {
  Arguments result;
  for (int index = 1; index < argc; ++index) {
    const auto key = utf8(argv[index]);
    if (index + 1 >= argc) throw std::runtime_error("missing value after " + key);
    const auto value = utf8(argv[++index]);
    if (key == "--plugin") result.plugin = value;
    else if (key == "--model") result.model = value;
    else if (key == "--voice") result.voice = std::stoi(value);
    else if (key == "--pitch-shift") result.pitch_shift = std::stod(value);
    else if (key == "--formant-shift") result.formant_shift = std::stod(value);
    else if (key == "--input-gain") result.input_gain = std::stod(value);
    else if (key == "--output-gain") result.output_gain = std::stod(value);
    else if (key == "--intonation") result.intonation = std::stod(value);
    else if (key == "--pitch-correction") result.pitch_correction = std::stod(value);
    else if (key == "--pitch-correction-type") result.pitch_correction_type = std::stoi(value);
    else if (key == "--sample-rate") result.sample_rate = std::stod(value);
    else if (key == "--block-samples") result.block_samples = static_cast<std::uint32_t>(std::stoul(value));
    else throw std::runtime_error("unknown argument: " + key);
  }
  if (result.plugin.empty() || result.model.empty()) throw std::runtime_error("--plugin and --model are required");
  if (!std::filesystem::exists(local_path(result.plugin))) throw std::runtime_error("VST3 plugin was not found: " + result.plugin);
  if (!std::filesystem::exists(local_path(result.model))) throw std::runtime_error("Beatrice model TOML was not found: " + result.model);
  if (result.voice < 0 || result.voice > 999) throw std::runtime_error("voice must be between 0 and 999");
  if (!std::isfinite(result.pitch_shift) || result.pitch_shift < -24.0 || result.pitch_shift > 24.0) throw std::runtime_error("pitch shift must be between -24 and 24");
  if (!std::isfinite(result.formant_shift) || result.formant_shift < -2.0 || result.formant_shift > 2.0) throw std::runtime_error("formant shift must be between -2 and 2");
  if (!std::isfinite(result.input_gain) || result.input_gain < -60.0 || result.input_gain > 20.0) throw std::runtime_error("input gain must be between -60 and 20");
  if (!std::isfinite(result.output_gain) || result.output_gain < -60.0 || result.output_gain > 20.0) throw std::runtime_error("output gain must be between -60 and 20");
  if (!std::isfinite(result.intonation) || result.intonation < -1.0 || result.intonation > 3.0) throw std::runtime_error("intonation must be between -1 and 3");
  if (!std::isfinite(result.pitch_correction) || result.pitch_correction < 0.0 || result.pitch_correction > 1.0) throw std::runtime_error("pitch correction must be between 0 and 1");
  if (result.pitch_correction_type < 0 || result.pitch_correction_type > 1) throw std::runtime_error("pitch correction type must be 0 or 1");
  if (result.sample_rate < 16000 || result.sample_rate > 192000) throw std::runtime_error("unsupported sample rate");
  if (!result.block_samples || result.block_samples > kMaximumFrameSamples) throw std::runtime_error("unsupported block size");
  return result;
}

template <typename T>
void append_value(std::vector<std::uint8_t>& target, const T& value) {
  const auto* bytes = reinterpret_cast<const std::uint8_t*>(&value);
  target.insert(target.end(), bytes, bytes + sizeof(T));
}

void append_integer_parameter(std::vector<std::uint8_t>& target, std::int16_t id, std::int32_t value) {
  constexpr std::int32_t integer_variant = 0;
  append_value(target, id);
  append_value(target, integer_variant);
  append_value(target, value);
}

void append_double_parameter(std::vector<std::uint8_t>& target, std::int16_t id, double value) {
  constexpr std::int32_t double_variant = 1;
  append_value(target, id);
  append_value(target, double_variant);
  append_value(target, value);
}

std::vector<std::uint8_t> make_component_state(const Arguments& args) {
  // Beatrice's public VST source serializes a ParameterState map as:
  // int16 id, int32 variant index, then the variant value. The component
  // wraps that byte sequence with one int32 length.
  std::vector<std::uint8_t> inner;
  const std::int16_t model_id = 1;
  const std::int32_t string_variant = 2;
  const std::int32_t model_size = static_cast<std::int32_t>(args.model.size());
  append_value(inner, model_id);
  append_value(inner, string_variant);
  append_value(inner, model_size);
  inner.insert(inner.end(), args.model.begin(), args.model.end());

  // IDs and ranges follow Beatrice VST's public parameter_schema.cc.
  append_integer_parameter(inner, 2, args.voice);
  append_double_parameter(inner, 3, args.formant_shift);
  append_double_parameter(inner, 4, args.pitch_shift);
  append_double_parameter(inner, 7, args.input_gain);
  append_double_parameter(inner, 8, args.output_gain);
  append_double_parameter(inner, 9, args.intonation);
  append_double_parameter(inner, 10, args.pitch_correction);
  append_integer_parameter(inner, 11, args.pitch_correction_type);

  std::vector<std::uint8_t> state;
  const auto inner_size = static_cast<std::int32_t>(inner.size());
  append_value(state, inner_size);
  state.insert(state.end(), inner.begin(), inner.end());
  return state;
}

bool read_exact(std::istream& stream, void* destination, std::size_t bytes) {
  stream.read(static_cast<char*>(destination), static_cast<std::streamsize>(bytes));
  return stream.gcount() == static_cast<std::streamsize>(bytes);
}

bool write_exact(std::ostream& stream, const void* source, std::size_t bytes) {
  stream.write(static_cast<const char*>(source), static_cast<std::streamsize>(bytes));
  stream.flush();
  return stream.good();
}

class BeatriceHost {
 public:
  explicit BeatriceHost(const Arguments& args) : args_(args) {
    PlugProvider::setErrorStream(&std::cerr);
    Steinberg::Vst::PluginContextFactory::instance().setPluginContext(Steinberg::gStandardPluginContext);
    std::string error;
    module_ = VST3::Hosting::Module::create(args.plugin, error);
    if (!module_) throw std::runtime_error("could not load Beatrice VST3: " + error);
    auto factory = module_->getFactory();
    for (const auto& info : factory.classInfos()) {
      if (info.category() == kVstAudioEffectClass) {
        provider_ = Steinberg::owned(new PlugProvider(factory, info, true));
        break;
      }
    }
    if (!provider_ || !provider_->initialize()) throw std::runtime_error("Beatrice VST3 audio component could not be initialized");
    component_ = provider_->getComponentPtr();
    processor_ = FUnknownPtr<IAudioProcessor>(component_);
    if (!component_ || !processor_) throw std::runtime_error("Beatrice VST3 has no audio processor");

    auto state_bytes = make_component_state(args);
    MemoryStream state(state_bytes.data(), static_cast<Steinberg::TSize>(state_bytes.size()));
    if (component_->setState(&state) != Steinberg::kResultTrue) throw std::runtime_error("Beatrice model state could not be loaded");

    SpeakerArrangement input = SpeakerArr::kMono;
    SpeakerArrangement output = SpeakerArr::kMono;
    if (processor_->setBusArrangements(&input, 1, &output, 1) != Steinberg::kResultTrue) throw std::runtime_error("Beatrice rejected mono audio buses");
    component_->activateBus(Steinberg::Vst::kAudio, Steinberg::Vst::kInput, 0, true);
    component_->activateBus(Steinberg::Vst::kAudio, Steinberg::Vst::kOutput, 0, true);
    ProcessSetup setup{};
    setup.processMode = Steinberg::Vst::kRealtime;
    setup.symbolicSampleSize = Steinberg::Vst::kSample32;
    setup.maxSamplesPerBlock = static_cast<Steinberg::int32>(args.block_samples);
    setup.sampleRate = args.sample_rate;
    if (processor_->setupProcessing(setup) != Steinberg::kResultTrue) throw std::runtime_error("Beatrice rejected the realtime processing setup");
    if (!process_data_.prepare(*component_, static_cast<Steinberg::int32>(args.block_samples), Steinberg::Vst::kSample32)) throw std::runtime_error("could not allocate Beatrice audio buffers");
    if (component_->setActive(true) != Steinberg::kResultTrue) throw std::runtime_error("could not activate Beatrice");
    // Beatrice inherits AudioEffect::setProcessing, which legally reports
    // kNotImplemented even though process() is ready after setActive(true).
    processor_->setProcessing(true);
    active_ = true;
  }

  ~BeatriceHost() {
    if (processor_ && active_) processor_->setProcessing(false);
    if (component_ && active_) component_->setActive(false);
    process_data_.unprepare();
  }

  std::vector<float> process(const std::vector<float>& samples) {
    std::vector<float> output(samples.size());
    std::size_t offset = 0;
    while (offset < samples.size()) {
      const auto count = std::min<std::size_t>(args_.block_samples, samples.size() - offset);
      process_data_.numSamples = static_cast<Steinberg::int32>(count);
      auto& input_bus = process_data_.inputs[0];
      auto& output_bus = process_data_.outputs[0];
      std::memcpy(input_bus.channelBuffers32[0], samples.data() + offset, count * sizeof(float));
      std::memset(output_bus.channelBuffers32[0], 0, count * sizeof(float));
      input_bus.silenceFlags = std::all_of(samples.begin() + static_cast<std::ptrdiff_t>(offset), samples.begin() + static_cast<std::ptrdiff_t>(offset + count), [](float value) { return value > -1e-7f && value < 1e-7f; }) ? 1U : 0U;
      output_bus.silenceFlags = 0;
      if (processor_->process(process_data_) != Steinberg::kResultTrue) throw std::runtime_error("Beatrice processing failed");
      std::memcpy(output.data() + offset, output_bus.channelBuffers32[0], count * sizeof(float));
      offset += count;
    }
    return output;
  }

  std::uint32_t latency_samples() const { return processor_->getLatencySamples(); }

 private:
  Arguments args_;
  VST3::Hosting::Module::Ptr module_;
  IPtr<PlugProvider> provider_;
  IPtr<IComponent> component_;
  FUnknownPtr<IAudioProcessor> processor_;
  HostProcessData process_data_;
  bool active_ = false;
};
}  // namespace

template <typename Character>
int run_host(int argc, Character** argv) {
#ifdef _WIN32
  // Windows CRT text mode rewrites CR/LF bytes. Float32 PCM and frame headers
  // are a binary protocol, so text mode corrupts real audio after a few blocks.
  if (_setmode(_fileno(stdin), _O_BINARY) == -1 ||
      _setmode(_fileno(stdout), _O_BINARY) == -1) {
    std::cerr << "ERROR could not enable binary standard I/O\n";
    return 1;
  }
#endif
  std::ios::sync_with_stdio(false);
  std::cin.tie(nullptr);
  try {
    const auto args = parse_arguments(argc, argv);
    BeatriceHost host(args);
    std::cerr << "READY " << host.latency_samples() << "\n";
    while (true) {
      std::uint32_t sample_count = 0;
      if (!read_exact(std::cin, &sample_count, sizeof(sample_count))) break;
      if (!sample_count || sample_count > kMaximumFrameSamples) throw std::runtime_error("invalid input frame size");
      std::vector<float> input(sample_count);
      if (!read_exact(std::cin, input.data(), input.size() * sizeof(float))) throw std::runtime_error("truncated input frame");
      const auto output = host.process(input);
      const auto output_count = static_cast<std::uint32_t>(output.size());
      if (!write_exact(std::cout, &output_count, sizeof(output_count)) || !write_exact(std::cout, output.data(), output.size() * sizeof(float))) throw std::runtime_error("could not write output frame");
    }
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "ERROR " << error.what() << "\n";
    return 1;
  }
}

#ifdef _WIN32
int wmain(int argc, wchar_t** argv) {
  return run_host(argc, argv);
}
#else
int main(int argc, char** argv) {
  return run_host(argc, argv);
}
#endif
