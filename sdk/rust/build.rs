fn main() {
    cc::Build::new()
        .file("src/../../c/src/pmf.c")
        .include("src/../../c/include")
        .compile("pronmemf");
    
    println!("cargo:rerun-if-changed=src/../../c/src/pmf.c");
    println!("cargo:rerun-if-changed=build.rs");
}
