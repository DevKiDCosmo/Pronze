# Pron OS Language Support & SDK Distribution Guidelines

This document provides developer guidelines for language support, FFI interfaces, and integration details in the Pron operating system.

## Overview

To maintain minimal complexity and maximize build stability during active kernel and memory validation testing, the Pron OS development suite has been consolidated around a single **universal C ABI shared library** (`libpronmft.so`). 

Cross-language SDKs (specifically Rust and Zig) are simplified to placeholder interfaces that exit cleanly with a "Not Implemented" status. Higher-level bindings will interact with Pron kernel services using C FFI against `libpronmft.so` in future milestones.

---

## Language Support Matrix

| Language | Status | Runtime Mode | Core Mechanism |
|---|---|---|---|
| **C** | **Supported** | Kernel / User Sim | Direct call to `libpronmft.so` / `/dev/pron_mf` |
| **C++** | **Supported** | Kernel / User Sim | Wrapper headers (`pronmf.hpp`) linking to `libpronmft.so` |
| **Rust** | Placeholder | Not Implemented | Dummy crate (`sdk/rust/`) returning `Err("Not Implemented")` |
| **Zig** | Placeholder | Not Implemented | Dummy module (`sdk/zig/pronmf.zig`) returning `error.NotImplemented` |
| **Python** | Planned | Simulation | Ctypes wrapper calling `libpronmft.so` |

---

## Universal Shared Library: `libpronmft.so`

All core services (e.g. allocation tracing, fault-injection hooks, containment simulations) are compiled into the C shared library `libpronmft.so`.

### Linking the Library

To compile programs linking against the universal library:

```bash
# Compilation command matching the distro build pipeline
musl-gcc -O2 -I/usr/include/pmf \
  main.c -lpronmft -o my_app
```

At runtime inside the Pron OS target, ensure `libpronmft.so` is loaded by placing it in `/usr/lib/` or setting `LD_LIBRARY_PATH`.

---

## SDK Integration

### 1. C/C++ SDK
- Header files are located under `sdk/c/include/pmf/pmf.h`.
- Implementation is written in `sdk/c/src/pmf.c`.
- Context configuration uses `PMFContext`.

### 2. Rust SDK Placeholder
- Located in `sdk/rust/`.
- Compiles a static struct returning `NotImplemented` logs. 
- Avoids FFI build dependency chains in early testing phases.

### 3. Zig SDK Placeholder
- Located in `sdk/zig/pronmf.zig`.
- Emits clean `error.NotImplemented` compile/runtime messages.

---

## Technical Support & Verification
All kernel mode telemetry metrics can be monitored via the device file `/dev/pron_telemetrics`.
- **Status check**: `cat /dev/pron_telemetrics`
- **Socket daemon control**: `/tmp/prond.sock`
