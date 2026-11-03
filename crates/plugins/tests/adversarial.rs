//! The plugin host against plugins that misbehave. The plugin is this test binary run again: the command
//! names it with `--plugin=MODE`, and `main` plays the plugin instead of running the tests. The binary has
//! no libtest harness because libtest would print its own lines on the plugin's stdout.
#![allow(clippy::print_stdout, clippy::print_stderr)] // a test runner without a harness reports with prints

mod common;

use std::{
    io::Write,
    panic::{self, AssertUnwindSafe},
    path::{Path, PathBuf},
    process::{Command, Stdio},
    time::{Duration, Instant},
};

use common::{Run, TestSink, decode, manifest_text};
use foundation::{CancelToken, Interrupt};
use handler_api::{OnEqualKey, Settings};
use plugins::{LoadedPlugin, PluginDecoder};
use scene::SceneTime;

const MODE_PREFIX: &str = "--plugin=";
const SECRET_NAMES: [&str; 3] = [
    "AX_DEVIL_TARGET_PASS",
    "AWS_SECRET_ACCESS_KEY",
    "AX_DEVIL_SERVER_PASSWORD",
];

fn main() {
    if let Some(mode) = std::env::args()
        .nth(1)
        .and_then(|arg| arg.strip_prefix(MODE_PREFIX).map(str::to_owned))
    {
        plugin(&mode);
        return;
    }
    // SAFETY: nothing else runs yet, so no other thread reads the environment.
    unsafe {
        std::env::set_var("AX_DEVIL_TARGET_PASS", "SECRET-1");
        std::env::set_var("AWS_SECRET_ACCESS_KEY", "SECRET-2");
        std::env::set_var("AX_DEVIL_SERVER_PASSWORD", "SECRET-3");
        std::env::set_var("FAKE_LISTED", "listed-value");
    }
    let filter = std::env::args().skip(1).find(|arg| !arg.starts_with('-'));
    let tests: [(&str, fn()); 9] = [
        ("environment_is_an_allowlist", environment_is_an_allowlist),
        ("wire_lines_reach_the_sink", wire_lines_reach_the_sink),
        (
            "a_silent_plugin_is_killed_after_the_idle_timeout",
            a_silent_plugin_is_killed_after_the_idle_timeout,
        ),
        (
            "the_whole_process_group_is_killed",
            the_whole_process_group_is_killed,
        ),
        (
            "a_line_over_16_mib_ends_the_run_and_keeps_the_samples",
            a_line_over_16_mib_ends_the_run_and_keeps_the_samples,
        ),
        (
            "bad_lines_are_skipped_and_counted_by_line_number",
            bad_lines_are_skipped_and_counted_by_line_number,
        ),
        (
            "a_failure_keeps_the_samples_and_quotes_stderr",
            a_failure_keeps_the_samples_and_quotes_stderr,
        ),
        (
            "a_late_header_is_a_protocol_error",
            a_late_header_is_a_protocol_error,
        ),
        (
            "cancelling_kills_the_plugin_and_removes_its_folder",
            cancelling_kills_the_plugin_and_removes_its_folder,
        ),
    ];
    let mut failed = 0;
    for (name, test) in tests {
        if filter
            .as_ref()
            .is_some_and(|filter| !name.contains(filter.as_str()))
        {
            continue;
        }
        let started = Instant::now();
        match panic::catch_unwind(AssertUnwindSafe(test)) {
            Ok(()) => println!(
                "test {name} ... ok ({:.1}s)",
                started.elapsed().as_secs_f32()
            ),
            Err(_) => {
                println!("test {name} ... FAILED");
                failed += 1;
            }
        }
    }
    if failed > 0 {
        println!("{failed} adversarial test(s) failed");
        std::process::exit(1);
    }
}

// The plugin side ------------------------------------------------------------------------------------------

fn sample_line(frame: u64) -> String {
    format!(
        r#"{{"sample":{{"time":{{"start":{{"frame":{frame}}},"end":{{"frame":{frame}}}}},"entities":[]}}}}"#
    )
}

fn out(line: &str) {
    let mut stdout = std::io::stdout().lock();
    stdout.write_all(line.as_bytes()).expect("stdout");
    stdout.write_all(b"\n").expect("stdout");
    stdout.flush().expect("stdout");
}

