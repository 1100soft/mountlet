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
}
