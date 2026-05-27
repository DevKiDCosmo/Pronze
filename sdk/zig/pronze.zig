const std = @import("std");

pub const PronzeContext = extern struct {
    fd: c_int,
    failure_rate: c_int,
    simulation_mode: c_int,
    sim_outer_space: ?*anyopaque,
    sim_outer_size: usize,
    sim_inner_space: ?*anyopaque,
    sim_inner_size: usize,
    sim_offset: usize,
};

pub const Pronze = struct {
    pub fn init() !Pronze {
        return error.NotImplemented;
    }
    pub fn deinit(self: *Pronze) void {
        _ = self;
    }
    pub fn loadProfile(self: *Pronze, path: [*:0]const u8) !void {
        _ = self;
        _ = path;
        return error.NotImplemented;
    }
    pub fn startProfiling(self: *Pronze) !void {
        _ = self;
        return error.NotImplemented;
    }
    pub fn enableAllocationFailure(self: *Pronze, failure_rate: i32) !void {
        _ = self;
        _ = failure_rate;
        return error.NotImplemented;
    }
    pub fn malloc(self: *Pronze, size: usize) ?*anyopaque {
        _ = self;
        _ = size;
        return null;
    }
    pub fn free(self: *Pronze, ptr: ?*anyopaque) void {
        _ = self;
        _ = ptr;
    }
    pub fn simulateAccess(self: *Pronze, ptr: ?*anyopaque) i32 {
        _ = self;
        _ = ptr;
        return -1;
    }
};
