#include <linux/init.h>
#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/fs.h>
#include <linux/uaccess.h>

MODULE_LICENSE("GPL");
MODULE_AUTHOR("PronKern Team");
MODULE_DESCRIPTION("MemFault Core fault injection and tracing engine driver");
MODULE_VERSION("0.1");

#define DEVICE_NAME "memfault"
#define MAJOR_NUM 240

#define MF_IOCTL_ENABLE_FAULT 0x1001
#define MF_IOCTL_SET_PROFILE  0x1002
#define MF_IOCTL_START_TRACE  0x1003

static int device_open(struct inode *inode, struct file *file) {
    pr_info("memfault_core: Device opened\n");
    return 0;
}

static int device_release(struct inode *inode, struct file *file) {
    pr_info("memfault_core: Device closed\n");
    return 0;
}

static long device_ioctl(struct file *file, unsigned int cmd, unsigned long arg) {
    switch (cmd) {
        case MF_IOCTL_ENABLE_FAULT:
            pr_info("memfault_core: ioctl MF_IOCTL_ENABLE_FAULT called with arg %lu\n", arg);
            break;
        case MF_IOCTL_SET_PROFILE:
            pr_info("memfault_core: ioctl MF_IOCTL_SET_PROFILE called\n");
            // In a real module we would copy the profile from userspace
            break;
        case MF_IOCTL_START_TRACE:
            pr_info("memfault_core: ioctl MF_IOCTL_START_TRACE called\n");
            break;
        default:
            pr_warn("memfault_core: Unknown ioctl command 0x%x\n", cmd);
            return -EINVAL;
    }
    return 0;
}

static struct file_operations memfault_fops = {
    .owner = THIS_MODULE,
    .open = device_open,
    .release = device_release,
    .unlocked_ioctl = device_ioctl,
};

static int __init memfault_init(void) {
    int ret;
    pr_info("memfault_core: MemFault Core Loading\n");

    ret = register_chrdev(MAJOR_NUM, DEVICE_NAME, &memfault_fops);
    if (ret < 0) {
        pr_err("memfault_core: Failed to register character device with major %d: %d\n", MAJOR_NUM, ret);
        return ret;
    }

    pr_info("memfault_core: MemFault Core Loaded with major %d\n", MAJOR_NUM);
    return 0;
}

static void __exit memfault_exit(void) {
    unregister_chrdev(MAJOR_NUM, DEVICE_NAME);
    pr_info("memfault_core: MemFault Core Unloaded\n");
}

module_init(memfault_init);
module_exit(memfault_exit);
