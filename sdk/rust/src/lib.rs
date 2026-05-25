use std::os::raw::{c_char, c_int, c_void};
use std::ffi::CString;
use std::ptr;

// Replicate the C MFContext struct layout
#[repr(C)]
#[derive(Debug)]
pub struct MFContext {
    pub fd: c_int,
    pub failure_rate: c_int,
    pub simulation_mode: c_int,
    pub sim_outer_space: *mut c_void,
    pub sim_outer_size: usize,
    pub sim_inner_space: *mut c_void,
    pub sim_inner_size: usize,
    pub sim_offset: usize,
}

impl Default for MFContext {
    fn default() -> Self {
        MFContext {
            fd: -1,
            failure_rate: 0,
            simulation_mode: 1,
            sim_outer_space: ptr::null_mut(),
            sim_outer_size: 0,
            sim_inner_space: ptr::null_mut(),
            sim_inner_size: 0,
            sim_offset: 0,
        }
    }
}

extern "C" {
    pub fn mfInit(ctx: *mut MFContext) -> c_int;
    pub fn mfLoadProfile(ctx: *mut MFContext, profile_path: *const c_char) -> c_int;
    pub fn mfStartProfiling(ctx: *mut MFContext) -> c_int;
    pub fn mf_malloc(ctx: *mut MFContext, size: usize) -> *mut c_void;
    pub fn mf_free(ctx: *mut MFContext, ptr: *mut c_void);
    pub fn mfShutdown(ctx: *mut MFContext) -> c_int;
    pub fn mfEnableAllocationFailure(ctx: *mut MFContext, failure_rate: c_int) -> c_int;
    pub fn mfSimulateAccess(ctx: *mut MFContext, ptr: *mut c_void) -> c_int;
}

pub struct MemFault {
    ctx: Box<MFContext>,
}

impl MemFault {
    /// Initialize the MemFault engine
    pub fn new() -> Result<Self, &'static str> {
        let mut ctx = Box::new(MFContext::default());
        unsafe {
            if mfInit(&mut *ctx) == 0 {
                Ok(MemFault { ctx })
            } else {
                Err("Failed to initialize MemFault Context")
            }
        }
    }

    /// Load a fault profile from disk
    pub fn load_profile(&mut self, path: &str) -> Result<(), &'static str> {
        let c_path = CString::new(path).map_err(|_| "Invalid profile path string")?;
        unsafe {
            if mfLoadProfile(&mut *self.ctx, c_path.as_ptr()) == 0 {
                Ok(())
            } else {
                Err("Failed to load fault profile")
            }
        }
    }

    /// Start trace collection
    pub fn start_profiling(&mut self) -> Result<(), &'static str> {
        unsafe {
            if mfStartProfiling(&mut *self.ctx) == 0 {
                Ok(())
            } else {
                Err("Failed to start profiling")
            }
        }
    }

    /// Explicitly override the allocation failure rate (0 to 100)
    pub fn enable_allocation_failure(&mut self, failure_rate: i32) -> Result<(), &'static str> {
        unsafe {
            if mfEnableAllocationFailure(&mut *self.ctx, failure_rate) == 0 {
                Ok(())
            } else {
                Err("Failed to enable allocation failure")
            }
        }
    }

    /// Allocate fault-instrumented memory
    pub fn malloc(&mut self, size: usize) -> *mut c_void {
        unsafe { mf_malloc(&mut *self.ctx, size) }
    }

    /// Free allocated memory block
    pub fn free(&mut self, ptr: *mut c_void) {
        unsafe { mf_free(&mut *self.ctx, ptr) }
    }

    /// Simulate memory access verification (telemetry bounds checks)
    pub fn simulate_access(&mut self, ptr: *mut c_void) -> i32 {
        unsafe { mfSimulateAccess(&mut *self.ctx, ptr) }
    }
}

impl Drop for MemFault {
    fn drop(&mut self) {
        unsafe {
            mfShutdown(&mut *self.ctx);
        }
    }
}