fn sleep_forever() -> ! {
    loop {
        std::thread::sleep(Duration::from_secs(60));
    }
}

fn plugin(mode: &str) {
    match mode {
        "env-dump" => {
            let mut debug: Vec<String> = std::env::vars()
                .map(|(name, value)| {
                    format!("{}:{}", serde_json_string(&name), serde_json_string(&value))
                })
                .collect();
            let cwd = std::env::current_dir().expect("cwd");
            debug.push(format!(
                "\"CWD\":{}",
                serde_json_string(&cwd.to_string_lossy())
            ));
            out(&format!(
                r#"{{"sample":{{"time":{{"start":{{"frame":0}},"end":{{"frame":0}}}},"entities":[],"debug":{{{}}}}}}}"#,
                debug.join(",")
            ));
        }
        "wire" => {
            out(
                r#"{"header":{"coverage":"dense","filter":[{"id":"show_car","label":"Cars","types":["car"]}],"metadata":{"source":"fake"}}}"#,
            );
            out(&sample_line(0));
            out(&format!("{}\r", sample_line(0)));
            out("");
            out(&sample_line(1).replacen("sample", "replace", 1));
            out(
                r#"{"warning":{"problem":"odd","reasons":["a","b","c","d"],"consequence":"none"}}"#,
            );
            out(r#"{"skipped":"row 9 has no id"}"#);
        }
        "hang" => sleep_forever(),
        "grandchild" => {
            // The orphan is the point: the host must kill it, and this plugin never waits for it.
            #[allow(clippy::zombie_processes)]
            let child = Command::new("sleep")
                .arg("300")
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .spawn()
                .expect("sleep starts");
            out(&format!(
                r#"{{"warning":{{"problem":"grandchild {}"}}}}"#,
                child.id()
            ));
            sleep_forever();
        }
        "huge-line" => {
            out(&sample_line(0));
            out(&sample_line(1));
            let mut stdout = std::io::stdout().lock();
            let chunk = vec![b'x'; 1 << 20];
            for _ in 0..17 {
                if stdout.write_all(&chunk).is_err() {
                    break;
                }
            }
            drop(stdout);
            sleep_forever();
        }
        "bad-lines" => {
            out(&sample_line(0));
            let mut stdout = std::io::stdout().lock();
            stdout.write_all(b"\xff\xfe not utf-8\n").expect("stdout");
            stdout.write_all(b"not json at all\n").expect("stdout");
            stdout.flush().expect("stdout");
            drop(stdout);
            out(&sample_line(1));
            out(r#"{"nope":1}"#);
            out(
                r#"{"sample":{"time":{"start":{"frame":2},"end":{"frame":2}},"entities":[{"id":"","observations":[]}]}}"#,
            );
            out(&sample_line(3));
        }
        "fail-after-3" => {
            for frame in 0..3 {
                out(&sample_line(frame));
            }
            eprintln!("boom: cannot read record 4");
            std::process::exit(3);
        }
        "error-after-2" => {
            out(&sample_line(0));
            out(&sample_line(1));
            out(r#"{"error":{"reason":"cannot read record 3","field":"camera"}}"#);
            out(&sample_line(2));
        }
        "stderr-flood" => {
            let mut stderr = std::io::stderr().lock();
            let line = format!("{}\n", "e".repeat(99));
            for _ in 0..100_000 {
                stderr.write_all(line.as_bytes()).expect("stderr");
            }
            drop(stderr);
            out(&sample_line(0));
        }
        "late-header" => {
            out(&sample_line(0));
            out(r#"{"header":{"coverage":"dense"}}"#);
            out(&sample_line(1));
        }
        "slow-stream" => {
            out(&format!(
                r#"{{"warning":{{"problem":"cwd","reasons":[{}]}}}}"#,
                serde_json_string(&std::env::current_dir().expect("cwd").to_string_lossy())
            ));
            for frame in 0..400 {
                out(&sample_line(frame));
                std::thread::sleep(Duration::from_millis(50));
            }
        }
        other => panic!("unknown plugin mode {other}"),
    }
}

/// JSON text for `text`, without a JSON library on the plugin side.
fn serde_json_string(text: &str) -> String {
    let mut quoted = String::from('"');
    for c in text.chars() {
        match c {
            '"' => quoted.push_str("\\\""),
            '\\' => quoted.push_str("\\\\"),
            c if (c as u32) < 0x20 => quoted.push_str(&format!("\\u{:04x}", c as u32)),
            c => quoted.push(c),
        }
    }
    quoted.push('"');
    quoted
}

// The host side ---------------------------------------------------------------------------------------------

/// A decoder for this binary playing `mode`, with `FAKE_LISTED` passed through and `extra` manifest fields.
fn fake(folder: &Path, mode: &str, extra: &str) -> std::sync::Arc<PluginDecoder> {
    let exe = std::env::current_exe().expect("test binary path");
    let command = serde_json::json!([exe, format!("{MODE_PREFIX}{mode}")]);
    let text = manifest_text(
        &command.to_string(),
        &format!(r#","env":["FAKE_LISTED"]{extra}"#),
    );
    std::fs::write(folder.join("ax-devil-plugin.json"), text).expect("manifest written");
    LoadedPlugin::load(&folder.join("ax-devil-plugin.json"))
        .expect("manifest loads")
        .decoders
        .remove(0)
}

fn run_fake(mode: &str, extra: &str, cancel: &CancelToken, sink: TestSink) -> Run {
    let folder = tempfile::tempdir().expect("folder");
    let decoder = fake(folder.path(), mode, extra);
    decode(
        &decoder,
        &folder.path().join("input.test"),
        &Settings::new(),
        cancel,
        sink,
    )
}

fn frame_of(run: &Run, index: usize) -> u64 {
    match run.sink.samples[index].0.time().start() {
        SceneTime::Frame(frame) => frame,
        SceneTime::Timestamp(_) => panic!("a frame-keyed sample"),
    }
}

fn environment_is_an_allowlist() {
    let run = run_fake("env-dump", "", &CancelToken::new(), TestSink::default());
    run.summary();
    let debug = run.sink.samples[0].0.debug();
    let has = |name: &str| debug.iter().any(|(key, _)| key == name);
    for name in SECRET_NAMES {
        assert!(!has(name), "{name} reached the plugin");
    }
    let everything = format!("{debug:?}");
    assert!(
        !everything.contains("SECRET"),
        "a secret value reached the plugin"
    );
    assert!(has("PATH"), "PATH is passed");
    assert!(has("FAKE_LISTED"), "a name the manifest lists is passed");
    assert!(has("TMPDIR"));
}

fn wire_lines_reach_the_sink() {
    let run = run_fake("wire", "", &CancelToken::new(), TestSink::default());
    let summary = run.summary();
    let header = run.sink.header.as_ref().expect("header reached the sink");
    assert_eq!(header.filter.options.len(), 1);
    assert_eq!(summary.coverage, scene::Coverage::Dense);
    assert_eq!(summary.filter, header.filter);
    let modes: Vec<OnEqualKey> = run.sink.samples.iter().map(|(_, mode)| *mode).collect();
    assert_eq!(
        modes,
        [OnEqualKey::Merge, OnEqualKey::Merge, OnEqualKey::Replace]
    );
    let warnings = &run.sink.warnings;
    assert_eq!(warnings.len(), 1, "{warnings:?}");
    assert_eq!(warnings[0].reasons, ["a", "b", "c"]);
    assert!(warnings[0].more_reasons);
    assert_eq!(run.sink.skips, [(7, "row 9 has no id".to_owned())]);
}

fn a_silent_plugin_is_killed_after_the_idle_timeout() {
    let started = Instant::now();
    let run = run_fake(
        "hang",
        r#","idle_timeout_s":1"#,
        &CancelToken::new(),
        TestSink::default(),
    );
    assert!(
        run.failure().contains("produced nothing"),
        "{}",
        run.failure()
    );
    assert!(
        started.elapsed() < Duration::from_secs(3),
        "{:?}",
        started.elapsed()
    );
}

fn process_is_gone(pid: u32) -> bool {
    match std::fs::read_to_string(format!("/proc/{pid}/stat")) {
        // "pid (name) S ..." with S = Z (zombie not yet reaped) or X (dead, being reaped).
        Ok(stat) => stat
            .rsplit(')')
            .next()
            .is_some_and(|rest| rest.trim_start().starts_with(['Z', 'X'])),
        Err(_) if Path::new("/proc/self").exists() => true,
        Err(_) => !Command::new("kill")
            .args(["-0", &pid.to_string()])
            .stderr(Stdio::null())
            .status()
            .is_ok_and(|status| status.success()),
    }
}

fn the_whole_process_group_is_killed() {
    let run = run_fake(
        "grandchild",
        r#","idle_timeout_s":1"#,
        &CancelToken::new(),
        TestSink::default(),
    );
    run.failure();
    let problem = &run.sink.warnings[0].problem;
    let pid: u32 = problem
        .trim_start_matches("grandchild ")
        .parse()
        .expect("pid in the warning");
    let deadline = Instant::now() + Duration::from_secs(3);
    let mut gone = process_is_gone(pid);
    while !gone && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(50));
        gone = process_is_gone(pid);
    }
    assert!(gone, "the grandchild {pid} outlived the run");
}

fn a_line_over_16_mib_ends_the_run_and_keeps_the_samples() {
    let started = Instant::now();
    let run = run_fake("huge-line", "", &CancelToken::new(), TestSink::default());
    assert!(
        run.failure().contains("longer than 16 MiB"),
        "{}",
        run.failure()
    );
    assert_eq!(run.sink.samples.len(), 2);
    assert!(started.elapsed() < Duration::from_secs(10));
}

fn bad_lines_are_skipped_and_counted_by_line_number() {
    let run = run_fake("bad-lines", "", &CancelToken::new(), TestSink::default());
    run.summary();
    let frames: Vec<u64> = (0..run.sink.samples.len())
        .map(|index| frame_of(&run, index))
        .collect();
    assert_eq!(frames, [0, 1, 3]);
    let positions: Vec<u64> = run
        .sink
        .skips
        .iter()
        .map(|(position, _)| *position)
        .collect();
    assert_eq!(positions, [2, 3, 5, 6]);
}

fn a_failure_keeps_the_samples_and_quotes_stderr() {
    let run = run_fake("fail-after-3", "", &CancelToken::new(), TestSink::default());
    assert_eq!(run.sink.samples.len(), 3);
    let reason = run.failure();
    assert!(reason.contains("exit code 3"), "{reason}");
    assert!(reason.contains("boom: cannot read record 4"), "{reason}");

    // An error line ends the run even when the plugin goes on and exits 0.
    let run = run_fake(
        "error-after-2",
        "",
        &CancelToken::new(),
        TestSink::default(),
    );
    assert_eq!(run.sink.samples.len(), 2);
    assert_eq!(run.failure(), "cannot read record 3");

    // Stderr is drained: ten megabytes of it do not block the plugin.
    let run = run_fake("stderr-flood", "", &CancelToken::new(), TestSink::default());
    run.summary();
    assert_eq!(run.sink.samples.len(), 1);
}

fn a_late_header_is_a_protocol_error() {
    let run = run_fake("late-header", "", &CancelToken::new(), TestSink::default());
    assert!(
        run.failure().contains("header must come once"),
        "{}",
        run.failure()
    );
    assert_eq!(run.sink.samples.len(), 1);
    assert!(run.sink.header.is_none());
}

fn cancelling_kills_the_plugin_and_removes_its_folder() {
    let cancel = CancelToken::new();
    let sink = TestSink {
        cancel_after: Some((2, cancel.clone())),
        ..TestSink::default()
    };
    let started = Instant::now();
    let run = run_fake("slow-stream", "", &cancel, sink);
    assert!(matches!(run.result, Err(Interrupt::Cancelled)));
    assert!(
        started.elapsed() < Duration::from_secs(1),
        "{:?}",
        started.elapsed()
    );
    assert!(run.sink.samples.len() < 10);
    let cwd = PathBuf::from(&run.sink.warnings[0].reasons[0]);
    assert!(
        !cwd.exists(),
        "the run folder {} was left behind",
        cwd.display()
    );
}
