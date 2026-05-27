#include <stdio.h>
#include <pronze/pronze.h>

int main() {
    PronzeContext ctx;
    printf("[+] Starting C allocation failure verification test...\n");
    
    if (pronzeInit(&ctx) != 0) {
        printf("[-] Failed to initialize SDK context\n");
        return 1;
    }
    
    // Enable allocation failure rate at 10%
    if (pronzeEnableAllocationFailure(&ctx, 10) != 0) {
        printf("[-] Failed to enable allocation failure\n");
        pronzeShutdown(&ctx);
        return 1;
    }
    
    int failures = 0;
    int successes = 0;
    
    for (int i = 0; i < 1000; i++) {
        void* p = pronze_malloc(&ctx, 1024);
        if (!p) {
            failures++;
        } else {
            successes++;
            pronze_free(&ctx, p);
        }
    }
    
    printf("\n--- Test Results ---\n");
    printf("  - Malloc Successes: %d\n", successes);
    printf("  - Malloc Failures: %d\n", failures);
    printf("  - Target Failure Rate: 10.0%%\n");
    printf("  - Measured Failure Rate: %.2f%%\n", (double)failures / 10.0);
    
    pronzeShutdown(&ctx);
    
    // With 1000 trials, the count should fall within [50, 150] (i.e. 5% to 15%) with extremely high probability.
    if (failures >= 50 && failures <= 150) {
        printf("[+] Verification SUCCESS: Allocation failure injection behaves deterministically.\n");
        return 0;
    } else {
        printf("[-] Verification FAILURE: Failure rate deviates significantly from target.\n");
        return 1;
    }
}
