#include <linux/init.h>
#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/fs.h>
#include <linux/uaccess.h>

MODULE_LICENSE("GPL");
MODULE_AUTHOR("PronKern Team");
MODULE_DESCRIPTION("Pron MF Core fault injection and tracing engine driver");
MODULE_VERSION("0.1");

#define DEVICE_NAME "pron_mf"
#define MAJOR_NUM 240

#define PMF_IOCTL_ENABLE_FAULT 0x1001
#define PMF_IOCTL_SET_PROFILE  0x1002
#define PMF_IOCTL_START_TRACE  0x1003

static int device_open(struct inode *inode, struct file *file) {
    pr_info("pron_mf_core: Device opened\n");
    return 0;
}

static int device_release(struct inode *inode, struct file *file) {
    pr_info("pron_mf_core: Device closed\n");
    return 0;
}

static long device_ioctl(struct file *file, unsigned int cmd, unsigned long arg) {
    switch (cmd) {
        case PMF_IOCTL_ENABLE_FAULT:
            pr_info("pron_mf_core: ioctl PMF_IOCTL_ENABLE_FAULT called with arg %lu\n", arg);
            break;
        case PMF_IOCTL_SET_PROFILE:
            pr_info("pron_mf_core: ioctl PMF_IOCTL_SET_PROFILE called\n");
            // In a real module we would copy the profile from userspace
            break;
        case PMF_IOCTL_START_TRACE:
            pr_info("pron_mf_core: ioctl PMF_IOCTL_START_TRACE called\n");
            break;
        default:
            pr_warn("pron_mf_core: Unknown ioctl command 0x%x\n", cmd);
            return -EINVAL;
    }
    return 0;
}

static struct file_operations pron_mf_fops = {
    .owner = THIS_MODULE,
    .open = device_open,
    .release = device_release,
    .unlocked_ioctl = device_ioctl,
};

static int __init pron_mf_init(void) {
    int ret;
    pr_info("pron_mf_core: Pron MF Core Loading\n");

    ret = register_chrdev(MAJOR_NUM, DEVICE_NAME, &pron_mf_fops);
    if (ret < 0) {
        pr_err("pron_mf_core: Failed to register character device with major %d: %d\n", MAJOR_NUM, ret);
        return ret;
    }

    pr_info("pron_mf_core: Pron MF Core Loaded with major %d\n", MAJOR_NUM);
    return 0;
}

static void __exit pron_mf_exit(void) {
    unregister_chrdev(MAJOR_NUM, DEVICE_NAME);
    pr_info("pron_mf_core: Pron MF Core Unloaded\n");
}

module_init(pron_mf_init);
module_exit(pron_mf_exit);
