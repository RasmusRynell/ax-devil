//! The plugin host against plugins that read one record at a time: the plugin side (`plugin`) and the tests.

use std::{
    io::{BufRead, Write},
    time::Duration,
};

use foundation::CancelToken;
use serde_json::{Value, json};

use crate::common::{Run, Scripted, decode_records};

/// The tests of this module, run by the harness-less `main`.
pub const TESTS: [(&str, fn()); 4] = [
    (
        "records_are_answered_in_order_binary_arrives_as_base64_and_skips_name_the_record",
        records_are_answered_in_order,
    ),
    (
        "a_plugin_that_dies_after_answering_loses_one_record_and_is_started_again",
        a_plugin_that_dies_after_answering_loses_one_record,
    ),
    (
        "a_plugin_that_never_answers_fails_the_session",
        a_plugin_that_never_answers_fails_the_session,
    ),
    (
        "a_gap_between_records_longer_than_the_idle_timeout_does_not_kill_the_plugin",
        a_gap_between_records_longer_than_the_idle_timeout,
    ),
];

// The plugin side ------------------------------------------------------------------------------------------

fn emit(line: &Value) {
    let mut stdout = std::io::stdout().lock();
    writeln!(stdout, "{line}").expect("stdout");
    stdout.flush().expect("stdout");
}

/// Answers each record with a header (once), then a sample carrying the payload and the process id, or a `skipped`
/// for the text `skip`. The text `crash` ends the process; the mode `records-mute` never answers.
pub fn plugin(mode: &str) {
    let stdin = std::io::stdin();
    let mut lines = stdin.lock().lines().map(|line| line.expect("stdin"));
    let request: Value = serde_json::from_str(&lines.next().expect("request")).expect("json");
    assert_eq!(request["input"], "records");
    let mut answered = 0_i64;
    for line in lines {
        if mode == "records-mute" {
            continue;
        }
        let record: Value = serde_json::from_str(&line).expect("json");
        let record = &record["record"];
        if answered == 0 {
            emit(&json!({"header": {"coverage": "sparse"}}));
        }
        match record["text"].as_str() {
            Some("crash") => {
                eprintln!("boom: cannot read this record");
                std::process::exit(3);
            }
            Some("skip") => emit(&json!({"skipped": "unreadable"})),
            _ => {
                answered += 1;
                let us = answered * 1000;
                emit(&json!({"sample": {
                    "time": {"start": {"us": us}, "end": {"us": us}},
                    "entities": [],
                    "debug": {"pid": std::process::id(), "payload": record},
                }}));
            }
        }
        emit(&json!({"done": {}}));
    }
}

// The host side ---------------------------------------------------------------------------------------------

fn run_records(mode: &str, extra: &str, records: &[&[u8]], gap: Duration) -> Run {
    let folder = tempfile::tempdir().expect("folder");
    let decoder = crate::fake(folder.path(), mode, extra);
    decode_records(
        &decoder,
        &mut Scripted::new(records, gap),
        &CancelToken::new(),
    )
}

/// The debug entry `key` of sample `index`, as text.
fn debug_of(run: &Run, index: usize, key: &str) -> String {
    let debug = run.sink.samples[index].0.debug();
    let (_, value) = debug
        .iter()
        .find(|(name, _)| name == key)
        .unwrap_or_else(|| panic!("sample {index} has no {key}"));
    format!("{value:?}")
}

fn records_are_answered_in_order() {
    let run = run_records(
        "records-echo",
        "",
        &[b"first", b"skip", &[0xff, 0x00, 0x01], b"last"],
        Duration::ZERO,
    );
    run.summary();
    assert_eq!(run.sink.samples.len(), 3);
    assert!(debug_of(&run, 0, "payload").contains("first"));
    assert!(debug_of(&run, 1, "payload").contains("base64"));
    assert!(debug_of(&run, 1, "payload").contains("/wAB"));
    assert!(debug_of(&run, 2, "payload").contains("last"));
    // The second record's `skipped` is reported at that record, not at the number of the output line carrying it.
    assert_eq!(run.sink.skips, [(2, "unreadable".to_owned())]);
    assert!(run.sink.header.is_some());
}

fn a_plugin_that_dies_after_answering_loses_one_record() {
    let run = run_records(
        "records-echo",
        "",
        &[b"one", b"crash", b"three"],
        Duration::ZERO,
    );
    run.summary();
    assert_eq!(run.sink.samples.len(), 2);
    assert_ne!(debug_of(&run, 0, "pid"), debug_of(&run, 1, "pid"));
    let [(position, reason)] = run.sink.skips.as_slice() else {
        panic!("one skipped record: {:?}", run.sink.skips);
    };
    assert_eq!(*position, 2);
    assert!(reason.contains("exit code 3"), "{reason}");
    assert!(reason.contains("boom: cannot read this record"), "{reason}");
    assert!(
        reason.contains("starts again with the next record"),
        "{reason}"
    );

    // The restarted process dies before answering anything: it is broken, so the session fails instead of the
    // plugin being started once per remaining record.
    let run = run_records(
        "records-echo",
        "",
        &[b"one", b"crash", b"crash", b"four"],
        Duration::ZERO,
    );
    assert!(run.failure().contains("exit code 3"), "{}", run.failure());
    assert_eq!(run.sink.samples.len(), 1);
    assert_eq!(run.sink.skips.len(), 1);
}

fn a_plugin_that_never_answers_fails_the_session() {
    let run = run_records(
        "records-mute",
        r#","idle_timeout_s":1"#,
        &[b"one", b"two"],
        Duration::ZERO,
    );
    assert!(
        run.failure().contains("produced nothing"),
        "{}",
        run.failure()
    );
    assert!(run.sink.samples.is_empty() && run.sink.skips.is_empty());
}

fn a_gap_between_records_longer_than_the_idle_timeout() {
    let run = run_records(
        "records-echo",
        r#","idle_timeout_s":1"#,
        &[b"one", b"two"],
        Duration::from_millis(1500),
    );
    run.summary();
    assert_eq!(run.sink.samples.len(), 2);
    assert_eq!(debug_of(&run, 0, "pid"), debug_of(&run, 1, "pid"));
    assert!(run.sink.skips.is_empty());
}
