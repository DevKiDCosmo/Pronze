# PronzeOS Language Support & SDK Distribution Guidelines

This document provides developer guidelines for language support, FFI interfaces, and integration details in the Pronze operating system.

## Overview

To maintain minimal complexity and maximize build stability during active kernel and memory validation testing, the PronzeOS development suite has been consolidated around a single **universal C ABI shared library** (`libpronze.so`). 

Cross-language SDKs (specifically Rust and Zig) are simplified to placeholder interfaces that exit cleanly with a "Not Implemented" status. Higher-level bindings will interact with Pronze kernel services using C FFI against `libpronze.so` in future milestones.

---

## Language Support Matrix

| Language | Status | Runtime Mode | Core Mechanism |
|---|---|---|---|
| **C** | **Supported** | Kernel / User Sim | Direct call to `libpronze.so` / `/dev/pronze` |
| **C++** | **Supported** | Kernel / User Sim | Wrapper headers (`pronze.hpp`) linking to `libpronze.so` |
| **Rust** | Placeholder | Not Implemented | Dummy crate (`sdk/rust/`) returning `Err("Not Implemented")` |
| **Zig** | Placeholder | Not Implemented | Dummy module (`sdk/zig/pronze.zig`) returning `error.NotImplemented` |
| **Python** | Planned | Simulation | Ctypes wrapper calling `libpronze.so` |

---

## Universal Shared Library: `libpronze.so`

All core services (e.g. allocation tracing, fault-injection hooks, containment simulations) are compiled into the C shared library `libpronze.so`.

### Linking the Library

To compile programs linking against the universal library:

```bash
# Compilation command matching the distro build pipeline
musl-gcc -O2 -I/usr/include/pronze \
  main.c -lpronze -o my_app
```

At runtime inside the PronzeOS target, ensure `libpronze.so` is loaded by placing it in `/usr/lib/` or setting `LD_LIBRARY_PATH`.

---

## SDK Integration

### 1. C/C++ SDK
- Header files are located under `sdk/c/include/pronze/pronze.h`.
- Implementation is written in `sdk/c/src/pronze.c`.
- Context configuration uses `PronzeContext`.

### 2. Rust SDK Placeholder
- Located in `sdk/rust/`.
- Compiles a static struct returning `NotImplemented` logs. 
- Avoids FFI build dependency chains in early testing phases.

### 3. Zig SDK Placeholder
- Located in `sdk/zig/pronze.zig`.
- Emits clean `error.NotImplemented` compile/runtime messages.

---

## Technical Support & Verification
All kernel mode telemetry metrics can be monitored via the device file `/dev/pronze_telemetry`.
- **Status check**: `cat /dev/pronze_telemetry`
- **Socket daemon control**: `/tmp/pronze.sock`
