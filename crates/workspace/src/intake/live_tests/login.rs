//! Copying the login saved with one stream into another.

use super::*;

#[test]
fn another_stream_copies_the_saved_login_only_for_the_same_camera() {
    let saved = LiveRecipe {
        host: "cam.local".into(),
        username: "root".into(),
        password: Secret::new("literal"),
        protocol: Protocol::Https,
        port: 0,
        head: 1,
        rtsp_port: 554,
        resolution: None,
        url: None,
        overlay: LiveOverlay::None,
    };
    let copy = |change: fn(&mut NewLive)| {
        let mut new = host("CAM.local");
        change(&mut new);
        new.copy_login(&saved, &defaults(), LoginUse::Stream)
            .unwrap();
        new
    };
    let login = |new: &NewLive| {
        (
            new.username.clone().unwrap_or_default(),
            new.password
                .as_ref()
                .map(|secret| secret.expose().to_owned()),
        )
    };

    // The same host, with the standard port written either way: the user and password fill in.
    let same = (String::from("root"), Some(String::from("literal")));
    assert_eq!(login(&copy(|_| {})), same);
    assert_eq!(login(&copy(|new| new.port = Some(443))), same);
    // A password the person entered stays.
    let typed = copy(|new| new.password = Some(Secret::new("typed")));
    assert_eq!(login(&typed).1.as_deref(), Some("typed"));
    // Another host, protocol, port or user gets nothing of the saved password.
    for change in [
        (|new: &mut NewLive| new.host = Some("other.local".into())) as fn(&mut NewLive),
        |new| new.protocol = Some(Protocol::Http),
        |new| new.port = Some(8443),
        |new| new.username = Some("viewer".into()),
    ] {
        assert_eq!(login(&copy(change)).1, None);
    }
}

#[test]
fn an_rtsp_endpoint_elsewhere_gets_no_saved_login() {
    let saved = LiveRecipe {
        host: "cam.local".into(),
        username: "root".into(),
        password: Secret::new("literal"),
        protocol: Protocol::Https,
        port: 0,
        head: 1,
        rtsp_port: 554,
        resolution: None,
        url: None,
        overlay: LiveOverlay::None,
    };
    let other_rtsp = || LiveRecipe {
        rtsp_port: 8554,
        ..saved.clone()
    };
    let copy = |url: &str| {
        let mut new = NewLive {
            url: Some(url.into()),
            ..host("cam.local")
        };
        new.copy_login(&saved, &defaults(), LoginUse::Stream)
            .map(|()| new.password)
    };
    // The camera's own RTSP address, spelled in the URL, takes the login; another host or port is refused.
    assert_eq!(
        copy("rtsp://CAM.local/stream").unwrap().unwrap().expose(),
        "literal"
    );
    for url in ["rtsp://other.example/s", "rtsp://cam.local:8554/s"] {
        let error = copy(url).unwrap_err();
        assert_eq!(error.location, Some(Location::field("password")));
    }
    // The same web connection with another RTSP port is another service, with or without a URL.
    let mut other_port = NewLive {
        rtsp_port: Some(8554),
        ..host("cam.local")
    };
    let error = other_port
        .copy_login(&saved, &defaults(), LoginUse::Stream)
        .unwrap_err();
    assert_eq!(error.location, Some(Location::field("password")));
    // Questions to the web interface alone take the login whatever the saved RTSP port or URL.
    let mut discovery = host("cam.local");
    discovery
        .copy_login(&other_rtsp(), &defaults(), LoginUse::Discovery)
        .unwrap();
    assert_eq!(discovery.password.unwrap().expose(), "literal");
    // A scanned camera's login goes to its own address only: a URL elsewhere is refused.
    let scanned = |url: Option<&str>| {
        let new = NewLive {
            url: url.map(Into::into),
            ..host("10.0.0.2")
        };
        new.check_login_destination(&new.camera_endpoint(&defaults()), &defaults())
    };
    scanned(None).unwrap();
    scanned(Some("rtsp://10.0.0.2/s")).unwrap();
    scanned(Some("rtsp://attacker.example/s")).unwrap_err();
    // With the login entered there is nothing to copy, so the URL is free.
    let mut typed = NewLive {
        url: Some("rtsp://other.example/s".into()),
        username: Some("root".into()),
        password: Some(Secret::new("typed")),
        ..host("cam.local")
    };
    typed
        .copy_login(&saved, &defaults(), LoginUse::Stream)
        .unwrap();
}

#[test]
fn an_ipv6_camera_is_the_same_endpoint_however_it_is_spelled() {
    let saved = LiveRecipe {
        host: "2001:db8::1".into(),
        username: "root".into(),
        password: Secret::new("literal"),
        protocol: Protocol::Https,
        port: 0,
        head: 1,
        rtsp_port: 554,
        resolution: None,
        url: Some("rtsp://[2001:db8::1]/s".into()),
        overlay: LiveOverlay::None,
    };
    // The saved URL brackets the address and the new stream's host does not.
    let mut new = host("2001:db8::1");
    new.copy_login(&saved, &defaults(), LoginUse::Stream)
        .unwrap();
    assert_eq!(new.password.unwrap().expose(), "literal");
    // An expanded spelling of the address is the same destination.
    let expanded = host("2001:DB8:0:0:0:0:0:1");
    expanded
        .check_login_destination(&("[2001:db8::1]".into(), 554), &defaults())
        .unwrap();
}
