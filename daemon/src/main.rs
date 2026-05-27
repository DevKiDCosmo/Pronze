use std::fs;
use std::path::Path;
use std::sync::Arc;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{UnixListener, UnixStream};
use tokio::sync::Mutex;
use serde::{Deserialize, Serialize};

const SOCKET_PATH: &str = "/tmp/pronze.sock";
const DEFAULT_PROFILE_PATH: &str = "/runtime/profiles/default.mfs";

#[derive(Serialize, Deserialize, Debug, Clone)]
struct FaultProfile {
    allocation_failure_rate: i32,
    fragmentation: bool,
    latency_ms: u64,
    guard_pages: bool,
    #[serde(default)]
    corruption_rate: f64,
}

struct DaemonState {
    current_profile: FaultProfile,
    kernel_driver_active: bool,
    active_partition: String,
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    println!("[+] Starting pronzed (PronzeOS Runtime Daemon)...");
    
    // Load default profile
    let default_profile = load_profile(DEFAULT_PROFILE_PATH).unwrap_or(FaultProfile {
        allocation_failure_rate: 2,
        fragmentation: true,
        latency_ms: 5,
        guard_pages: true,
        corruption_rate: 0.01,
    });
    
    println!("[+] Loaded active fault profile: {:?}", default_profile);
    
    // Check if kernel driver is present
    let driver_present = Path::new("/dev/pronze").exists();
    println!(
        "[+] Checking for Pronze Kernel Module: {}", 
        if driver_present { "Detected (/dev/pronze)" } else { "Not Detected (Running in User-Space Simulator Mode)" }
    );
    
    let state = Arc::new(Mutex::new(DaemonState {
        current_profile: default_profile,
        kernel_driver_active: driver_present,
        active_partition: "Partition_A".to_string(), // A/B Partition simulation
    }));
    
    // Clean up old socket if it exists
    if Path::new(SOCKET_PATH).exists() {
        fs::remove_file(SOCKET_PATH)?;
    }
    
    let listener = UnixListener::bind(SOCKET_PATH)?;
    println!("[+] IPC Server listening on Unix socket: {}", SOCKET_PATH);
    
    // Start background telemetry reporting task
    let telemetry_state = Arc::clone(&state);
    tokio::spawn(async move {
        loop {
            tokio::time::sleep(tokio::time::Duration::from_secs(10)).await;
            let s = telemetry_state.lock().await;
            // println!("[Telemetry] --- PronzeOS Status ---");
            // println!("  - Active Partition: {}", s.active_partition);
            // println!("  - Kernel Mode: {}", if s.kernel_driver_active { "Active" } else { "Simulated (User-space)" });
            // println!("  - Allocation Failure Rate: {}%", s.current_profile.allocation_failure_rate);
            // println!("  - Guard Pages Enabled: {}", s.current_profile.guard_pages);
            // println!("  - Simulation Latency: {} ms", s.current_profile.latency_ms);
            // println!("----------------------------------_");
        }
    });
    
    // Accept connections
    loop {
        match listener.accept().await {
            Ok((stream, _addr)) => {
                let state_clone = Arc::clone(&state);
                tokio::spawn(async move {
                    if let Err(e) = handle_connection(stream, state_clone).await {
                        eprintln!("[-] Error handling connection: {}", e);
                    }
                });
            }
            Err(e) => {
                eprintln!("[-] Unix socket accept error: {}", e);
            }
        }
    }
}

fn load_profile<P: AsRef<Path>>(path: P) -> Option<FaultProfile> {
    let content = fs::read_to_string(path).ok()?;
    serde_json::from_str(&content).ok()
}

async fn handle_connection(mut stream: UnixStream, state: Arc<Mutex<DaemonState>>) -> Result<(), Box<dyn std::error::Error>> {
    let mut buffer = [0; 1024];
    let bytes_read = stream.read(&mut buffer).await?;
    if bytes_read == 0 {
        return Ok(());
    }
    
    let request = String::from_utf8_lossy(&buffer[..bytes_read]);
    let req_trimmed = request.trim();
    
    println!("[+] Daemon IPC Command Received: '{}'", req_trimmed);
    
    let mut s = state.lock().await;
    let response = match req_trimmed {
        "status" => {
            format!(
                "STATUS active_partition={} kernel_active={} alloc_fail_rate={}",
                s.active_partition, s.kernel_driver_active, s.current_profile.allocation_failure_rate
            )
        }
        "toggle_partition" => {
            s.active_partition = if s.active_partition == "Partition_A" {
                "Partition_B".to_string()
            } else {
                "Partition_A".to_string()
            };
            format!("SUCCESS partition_switched_to={}", s.active_partition)
        }
        cmd if cmd.starts_with("load_profile ") => {
            let path = cmd.strip_prefix("load_profile ").unwrap_or("").trim();
            if let Some(profile) = load_profile(path) {
                s.current_profile = profile;
                
                // If kernel driver is active, notify it of profile change
                if s.kernel_driver_active {
                    // In real driver, daemon writes profile configuration to /dev/pronze
                }
                
                format!("SUCCESS profile_loaded={:?}", s.current_profile)
            } else {
                "ERROR failed_to_load_profile".to_string()
            }
        }
        _ => "ERROR unknown_command".to_string(),
    };
    
    stream.write_all(response.as_bytes()).await?;
    stream.flush().await?;
    Ok(())
}
