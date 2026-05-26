const std = @import("std");

pub const PMFContext = extern struct {
    fd: c_int,
    failure_rate: c_int,
    simulation_mode: c_int,
    sim_outer_space: ?*anyopaque,
    sim_outer_size: usize,
    sim_inner_space: ?*anyopaque,
    sim_inner_size: usize,
    sim_offset: usize,
};

pub const PronMF = struct {
    pub fn init() !PronMF {
        return error.NotImplemented;
    }
    pub fn deinit(self: *PronMF) void {
        _ = self;
    }
    pub fn loadProfile(self: *PronMF, path: [*:0]const u8) !void {
        _ = self;
        _ = path;
        return error.NotImplemented;
    }
    pub fn startProfiling(self: *PronMF) !void {
        _ = self;
        return error.NotImplemented;
    }
    pub fn enableAllocationFailure(self: *PronMF, failure_rate: i32) !void {
        _ = self;
        _ = failure_rate;
        return error.NotImplemented;
    }
    pub fn malloc(self: *PronMF, size: usize) ?*anyopaque {
        _ = self;
        _ = size;
        return null;
    }
    pub fn free(self: *PronMF, ptr: ?*anyopaque) void {
        _ = self;
        _ = ptr;
    }
    pub fn simulateAccess(self: *PronMF, ptr: ?*anyopaque) i32 {
        _ = self;
        _ = ptr;
        return -1;
    }
};
