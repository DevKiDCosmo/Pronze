#include <linux/init.h>
#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/fs.h>
#include <linux/uaccess.h>

#include "_page_pr.h"

MODULE_LICENSE("GPL");
MODULE_AUTHOR("PronKern Team");
MODULE_DESCRIPTION("Pron MF Core fault injection and tracing engine driver");
MODULE_VERSION("0.1");

#define DEVICE_NAME "pron_mf"
#define MAJOR_NUM 240

#define TELEMETRICS_DEVICE_NAME "pron_telemetrics"
#define TELEMETRICS_MAJOR_NUM 241

#define PMF_IOCTL_ENABLE_FAULT  0x1001
#define PMF_IOCTL_SET_PROFILE   0x1002
#define PMF_IOCTL_START_TRACE   0x1003
#define PMF_IOCTL_RECORD_MALLOC 0x1004
#define PMF_PAGE_ORDER          10

static int fault_rate = 2;
static int malloc_success_count = 0;
static int malloc_failure_count = 0;


static char telemetry_buf[512];

static int device_open(struct inode *inode, struct file *file) {
    int ret = 0;
    int *addr1;

    pr_info("pron_mf_core: Device opened\n");

    pr_info("Init DEVICE.\n");
    pr_info("OPEN FOR USE. Move BLOCK to /dev/%s \n", DEVICE_NAME);

    /* Test 1: Standard allocation function */
    pr_info("pron_mf_core: Testing __page_allocation_function(order=%d)\n", PMF_PAGE_ORDER);
    ret = __page_allocation_function(PMF_PAGE_ORDER);
    if (ret == 0) {
        addr1 = __page_aladdr();
        pr_info("pron_mf_core: __page_allocation_function succeeded, addr: %p\n", addr1);
    } else {
        pr_err("pron_mf_core: __page_allocation_function failed: %d\n", ret);
    }

    /* Test 2: Cleanup helper path remains in C only */
    pr_info("pron_mf_core: Using C-only page helper routines\n");

    return 0;
}

static int device_release(struct inode *inode, struct file *file) {
    pr_info("pron_mf_core: Releasing device. Cleaning up remaining pages...\n");
    page_pr_cleanup();
    pr_info("pron_mf_core: Device closed\n");
    return 0;
}

static long device_ioctl(struct file *file, unsigned int cmd, unsigned long arg) {
    switch (cmd) {
        case PMF_IOCTL_ENABLE_FAULT:
            fault_rate = (int)arg;
            pr_info("pron_mf_core: ioctl PMF_IOCTL_ENABLE_FAULT called with arg %d\n", fault_rate);
            break;
        case PMF_IOCTL_SET_PROFILE:
            pr_info("pron_mf_core: ioctl PMF_IOCTL_SET_PROFILE called\n");
            break;
        case PMF_IOCTL_START_TRACE:
            pr_info("pron_mf_core: ioctl PMF_IOCTL_START_TRACE called\n");
            break;
        case PMF_IOCTL_RECORD_MALLOC:
            if (arg == 0) {
                malloc_success_count++;
            } else {
                malloc_failure_count++;
            }
            break;
        default:
            pr_warn("pron_mf_core: Unknown ioctl command 0x%x\n", cmd);
            return -EINVAL;
    }
    return 0;
}

static ssize_t telemetrics_read(struct file *file, char __user *buf, size_t count, loff_t *ppos) {
    int len;
    if (*ppos > 0) return 0; // EOF after first read

    len = snprintf(telemetry_buf, sizeof(telemetry_buf),
        "[Telemetry] --- Pron OS Status ---\n"
        "  - Active Partition: Partition_A\n"
        "  - Kernel Mode: Active\n"
        "  - Allocation Failure Rate: %d%%\n"
        "  - Guard Pages Enabled: true\n"
        "  - Simulation Latency: 5 ms\n"
        "  - Malloc Successes: %d\n"
        "  - Malloc Failures: %d\n"
        "----------------------------------\n",
        fault_rate, malloc_success_count, malloc_failure_count);

    if (count > len) {
        count = len;
    }
    if (copy_to_user(buf, telemetry_buf, count)) {
        return -EFAULT;
    }
    *ppos += count;
    return count;
}

static struct file_operations pron_mf_fops = {
    .owner = THIS_MODULE,
    .open = device_open,
    .release = device_release,
    .unlocked_ioctl = device_ioctl,
};

static struct file_operations telemetrics_fops = {
    .owner = THIS_MODULE,
    .read = telemetrics_read,
};

static int __init pron_mf_init(void) {
    int ret;
    pr_info("pron_mf_core: Pron MF Core Loading\n");

    ret = register_chrdev(MAJOR_NUM, DEVICE_NAME, &pron_mf_fops);
    if (ret < 0) {
        pr_err("pron_mf_core: Failed to register device /dev/%s: %d\n", DEVICE_NAME, ret);
        return ret;
    }

    ret = register_chrdev(TELEMETRICS_MAJOR_NUM, TELEMETRICS_DEVICE_NAME, &telemetrics_fops);
    if (ret < 0) {
        unregister_chrdev(MAJOR_NUM, DEVICE_NAME);
        pr_err("pron_mf_core: Failed to register device /dev/%s: %d\n", TELEMETRICS_DEVICE_NAME, ret);
        return ret;
    }

    pr_info("pron_mf_core: Devices registered successfully\n");
    return 0;
}

static void __exit pron_mf_exit(void) {
    page_pr_cleanup();
    unregister_chrdev(MAJOR_NUM, DEVICE_NAME);
    unregister_chrdev(TELEMETRICS_MAJOR_NUM, TELEMETRICS_DEVICE_NAME);
    pr_info("pron_mf_core: Devices unregistered successfully\n");
}

module_init(pron_mf_init);
module_exit(pron_mf_exit);
