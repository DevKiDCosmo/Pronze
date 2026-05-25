use std::os::raw::{c_char, c_int, c_void};
use std::ffi::CString;
use std::ptr;

// Replicate the C PMFContext struct layout
#[repr(C)]
#[derive(Debug)]
pub struct PMFContext {
    pub fd: c_int,
    pub failure_rate: c_int,
    pub simulation_mode: c_int,
    pub sim_outer_space: *mut c_void,
    pub sim_outer_size: usize,
    pub sim_inner_space: *mut c_void,
    pub sim_inner_size: usize,
    pub sim_offset: usize,
}

impl Default for PMFContext {
    fn default() -> Self {
        PMFContext {
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
    pub fn pmfInit(ctx: *mut PMFContext) -> c_int;
    pub fn pmfLoadProfile(ctx: *mut PMFContext, profile_path: *const c_char) -> c_int;
    pub fn pmfStartProfiling(ctx: *mut PMFContext) -> c_int;
    pub fn pmf_malloc(ctx: *mut PMFContext, size: usize) -> *mut c_void;
    pub fn pmf_free(ctx: *mut PMFContext, ptr: *mut c_void);
    pub fn pmfShutdown(ctx: *mut PMFContext) -> c_int;
    pub fn pmfEnableAllocationFailure(ctx: *mut PMFContext, failure_rate: c_int) -> c_int;
    pub fn pmfSimulateAccess(ctx: *mut PMFContext, ptr: *mut c_void) -> c_int;
}

pub struct PronMF {
    ctx: Box<PMFContext>,
}

impl PronMF {
    /// Initialize the PronMF engine
    pub fn new() -> Result<Self, &'static str> {
        let mut ctx = Box::new(PMFContext::default());
        unsafe {
            if pmfInit(&mut *ctx) == 0 {
                Ok(PronMF { ctx })
            } else {
                Err("Failed to initialize Pron MF Context")
            }
        }
    }

    /// Load a fault profile from disk
    pub fn load_profile(&mut self, path: &str) -> Result<(), &'static str> {
        let c_path = CString::new(path).map_err(|_| "Invalid profile path string")?;
        unsafe {
            if pmfLoadProfile(&mut *self.ctx, c_path.as_ptr()) == 0 {
                Ok(())
            } else {
                Err("Failed to load fault profile")
            }
        }
    }

    /// Start trace collection
    pub fn start_profiling(&mut self) -> Result<(), &'static str> {
        unsafe {
            if pmfStartProfiling(&mut *self.ctx) == 0 {
                Ok(())
            } else {
                Err("Failed to start profiling")
            }
        }
    }

    /// Explicitly override the allocation failure rate (0 to 100)
    pub fn enable_allocation_failure(&mut self, failure_rate: i32) -> Result<(), &'static str> {
        unsafe {
            if pmfEnableAllocationFailure(&mut *self.ctx, failure_rate) == 0 {
                Ok(())
            } else {
                Err("Failed to enable allocation failure")
            }
        }
    }

    /// Allocate fault-instrumented memory
    pub fn malloc(&mut self, size: usize) -> *mut c_void {
        unsafe { pmf_malloc(&mut *self.ctx, size) }
    }

    /// Free allocated memory block
    pub fn free(&mut self, ptr: *mut c_void) {
        unsafe { pmf_free(&mut *self.ctx, ptr) }
    }

    /// Simulate memory access verification (telemetry bounds checks)
    pub fn simulate_access(&mut self, ptr: *mut c_void) -> i32 {
        unsafe { pmfSimulateAccess(&mut *self.ctx, ptr) }
    }
}

impl Drop for PronMF {
    fn drop(&mut self) {
        unsafe {
            pmfShutdown(&mut *self.ctx);
        }
    }
}
