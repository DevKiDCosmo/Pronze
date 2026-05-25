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

pub extern fn pmfInit(ctx: *PMFContext) c_int;
pub extern fn pmfLoadProfile(ctx: *PMFContext, profile_path: [*:0]const u8) c_int;
pub extern fn pmfStartProfiling(ctx: *PMFContext) c_int;
pub extern fn pmf_malloc(ctx: *PMFContext, size: usize) ?*anyopaque;
pub extern fn pmf_free(ctx: *PMFContext, ptr: ?*anyopaque) void;
pub extern fn pmfShutdown(ctx: *PMFContext) c_int;
pub extern fn pmfEnableAllocationFailure(ctx: *PMFContext, failure_rate: c_int) c_int;
pub extern fn pmfSimulateAccess(ctx: *PMFContext, ptr: ?*anyopaque) c_int;

pub const PronMF = struct {
    ctx: PMFContext,

    pub fn init() !PronMF {
        var self = PronMF{
            .ctx = .{
                .fd = -1,
                .failure_rate = 0,
                .simulation_mode = 1,
                .sim_outer_space = null,
                .sim_outer_size = 0,
                .sim_inner_space = null,
                .sim_inner_size = 0,
                .sim_offset = 0,
            },
        };
        if (pmfInit(&self.ctx) != 0) {
            return error.PronMFInitFailed;
        }
        return self;
    }

    pub fn deinit(self: *PronMF) void {
        _ = pmfShutdown(&self.ctx);
    }

    pub fn loadProfile(self: *PronMF, path: [*:0]const u8) !void {
        if (pmfLoadProfile(&self.ctx, path) != 0) {
            return error.LoadProfileFailed;
        }
    }

    pub fn startProfiling(self: *PronMF) !void {
        if (pmfStartProfiling(&self.ctx) != 0) {
            return error.StartProfilingFailed;
        }
    }

    pub fn enableAllocationFailure(self: *PronMF, failure_rate: i32) !void {
        if (pmfEnableAllocationFailure(&self.ctx, @intCast(failure_rate)) != 0) {
            return error.EnableAllocationFailureFailed;
        }
    }

    pub fn malloc(self: *PronMF, size: usize) ?*anyopaque {
        return pmf_malloc(&self.ctx, size);
    }

    pub fn free(self: *PronMF, ptr: ?*anyopaque) void {
        pmf_free(&self.ctx, ptr);
    }

    pub fn simulateAccess(self: *PronMF, ptr: ?*anyopaque) i32 {
        return pmfSimulateAccess(&self.ctx, ptr);
    }
};
