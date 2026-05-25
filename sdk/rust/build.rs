fn main() {
    // Tell cargo to tell rustc to link the memfault shared library
    println!("cargo:rustc-link-lib=dylib=memfault");
    
    // Direct rustc to find the library in sdk/c/src
    println!("cargo:rustc-link-search=native=sdk/c/src");
    println!("cargo:rustc-link-search=native=../c/src");
    println!("cargo:rustc-link-search=native=../../sdk/c/src");
    
    // Re-run if build.rs changes
    println!("cargo:rerun-if-changed=build.rs");
}
