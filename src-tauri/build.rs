fn main() {
    println!("cargo:rerun-if-changed=icons");
    println!("cargo:rerun-if-changed=tauri.conf.json");
    println!("cargo:rerun-if-changed=windows-app-manifest.xml");
    println!("cargo:rerun-if-changed=../package.json");
    for key in [
        "PYMSS_BUILD_GIT_COMMIT",
        "PYMSS_BUILD_GIT_TAG",
        "PYMSS_BUILD_GIT_REF",
        "PYMSS_BUILD_RUN_ID",
        "PYMSS_BUILD_RUN_ATTEMPT",
        "PYMSS_BUILD_REPOSITORY",
        "PYMSS_BUILD_REPOSITORY_OWNER",
        "PYMSS_BUILD_TIME",
        "PYMSS_BUILD_TARGET",
        "PYMSS_BUILD_VARIANT",
        "PYMSS_BUILD_UPDATE_SUPPORTED",
        "PYMSS_BUILD_OFFICIAL",
        // Local update fixtures use a separate signing key; make Cargo
        // invalidate the binary whenever that compile-time override changes.
        "PYMSS_UPDATE_PUBLIC_KEY",
    ] {
        println!("cargo:rerun-if-env-changed={key}");
        if let Ok(value) = std::env::var(key) {
            println!("cargo:rustc-env={key}={value}");
        }
    }
    let windows = tauri_build::WindowsAttributes::new()
        .app_manifest(include_str!("windows-app-manifest.xml"));
    let attributes = tauri_build::Attributes::new().windows_attributes(windows);
    tauri_build::try_build(attributes).expect("failed to run Tauri build script")
}
