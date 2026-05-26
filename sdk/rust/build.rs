fn main() {
    // Re-run if build.rs changes
    println!("cargo:rerun-if-changed=build.rs");
}
