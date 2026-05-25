# PronKern — Framework & Distribution Architecture

PronKern is a deterministic systems engineering and fault-injection Linux distribution. It is designed for compiler testing, runtime verification, and deterministic system behavior analysis.

This repository contains the core software components of the PronKern user-space and kernel interface framework.

---

## High-Level Architecture

```text
       ┌────────────────────────────────────────────────────────┐
       │                   User Application                     │
       └───────────────────────────┬────────────────────────────┘
                                   │
       ┌───────────────────────────▼────────────────────────────┐
       │        MemFault SDK (C / C++ / Rust / Zig API)         │
       └───────────────────────────┬────────────────────────────┘
                                   │
            ┌──────────────────────┴──────────────────────┐
            ▼ (Kernel Available?)                         ▼ (Fallback Simulation)
  ┌──────────────────────────┐               ┌──────────────────────────┐
  │   /dev/memfault Driver   │               │   User-space Container   │
  │     (memfault_core)      │               │   (Memory Sandbox Map)   │
  └─────────────┬────────────┘               └────────────┬─────────────┘
                │                                         │
  ┌─────────────▼────────────┐                            │
  │     memfaultd Daemon     │◄───────────────────────────┘
  │ (IPC socket configuration)│
  └──────────────────────────┘
```

---

## Directory Layout

- [sdk/c/](file:///Users/duynamschlitz/GitProject/PronKern/sdk/c): The primary FFI C SDK (`libmemfault.so`) implementing the allocation hooks and boundary sandbox container.
- [sdk/cpp/](file:///Users/duynamschlitz/GitProject/PronKern/sdk/cpp): RAII C++ wrapper (`memfault.hpp`).
- [sdk/rust/](file:///Users/duynamschlitz/GitProject/PronKern/sdk/rust): Idiomatic Rust crate (`memfault`) wrapping the C ABI.
- [sdk/zig/](file:///Users/duynamschlitz/GitProject/PronKern/sdk/zig): Zig module bindings (`memfault.zig`).
- [daemon/](file:///Users/duynamschlitz/GitProject/PronKern/daemon): Rust runtime daemon (`memfaultd`) handling sockets, configs, and rollback telemetry.
- [kernel/](file:///Users/duynamschlitz/GitProject/PronKern/kernel): Loadable C kernel module (`memfault_core`) exposing `/dev/memfault` controls.
- [profiles/](file:///Users/duynamschlitz/GitProject/PronKern/profiles): Default JSON fault configurations.
- [scripts/](file:///Users/duynamschlitz/GitProject/PronKern/scripts): Kernel compilation, machine setup, and testing orchestration scripts.
- [test/](file:///Users/duynamschlitz/GitProject/PronKern/test): Verification scripts for checking allocation failures and boundary tracking.

---

## SDK Language Integration Examples

PronKern is designed for multi-language systems development. Below is how you can use the SDK in different languages:

### 1. C

```c
#include <memfault/memfault.h>

int main() {
    MFContext ctx;
    mfInit(&ctx);
    mfEnableAllocationFailure(&ctx, 10); // 10% failure rate

    void* ptr = mf_malloc(&ctx, 1024);
    if (!ptr) {
        // Handle injected failure
    }

    mf_free(&ctx, ptr);
    mfShutdown(&ctx);
    return 0;
}
```

### 2. C++

```cpp
#include <memfault/memfault.hpp>

int main() {
    memfault::MemFaultContext mfc;
    mfc.enableAllocationFailure(10);

    void* ptr = mfc.allocate(1024);
    mfc.deallocate(ptr);
    return 0;
}
```

### 3. Rust

```rust
use memfault::MemFault;

fn main() {
    let mut mf = MemFault::new().unwrap();
    mf.enable_allocation_failure(10).unwrap();

    let ptr = mf.malloc(1024);
    mf.free(ptr);
}
```

### 4. Zig

```zig
const std = @import("std");
const memfault = @import("memfault");

pub fn main() !void {
    var mf = try memfault.MemFault.init();
    defer mf.deinit();

    try mf.enableAllocationFailure(10);
    const ptr = mf.malloc(1024);
    if (ptr) |p| {
        mf.free(p);
    }
}
```

---

## Verification & Build Instructions

### Local Compilation and Execution

You can compile all code and execute tests locally using the orchestrator:

```bash
# Compile libraries, daemon, and execute tests across all languages
chmod +x scripts/run-all.sh
./scripts/run-all.sh
```

### Running with Docker

To build and run the verification tests inside a clean, reproducible container:

```bash
# Build the Docker environment
docker build -t PronKern:latest .

# Run the test suite
docker run --rm PronKern:latest


docker run --rm memfaultos:latest > out.log
docker build -t memfaultos:latest .

docker run --rm -v $(pwd)/output:/workspace/output memfaultos-builder
docker run --rm -v "$(pwd):/workspace" -v memfaultos-cache:/opt/memfaultos memfaultos-builder:latest
```

### Dev Container for Linux Kernel Module Development

Open the repository in VS Code and choose **Reopen in Container**. The dev container includes the kernel-module build toolchain, Clang/LLVM, `kmod`, `pahole`, and other common utilities for driver work.

Inside the container, build the module with:

```bash
make -C /lib/modules/$(uname -r)/build M="$PWD/kernel" modules
```

On Linux hosts, the container can build against the host kernel headers if `/lib/modules/$(uname -r)/build` is available. If that path is missing, mount the matching kernel headers or set `KDIR` manually.

### Compiling the Linux Kernel with custom config

To setup and build the LTS Linux Kernel with custom debugging features enabled:

```bash
./scripts/setup.sh
./scripts/build-kernel.sh
```

This enables BPF, Kprobes, Kasan, fault injection, and disables panic-on-oops behavior to ensure the host machine remains safe during fault runs.
