# Language Support

C++/C y SDK(kernel inj + sim)

Rust y SDK(kernel inj + sim)

Zig n SDK(kernel inj + sim)

Py y (sim)

C — native ABI, direct .so support
C++ — compatible with C libraries using extern "C"
Rust — excellent FFI support with C
Go — possible via cgo, but less ergonomic
Python — easy to call .so using ctypes or bindings
Zig — very good C interoperability

Also JS can later be through injected Library into Runtime to run ASM/WASM.
