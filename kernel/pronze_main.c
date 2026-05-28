#include <linux/init.h>
#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/fs.h>
#include <linux/uaccess.h>

#include "pager/_page_pr.h"

MODULE_LICENSE("GPL");
MODULE_AUTHOR("PronzeOS Team");
MODULE_DESCRIPTION("Pronze fault injection and tracing engine driver");
MODULE_VERSION("0.1");

#define DEVICE_NAME "pronze"
#define MAJOR_NUM 240

#define TELEMETRICS_DEVICE_NAME "pronze_telemetry"
#define TELEMETRICS_MAJOR_NUM 241

#define PRONZE_IOCTL_ENABLE_FAULT  0x1001
#define PRONZE_IOCTL_SET_PROFILE   0x1002
#define PRONZE_IOCTL_START_TRACE   0x1003
#define PRONZE_IOCTL_RECORD_MALLOC 0x1004
#define PMF_PAGE_ORDER             10
#define PRONZE_IO_BUFFER_SIZE       4096

static int fault_rate = 2;
static int malloc_success_count = 0;
static int malloc_failure_count = 0;
static unsigned long long pronze_read_seq;
static unsigned long long pronze_write_seq;

static char telemetry_buf[512];
static char pronze_read_buf[PRONZE_IO_BUFFER_SIZE];

static int device_open(struct inode *inode, struct file *file) {
    int ret = 0;
    int *addr1;

    pr_info("pronze: Device opened\n");

    pr_info("Init DEVICE.\n");
    pr_info("OPEN FOR USE. Move BLOCK to /dev/%s \n", DEVICE_NAME);

    /* Test 1: Standard allocation function */
    pr_info("pronze: Testing __page_allocation_function(order=%d)\n", PMF_PAGE_ORDER);
    ret = __page_allocation_function(PMF_PAGE_ORDER);
    if (ret == 0) {
        addr1 = __page_aladdr();
        pr_info("pronze: __page_allocation_function succeeded, addr: %p\n", addr1);
    } else {
        pr_err("pronze: __page_allocation_function failed: %d\n", ret);
    }

    /* Test 2: Cleanup helper path remains in C only */
    pr_info("pronze: Using C-only page helper routines\n");

    return 0;
}

static int device_release(struct inode *inode, struct file *file) {
    pr_info("pronze: Releasing device. Cleaning up remaining pages...\n");
    page_pr_cleanup();
    pr_info("pronze: Device closed\n");
    return 0;
}

static ssize_t device_read(struct file *file, char __user *buf, size_t count, loff_t *ppos) {
    size_t to_copy;
    int len;

    to_copy = count < sizeof(pronze_read_buf) ? count : sizeof(pronze_read_buf);
    if (to_copy == 0) {
        return 0;
    }

    len = snprintf(pronze_read_buf, sizeof(pronze_read_buf),
        "PRONZE-BLOCK seq=%llu requested=%zu fault_rate=%d malloc_ok=%d malloc_fail=%d\n",
        ++pronze_read_seq, count, fault_rate, malloc_success_count, malloc_failure_count);

    if (len < 0) {
        return -EFAULT;
    }

    if ((size_t)len < to_copy) {
        memset(pronze_read_buf + len, '.', to_copy - len);
    }

    if (copy_to_user(buf, pronze_read_buf, to_copy)) {
        return -EFAULT;
    }

    *ppos += to_copy;
    pr_info("pronze: read block #%llu delivered %zu bytes\n", pronze_read_seq, to_copy);
    return to_copy;
}

static ssize_t device_write(struct file *file, const char __user *buf, size_t count, loff_t *ppos) {
    pr_info("pronze: write block #%llu received %zu bytes\n", ++pronze_write_seq, count);
    *ppos += count;
    return count;
}

static long device_ioctl(struct file *file, unsigned int cmd, unsigned long arg) {
    switch (cmd) {
        case PRONZE_IOCTL_ENABLE_FAULT:
            fault_rate = (int)arg;
            pr_info("pronze: ioctl PRONZE_IOCTL_ENABLE_FAULT called with arg %d\n", fault_rate);
            break;
        case PRONZE_IOCTL_SET_PROFILE:
            pr_info("pronze: ioctl PRONZE_IOCTL_SET_PROFILE called\n");
            break;
        case PRONZE_IOCTL_START_TRACE:
            pr_info("pronze: ioctl PRONZE_IOCTL_START_TRACE called\n");
            break;
        case PRONZE_IOCTL_RECORD_MALLOC:
            if (arg == 0) {
                malloc_success_count++;
            } else {
                malloc_failure_count++;
            }
            break;
        default:
            pr_warn("pronze: Unknown ioctl command 0x%x\n", cmd);
            return -EINVAL;
    }
    return 0;
}

static ssize_t telemetrics_read(struct file *file, char __user *buf, size_t count, loff_t *ppos) {
    int len;
    if (*ppos > 0) return 0; // EOF after first read

    len = snprintf(telemetry_buf, sizeof(telemetry_buf),
        "[Telemetry] --- PronzeOS Status ---\n"
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

static struct file_operations pronze_fops = {
    .owner = THIS_MODULE,
    .open = device_open,
    .read = device_read,
    .write = device_write,
    .release = device_release,
    .unlocked_ioctl = device_ioctl,
};

static struct file_operations telemetrics_fops = {
    .owner = THIS_MODULE,
    .read = telemetrics_read,
};

static int __init pronze_init(void) {
    int ret;
    pr_info("pronze: Pronze Core Loading\n");

    ret = register_chrdev(MAJOR_NUM, DEVICE_NAME, &pronze_fops);
    if (ret < 0) {
        pr_err("pronze: Failed to register device /dev/%s: %d\n", DEVICE_NAME, ret);
        return ret;
    }

    ret = register_chrdev(TELEMETRICS_MAJOR_NUM, TELEMETRICS_DEVICE_NAME, &telemetrics_fops);
    if (ret < 0) {
        unregister_chrdev(MAJOR_NUM, DEVICE_NAME);
        pr_err("pronze: Failed to register device /dev/%s: %d\n", TELEMETRICS_DEVICE_NAME, ret);
        return ret;
    }

    pr_info("pronze: Devices registered successfully\n");
    return 0;
}

static void __exit pronze_exit(void) {
    page_pr_cleanup();
    unregister_chrdev(MAJOR_NUM, DEVICE_NAME);
    unregister_chrdev(TELEMETRICS_MAJOR_NUM, TELEMETRICS_DEVICE_NAME);
    pr_info("pronze: Devices unregistered successfully\n");
}

module_init(pronze_init);
module_exit(pronze_exit);
