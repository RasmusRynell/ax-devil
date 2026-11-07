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

/// Sends SIGINT to this process, which every Ctrl+C listener of the test binary receives.
#[cfg(unix)]
fn ctrl_c() {
    let sent = std::process::Command::new("kill")
        .args(["-INT", &std::process::id().to_string()])
        .status()
        .unwrap();
    assert!(sent.success());
}

/// How long a test waits for the runtime to hand over a signal before it fails.
#[cfg(unix)]
const SIGNAL_WAIT: std::time::Duration = std::time::Duration::from_secs(10);

/// Ctrl+C with no task listening for it, then a startup error: the run still ends as interrupted, so the answer does
/// not wait for anything to be scheduled.
#[cfg(unix)]
#[tokio::test]
async fn an_early_ctrl_c_wins_over_the_startup_error_that_follows() {
    use tokio::signal::unix::{SignalKind, signal};
    let interrupt = Interrupt::listen();
    // The runtime hands a signal to all its listeners at once, so this one hearing it means `interrupt` holds it too.
    let mut heard = signal(SignalKind::interrupt()).unwrap();
    ctrl_c();
    tokio::time::timeout(SIGNAL_WAIT, heard.recv())
        .await
        .expect("the runtime never handed over the signal");

    let failed = Err("settings.json is not valid JSON".into());
    assert_eq!(
        finish(&interrupt, "Application failed to start: ", failed).await,
        ExitCode::from(130)
    );
}

/// The first Ctrl+C asks for the cleanup; a second one, while it is not done, ends the process.
#[cfg(unix)]
#[tokio::test]
async fn a_second_ctrl_c_ends_a_stalled_run_at_once() {
    let (ended, ending) = tokio::sync::oneshot::channel();
    let interrupt = Interrupt::watch_then(move || ended.send(()).unwrap());
    ctrl_c();
    // The first one is taken before the second comes, which would otherwise be the same event.
    let mut stopped = interrupt.stop.subscribe();
    tokio::time::timeout(SIGNAL_WAIT, stopped.wait_for(|stop| *stop))
        .await
        .expect("the first Ctrl+C never stopped the run")
        .unwrap();
    ctrl_c();
    tokio::time::timeout(SIGNAL_WAIT, ending)
        .await
        .expect("the second Ctrl+C never ended the run")
        .unwrap();
}
