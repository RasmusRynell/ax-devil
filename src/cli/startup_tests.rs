use super::{tests::parse, *};

#[test]
fn the_root_help_ends_with_the_links_and_a_subcommand_help_does_not() {
    let help = |args: &[&str]| parse(args).err().unwrap().to_string();
    let root = help(&["--help"]);
    for link in [
        "https://github.com/RasmusRynell/ax-devil\n",
        "/blob/main/AGENTS.md",
        "/tree/main/.agents/skills",
    ] {
        assert!(root.contains(link), "{link}");
    }
    assert!(!help(&["local", "--help"]).contains("AGENTS.md"));
}

#[test]
fn a_failure_logs_the_chain_of_causes_that_led_to_it() {
    #[derive(Debug)]
    struct Because(&'static str, Option<Box<Because>>);
    impl std::fmt::Display for Because {
        fn fmt(&self, out: &mut std::fmt::Formatter) -> std::fmt::Result {
            out.write_str(self.0)
        }
    }
    impl Error for Because {
        fn source(&self) -> Option<&(dyn Error + 'static)> {
            self.1.as_deref().map(|cause| cause as _)
        }
    }
    let leaf = Because("disk full", None);
    let chain = Because(
        "cannot save",
        Some(Box::new(Because("cannot write", Some(Box::new(leaf))))),
    );
    assert_eq!(
        causes(&chain).as_deref(),
        Some("caused by: cannot write\ncaused by: disk full")
    );
    assert_eq!(causes(&Because("alone", None)), None);
}

/// Ctrl+C with no task listening for it, then a startup error: the run still ends as interrupted, so the answer does
/// not wait for anything to be scheduled.
#[cfg(unix)]
#[tokio::test]
async fn an_early_ctrl_c_wins_over_the_startup_error_that_follows() {
    let interrupt = Interrupt::listen();
    let sent = std::process::Command::new("kill")
        .args(["-INT", &std::process::id().to_string()])
        .status()
        .unwrap();
    assert!(sent.success());
    // Lets the runtime hand the signal to its listeners.
    tokio::time::sleep(std::time::Duration::from_millis(50)).await;

    let failed = Err("settings.json is not valid JSON".into());
    assert_eq!(
        finish(&interrupt, "Application failed to start: ", failed),
        ExitCode::from(130)
    );
}
