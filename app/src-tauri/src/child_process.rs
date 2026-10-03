use std::ffi::OsStr;

pub struct Command;

impl Command {
    #[allow(clippy::new_ret_no_self)]
    pub fn new<S: AsRef<OsStr>>(program: S) -> std::process::Command {
        #[cfg(target_os = "windows")]
        {
            use std::os::windows::process::CommandExt;
            const CREATE_NO_WINDOW: u32 = 0x0800_0000;
            let mut command = std::process::Command::new(program);
            command.creation_flags(CREATE_NO_WINDOW);
            command
        }
        #[cfg(not(target_os = "windows"))]
        {
            std::process::Command::new(program)
        }
    }

    pub fn low_priority<S: AsRef<OsStr>>(program: S) -> std::process::Command {
        #[cfg(target_os = "windows")]
        {
            use std::os::windows::process::CommandExt;
            const CREATE_NO_WINDOW: u32 = 0x0800_0000;
            const BELOW_NORMAL_PRIORITY_CLASS: u32 = 0x0000_4000;
            let mut command = std::process::Command::new(program);
            command.creation_flags(CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS);
            command
        }
        #[cfg(not(target_os = "windows"))]
        {
            let mut command = std::process::Command::new("nice");
            command.args(["-n", "10"]).arg(program.as_ref());
            command
        }
    }
}
