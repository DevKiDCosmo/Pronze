1. Create docker making distro using kernel.
2. Create image with bootloader syslinux or grub
3. Adding user space somehow.
4. Adding daemon and reading if ran.
5. Create real "device"
6. Make non testable mem safe. If not safe, no env to run can be done.
7. CI/CD Pipelines
8. SDK, Fraemwork and UT Testing
9. Windows, Mac etc. support but ...

Develop an GUI. (Later on) Tauri and not electron.

Features

Later capturing also all inputs inside application and for reproductibility add something like Framework::WaitForNextInput. Framework::ContinueWithNextClick. This let's input be indepently execute from loading. So no time constraint. Those also say when capturing is possible. It needs to be init with Framework::InitDriverInput(&registrar, &uid, &pid);
