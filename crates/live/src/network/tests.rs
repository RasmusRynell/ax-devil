use super::*;

type Asked = (ScanLoad, Vec<(String, String)>);

async fn next(requests: &mut mpsc::UnboundedReceiver<Asked>) -> Option<Asked> {
    tokio::time::timeout(std::time::Duration::from_secs(1), requests.recv())
        .await
        .unwrap()
}

#[tokio::test]
async fn the_scan_at_launch_signs_in_with_the_saved_logins_as_the_settings_say() {
    let folder = tempfile::tempdir().unwrap();
    let (asked, mut requests) = mpsc::unbounded_channel();
    let network = Network::with_scanner(move |request| {
        let logins = request
            .logins
            .iter()
            .map(|login| (login.user.clone(), login.password.expose().to_owned()))
            .collect::<Vec<_>>();
        asked.send((request.load, logins)).unwrap();
        mpsc::channel(1).1
    });
    let state = |on: bool, sign_in: bool| {
        let path = folder.path().join(format!("{on}-{sign_in}.json"));
        std::fs::write(
            &path,
            format!(
                r#"{{"version": 1, "settings": {{"scan.on_startup": {on}, "scan.sign_in": {sign_in},
                "scan.logins": [{{"user": "root", "password": "first"}}, {{"user": "root", "password": "second"}}]}}}}"#
            ),
        )
        .unwrap();
        settings::Settings::load(&path).unwrap().state()
    };
    scan_on_startup(&network, &state(true, true));
    let both = vec![
        ("root".to_owned(), "first".to_owned()),
        ("root".to_owned(), "second".to_owned()),
    ];
    assert_eq!(next(&mut requests).await, Some((ScanLoad::Startup, both)));
    scan_on_startup(&network, &state(true, false));
    assert_eq!(
        next(&mut requests).await,
        Some((ScanLoad::Startup, Vec::new()))
    );
    let started = network.snapshot().scan;
    scan_on_startup(&network, &state(false, true));
    assert_eq!(
        network.snapshot().scan,
        started,
        "off means no scan at launch"
    );
    // The footer's choice for one scan wins over the setting, either way.
    let on = state(true, true);
    assert!(
        request_from_settings(&on, None, Some(false), ScanLoad::Normal)
            .unwrap()
            .logins
            .is_empty()
    );
}

#[test]
fn the_saved_range_is_scanned_as_the_variable_it_names_holds_it() {
    let folder = tempfile::tempdir().unwrap();
    let path = folder.path().join("default.json");
    // `PATH` is set wherever the tests run and is no address, so the refusal shows what the reference expanded to.
    std::fs::write(
        &path,
        r#"{"version": 1, "settings": {"scan.range": "$PATH"}}"#,
    )
    .unwrap();
    let state = settings::Settings::load(&path).unwrap().state();
    let Err(refused) = request_from_settings(&state, None, None, ScanLoad::Normal) else {
        panic!("a variable that holds no range is no range to scan");
    };
    assert!(!refused.reason.contains("$PATH"), "{}", refused.reason);
    assert_eq!(refused.location, Some(Location::field("scan.range")));
}

#[test]
fn only_a_whole_name_reference_in_a_scan_login_expands() {
    let lookup = |name: &str| (name == "AX_DEVIL_TARGET_PASS").then(|| "pw".to_owned());
    assert_eq!(expand("$AX_DEVIL_TARGET_PASS", lookup), "pw");
    assert_eq!(expand("$UNSET", lookup), "");
    for literal in ["$1A", "$A-B", "$ecret!", "$"] {
        assert_eq!(expand(literal, lookup), literal);
    }
}

fn found_signed_in(index: usize) -> Found {
    Found {
        addr: Ipv4Addr::new(10, 9, 9, 2),
        web: camera::Web {
            scheme: Scheme::Http,
            port: 80,
        },
        identity: camera::Identity {
            model: String::new(),
            serial: String::new(),
            firmware: String::new(),
            product_type: String::new(),
            architecture: String::new(),
            soc: String::new(),
        },
        uptime: None,
        sign_in: SignIn::SignedIn {
            login_index: index,
            architecture: String::new(),
            soc: String::new(),
        },
    }
}

