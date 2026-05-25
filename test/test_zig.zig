const std = @import("std");
const pronmf = @import("pronmf.zig");

pub fn main() !void {
    const stdout = std.io.getStdOut().writer();
    try stdout.print("[+] Starting Zig SDK verification test...\n", .{});
    
    var mf = try pronmf.PronMF.init();
    defer mf.deinit();
    
    // 1. Allocate block
    const ptr = mf.malloc(512) orelse {
        try stdout.print("[-] Error: Failed to allocate memory block inside container pool\n", .{});
        std.process.exit(1);
    };
    defer mf.free(ptr);
    
    // 2. Verify valid access
    const status_valid = mf.simulateAccess(ptr);
    try stdout.print("[+] Zig valid access test: status = {} (Expected: 0)\n", .{status_valid});
    if (status_valid != 0) {
        try stdout.print("[-] Error: Valid pointer access verification failed\n", .{});
        std.process.exit(1);
    }
    
    // 3. Verify critical sandbox violation
    const invalid_ptr = @as(?*anyopaque, @ptrFromInt(0xBAADF00D));
    const status_invalid = mf.simulateAccess(invalid_ptr);
    try stdout.print("[+] Zig invalid access test: status = {} (Expected: -2)\n", .{status_invalid});
    if (status_invalid != -2) {
        try stdout.print("[-] Error: Out-of-sandbox critical fault check failed\n", .{});
        std.process.exit(1);
    }
    
    try stdout.print("[+] Verification SUCCESS: All Zig SDK tests passed.\n", .{});
}
