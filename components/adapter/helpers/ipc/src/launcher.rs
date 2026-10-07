//! Literal-path launcher for the separately supervised public runtime.
use std::{io, path::Path, process::{Child, Command, Stdio}};
use std::os::windows::process::CommandExt;

pub fn start(python:&str, script:&str)->io::Result<Child> {
    let python=Path::new(python);
    let script=Path::new(script);
    let invalid=||io::Error::new(io::ErrorKind::InvalidInput,"Expected absolute paths to python.exe/pythonw.exe and skate_launcher.py");
    if !python.is_absolute() || !script.is_absolute() ||
        !matches!(python.file_name().and_then(|s|s.to_str()),Some("python.exe"|"pythonw.exe")) ||
        script.file_name().and_then(|s|s.to_str())!=Some("skate_launcher.py") {
        return Err(invalid());
    }
    if !python.is_file() || !script.is_file() {
        return Err(io::Error::new(io::ErrorKind::NotFound,"Configured mod launcher is missing"));
    }
    Command::new(python).arg("-u").arg(script).arg("--parent-pid").arg(std::process::id().to_string())
        .current_dir(script.parent().ok_or_else(invalid)?)
        .stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null())
        .creation_flags(0x08000000) // CREATE_NO_WINDOW
        .spawn()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_shell_and_relative_launch_paths() {
        for (python,script) in [("cmd.exe","skate_launcher.py"),("C:\\Windows\\System32\\cmd.exe","C:\\skate_launcher.py"),
            ("C:\\python.exe","C:\\launcher.ps1"),("python.exe","C:\\skate_launcher.py")] {
            assert_eq!(start(python,script).unwrap_err().kind(),io::ErrorKind::InvalidInput);
        }
    }

    #[test]
    #[ignore="author check: set S3_TEST_PYTHON and S3_TEST_LAUNCH_DIR to a private empty directory"]
    fn unicode_and_metacharacter_paths_are_literal() {
        let python=std::env::var("S3_TEST_PYTHON").expect("test Python path");
        let folder=std::path::PathBuf::from(std::env::var("S3_TEST_LAUNCH_DIR").expect("private test folder"));
        assert!(folder.is_absolute() && !folder.exists());
        std::fs::create_dir(&folder).unwrap();
        let script=folder.join("skate_launcher.py");
        let output=folder.join("observed.txt");
        std::fs::write(&script,"import pathlib, sys\npathlib.Path(__file__).with_name('observed.txt').write_text('|'.join(sys.argv[1:]))\n").unwrap();
        let mut child=start(&python,script.to_str().unwrap()).unwrap();
        assert!(child.wait().unwrap().success());
        assert_eq!(std::fs::read_to_string(&output).unwrap(),format!("--parent-pid|{}",std::process::id()));
        std::fs::remove_file(output).unwrap();
        std::fs::remove_file(script).unwrap();
        std::fs::remove_dir(folder).unwrap();
    }
}