fn request(users: &[&str]) -> ScanRequest {
    ScanRequest {
        targets: Targets::parse("10.9.9.0/30").unwrap(),
        logins: users
            .iter()
            .map(|user| Login {
                user: (*user).to_owned(),
                password: Secret::new("pw".to_owned()),
            })
            .collect(),
        load: ScanLoad::Normal,
        ports: ScanPorts::preferring(Scheme::Http),
    }
}

#[tokio::test]
async fn a_login_index_means_the_login_its_scan_tried_and_nothing_once_another_scan_began() {
    let network = Network::with_scanner(|_| {
        let (found, rows) = mpsc::channel(1);
        found.try_send(Ok(found_signed_in(1))).unwrap();
        rows
    });
    // `PATH` is set wherever the tests run: the scan sends its value, a camera made from the row keeps the text.
    let first = network.rescan(request(&["a", "$PATH"]));
    let mut snapshots = network.subscribe();
    tokio::time::timeout(
        Duration::from_secs(10),
        snapshots.wait_for(|snapshot| snapshot.rows.len() == 1),
    )
    .await
    .expect("the row in time")
    .unwrap();
    let user = |login: Option<Login>| login.map(|login| login.user);
    assert_eq!(
        user(network.accepted_login(first, 1)),
        std::env::var("PATH").ok()
    );
    assert_eq!(
        user(network.signed_in("10.9.9.2").map(|(_, login)| login)),
        Some("$PATH".to_owned())
    );
    assert_eq!(user(network.accepted_login(first, 0)), Some("a".to_owned()));
    assert_eq!(user(network.accepted_login(first, 2)), None);

    let second = network.rescan(request(&["c"]));
    assert_eq!(user(network.accepted_login(first, 0)), None);
    assert_eq!(
        user(network.accepted_login(second, 0)),
        Some("c".to_owned())
    );
}

#[tokio::test]
async fn a_cancel_that_wins_the_race_with_a_publish_leaves_the_scan_not_scanning() {
    let network = Network::with_scanner(|_| mpsc::channel(1).1);
    let number = network.rescan(request(&[]));
    network.cancel();
    // The scan's timer was waiting for the lock cancel held, and publishes once it has it.
    let rows = [(Ipv4Addr::new(10, 9, 9, 2), row(&found_signed_in(0)))];
    network.publish(number, &rows, true, None);
    assert!(!network.snapshot().scanning);
    network.publish(number, &rows, false, Some("failed".to_owned()));
    assert!(!network.snapshot().scanning);
    assert_eq!(network.snapshot().failure, None);
}

#[tokio::test]
async fn a_scan_that_panics_ends_as_a_failure_the_next_scan_clears() {
    let calls = std::sync::atomic::AtomicUsize::new(0);
    let network = Network::with_scanner(move |_| {
        let earlier = calls.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        assert!(earlier > 0, "the first scan breaks");
        mpsc::channel(1).1
    });
    let mut snapshots = network.subscribe();
    let first = network.rescan(request(&[]));
    let ended = snapshots
        .wait_for(|now| now.scan == first && !now.scanning)
        .await
        .unwrap()
        .clone();
    assert!(ended.failure.is_some());

    let second = network.rescan(request(&[]));
    let now = network.snapshot();
    assert_eq!(
        (now.scan, now.scanning, now.failure.as_deref()),
        (second, true, None)
    );
}

#[tokio::test]
async fn a_scanner_that_reports_it_broke_is_a_failure_apart_from_one_that_ran_out() {
    let network = Network::with_scanner(|request| {
        let (tx, rx) = mpsc::channel(2);
        if request.logins.is_empty() {
            tx.try_send(Ok(found_signed_in(0))).unwrap();
            tx.try_send(Err(ScanFailed)).unwrap();
        }
        rx
    });
    let mut snapshots = network.subscribe();
    let broke = network.rescan(request(&[]));
    let ended = snapshots
        .wait_for(|now| now.scan == broke && !now.scanning)
        .await
        .unwrap()
        .clone();
    assert!(ended.failure.is_some());

    let finished = network.rescan(request(&["a"]));
    let ended = snapshots
        .wait_for(|now| now.scan == finished && !now.scanning)
        .await
        .unwrap()
        .clone();
    assert_eq!(ended.failure, None);
}
