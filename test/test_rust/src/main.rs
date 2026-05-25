use memfault::MemFault;

fn main() {
    println!("[+] Starting Rust FFI verification test...");
    
    let mut mf = MemFault::new().expect("Failed to initialize MemFault SDK");
    
    // 1. Allocate block
    let ptr = mf.malloc(512);
    if ptr.is_null() {
        panic!("[-] Error: Failed to allocate memory block inside container pool");
    }
    
    // 2. Verify valid access
    let status_valid = mf.simulate_access(ptr);
    println!("[+] Rust valid access test: status = {} (Expected: 0)", status_valid);
    if status_valid != 0 {
        panic!("[-] Error: Valid pointer access verification failed");
    }
    
    // 3. Verify critical sandbox violation
    let invalid_ptr = 0xBAADF00D as *mut std::os::raw::c_void;
    let status_invalid = mf.simulate_access(invalid_ptr);
    println!("[+] Rust invalid access test: status = {} (Expected: -2)", status_invalid);
    if status_invalid != -2 {
        panic!("[-] Error: Out-of-sandbox critical fault check failed");
    }
    
    mf.free(ptr);
    println!("[+] Verification SUCCESS: All Rust SDK tests passed.");
}
