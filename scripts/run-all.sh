#!/usr/bin/env bash

# Exit on error
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/utils/log_lib.sh
source "$SCRIPT_DIR/utils/log_lib.sh"

# Base directory
BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$BASE_DIR"

log_section "          PronzeOS Framework Build & Test Suite         " 58

# Create log directories
mkdir -p logs

# 1. Build C SDK (Shared Library)
log_step "1/6 Compiling C SDK Shared Library (libpronze.so)"
gcc -O2 -fPIC -shared -I"$BASE_DIR/sdk/c/include" "$BASE_DIR/sdk/c/src/pronze.c" -o "$BASE_DIR/sdk/c/src/libpronze.so"
log_success "Done: sdk/c/src/libpronze.so"

# 2. Build Rust Runtime Daemon (pronzed)
if command -v cargo &> /dev/null; then
    log_step "2/6 Building Rust Runtime Daemon (pronzed)"
    cargo build --manifest-path "$BASE_DIR/daemon/Cargo.toml"
    log_success "Done: daemon/target/debug/pronzed"
else
    log_info "cargo not found. Skipping daemon compilation."
fi

# 3. Build C test (Allocation failure)
log_step "3/6 Compiling C Allocation Failure Test"
gcc -O2 -I"$BASE_DIR/sdk/c/include" "$BASE_DIR/test/test_alloc.c" -L"$BASE_DIR/sdk/c/src" -lpronze -Wl,-rpath,"$BASE_DIR/sdk/c/src" -o "$BASE_DIR/test/test_alloc"
log_success "Done: test/test_alloc"

# 4. Build C++ test (Container bounds checking)
if command -v g++ &> /dev/null; then
    log_step "4/6 Compiling C++ Container Bounds checking Test"
    g++ -O2 -I"$BASE_DIR/sdk/c/include" -I"$BASE_DIR/sdk/cpp/include" "$BASE_DIR/test/test_bounds.cpp" -L"$BASE_DIR/sdk/c/src" -lpronze -Wl,-rpath,"$BASE_DIR/sdk/c/src" -o "$BASE_DIR/test/test_bounds"
    log_success "Done: test/test_bounds"
else
    log_info "g++ not found. Skipping C++ test compilation."
fi

# 5. Build Rust SDK test
if command -v cargo &> /dev/null; then
    log_step "5/6 Compiling Rust FFI Binding Test"
    # Set linker search path to sdk/c/src for cargo build
    export RUSTFLAGS="-L $BASE_DIR/sdk/c/src"
    cargo build --manifest-path "$BASE_DIR/test/test_rust/Cargo.toml"
    log_success "Done: test/test_rust/target/debug/test_rust"
else
    log_info "cargo not found. Skipping Rust SDK test."
fi

# 6. Build Zig SDK test
if command -v zig &> /dev/null; then
    log_step "6/6 Compiling Zig SDK Binding Test"
    # Compile Zig test, incorporating C sources directly to skip linker hassles
    zig build-exe "$BASE_DIR/test/test_zig.zig" "$BASE_DIR/sdk/c/src/pronze.c" -I "$BASE_DIR/sdk/c/include" --library c --name "$BASE_DIR/test/test_zig"
    log_success "Done: test/test_zig"
else
    log_info "zig not found. Skipping Zig SDK test."
fi

# Create default profile if not exists
mkdir -p profiles
if [ ! -f profiles/default.mfs ]; then
    cat <<EOF > profiles/default.mfs
{
  "allocation_failure_rate": 2,
  "fragmentation": true,
  "latency_ms": 5,
  "guard_pages": true,
  "corruption_rate": 0.01
}
EOF
fi

# Run daemon in the background (simulation)
if command -v cargo &> /dev/null; then
    log_step "Starting pronzed daemon in simulation mode"
    # Kill any existing daemon
    pkill -f pronzed || true
    "$BASE_DIR/daemon/target/debug/pronzed" &> logs/pronzed.log &
    DAEMON_PID=$!
    # Give it a second to bind
    sleep 1.5
    log_success "Daemon started in background (PID: $DAEMON_PID, logs/pronzed.log)"
fi

# Executing Tests
log_section "                   Running Verification Tests             " 58

FAILED=0

# Run C Test
log_step "Test 1: Running C Allocation Failure Test"
if "$BASE_DIR/test/test_alloc"; then
    log_success "C Test Passed!"
else
    log_error "C Test Failed!"
    FAILED=$((FAILED + 1))
fi

# Run C++ Test
if [ -f "$BASE_DIR/test/test_bounds" ]; then
    log_step "Test 2: Running C++ Container Bounds verification Test"
    if "$BASE_DIR/test/test_bounds"; then
        log_success "C++ Test Passed!"
    else
        log_error "C++ Test Failed!"
        FAILED=$((FAILED + 1))
    fi
fi

# Run Rust Test
if [ -f "$BASE_DIR/test/test_rust/target/debug/test_rust" ]; then
    log_step "Test 3: Running Rust SDK FFI Test"
    export LD_LIBRARY_PATH="$BASE_DIR/sdk/c/src:$LD_LIBRARY_PATH"
    export DYLD_LIBRARY_PATH="$BASE_DIR/sdk/c/src:$DYLD_LIBRARY_PATH" # macOS fallback
    if "$BASE_DIR/test/test_rust/target/debug/test_rust"; then
        log_success "Rust Test Passed!"
    else
        log_error "Rust Test Failed!"
        FAILED=$((FAILED + 1))
    fi
fi

# Run Zig Test
if [ -f "$BASE_DIR/test/test_zig" ]; then
    log_step "Test 4: Running Zig SDK Test"
    if "$BASE_DIR/test/test_zig"; then
        log_success "Zig Test Passed!"
    else
        log_error "Zig Test Failed!"
        FAILED=$((FAILED + 1))
    fi
fi

# Clean up daemon
if [ ! -z "$DAEMON_PID" ]; then
    log_step "Stopping background daemon"
    kill $DAEMON_PID || true
    wait $DAEMON_PID 2>/dev/null || true
    log_success "Daemon stopped."
fi

log_plain "=========================================================="
if [ $FAILED -eq 0 ]; then
    log_success "       ALL VERIFICATION TESTS COMPLETED SUCCESSFULLY!      "
else
    log_warn "       VERIFICATION TESTS COMPLETED WITH $FAILED FAILURES! "
fi
log_plain "=========================================================="

exit $FAILED
