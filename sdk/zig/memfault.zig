const std = @import("std");

pub const MFContext = extern struct {
    fd: c_int,
    failure_rate: c_int,
    simulation_mode: c_int,
    sim_outer_space: ?*anyopaque,
    sim_outer_size: usize,
    sim_inner_space: ?*anyopaque,
    sim_inner_size: usize,
    sim_offset: usize,
};

pub extern fn mfInit(ctx: *MFContext) c_int;
pub extern fn mfLoadProfile(ctx: *MFContext, profile_path: [*:0]const u8) c_int;
pub extern fn mfStartProfiling(ctx: *MFContext) c_int;
pub extern fn mf_malloc(ctx: *MFContext, size: usize) ?*anyopaque;
pub extern fn mf_free(ctx: *MFContext, ptr: ?*anyopaque) void;
pub extern fn mfShutdown(ctx: *MFContext) c_int;
pub extern fn mfEnableAllocationFailure(ctx: *MFContext, failure_rate: c_int) c_int;
pub extern fn mfSimulateAccess(ctx: *MFContext, ptr: ?*anyopaque) c_int;

pub const MemFault = struct {
    ctx: MFContext,

    pub fn init() !MemFault {
        var self = MemFault{
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
        if (mfInit(&self.ctx) != 0) {
            return error.MemFaultInitFailed;
        }
        return self;
    }

    pub fn deinit(self: *MemFault) void {
        _ = mfShutdown(&self.ctx);
    }

    pub fn loadProfile(self: *MemFault, path: [*:0]const u8) !void {
        if (mfLoadProfile(&self.ctx, path) != 0) {
            return error.LoadProfileFailed;
        }
    }

    pub fn startProfiling(self: *MemFault) !void {
        if (mfStartProfiling(&self.ctx) != 0) {
            return error.StartProfilingFailed;
        }
    }

    pub fn enableAllocationFailure(self: *MemFault, failure_rate: i32) !void {
        if (mfEnableAllocationFailure(&self.ctx, @intCast(failure_rate)) != 0) {
            return error.EnableAllocationFailureFailed;
        }
    }

    pub fn malloc(self: *MemFault, size: usize) ?*anyopaque {
        return mf_malloc(&self.ctx, size);
    }

    pub fn free(self: *MemFault, ptr: ?*anyopaque) void {
        mf_free(&self.ctx, ptr);
    }

    pub fn simulateAccess(self: *MemFault, ptr: ?*anyopaque) i32 {
        return mfSimulateAccess(&self.ctx, ptr);
    }
};
