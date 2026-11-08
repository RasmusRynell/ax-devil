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
        rtsp_route: RtspRoute::Direct,
        resolution: None,
        url: None,
        overlay: LiveOverlay::None,
        kept: Default::default(),
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
fn a_saved_login_for_an_environment_reference_is_not_copied_to_another_variable_by_case() {
    let saved = LiveRecipe {
        host: "$CAM".into(),
        username: "root".into(),
        password: Secret::new("literal"),
        protocol: Protocol::Https,
        port: 0,
        head: 1,
        rtsp_port: 554,
        rtsp_route: RtspRoute::Direct,
        resolution: None,
        url: None,
        overlay: LiveOverlay::None,
        kept: Default::default(),
    };
    let copied = |host_text: &str| {
        let mut new = host(host_text);
        new.copy_login(&saved, &defaults(), LoginUse::Discovery)
            .unwrap();
        new.password
    };
    assert_eq!(copied("$CAM").unwrap().expose(), "literal");
    assert!(copied("$cam").is_none());
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
        rtsp_route: RtspRoute::Direct,
        resolution: None,
        url: None,
        overlay: LiveOverlay::None,
        kept: Default::default(),
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
fn through_the_web_api_the_login_goes_to_the_web_port_so_changing_route_asks_again() {
    let saved = |route, port| LiveRecipe {
        host: "cam.local".into(),
        username: "root".into(),
        password: Secret::new("literal"),
        protocol: Protocol::Https,
        port,
        head: 1,
        rtsp_port: 554,
        rtsp_route: route,
        resolution: None,
        url: None,
        overlay: LiveOverlay::None,
        kept: Default::default(),
    };
    let copy = |saved: &LiveRecipe, route, port| {
        let mut new = NewLive {
            rtsp_route: Some(route),
            port: Some(port),
            ..host("cam.local")
        };
        new.copy_login(saved, &defaults(), LoginUse::Stream)
            .map(|()| new.password.is_some())
    };
    let direct = saved(RtspRoute::Direct, 0);
    let web = saved(RtspRoute::WebApi, 0);
    assert_eq!(web.rtsp_endpoint(), ("cam.local".to_owned(), 443));
    assert!(
        copy(&web, RtspRoute::WebApi, 0).unwrap(),
        "the same route and port"
    );
    assert!(
        copy(&web, RtspRoute::WebApi, 443).unwrap(),
        "443 is the standard port"
    );
    for (saved, route, port) in [
        (&direct, RtspRoute::WebApi, 0),
        (&web, RtspRoute::Direct, 0),
    ] {
        let error = copy(saved, route, port).unwrap_err();
        assert_eq!(
            error.location,
            Some(Location::field("password")),
            "{route:?} {port}"
        );
    }
    // Another web port is another camera connection: nothing is copied.
    assert!(!copy(&web, RtspRoute::WebApi, 8443).unwrap());
}

/// The login is copied to the URL's destination as the connection reads the URL, not as it looks to a hand-written
/// parser: a backslash, a second `@`, a path that holds `@cam:554` and the like must not pass for the camera.
#[test]
fn a_saved_login_is_copied_only_where_the_connection_would_send_it() {
    let saved = LiveRecipe {
        host: "cam".into(),
        username: "root".into(),
        password: Secret::new("literal"),
        protocol: Protocol::Https,
        port: 0,
        head: 1,
        rtsp_port: 554,
        rtsp_route: RtspRoute::Direct,
        resolution: None,
        url: None,
        overlay: LiveOverlay::None,
        kept: Default::default(),
    };
    let copy = |url: &str| {
        let mut new = NewLive {
            url: Some(url.into()),
            ..host("cam")
        };
        new.copy_login(&saved, &defaults(), LoginUse::Stream)
            .map(|()| new.password.is_some())
    };
    for url in [
        "http://cam:554/s",
        "HTTP://CAM:554/s",
        "http:root:secret@cam:554/s",
        "http:////root:secret@cam:554/s",
        "http://root:p%40ss:word@cam:554/s",
        "http://a@b:c@cam:554/s",
        "rtsp://cam/s",
        // The login is the URL's own (user `cam`), so the camera's goes nowhere, whatever the host.
        "http://cam:554@attacker:8080/s",
    ] {
        assert_eq!(copy(url).ok(), Some(true), "{url}");
    }
    for url in [
        r"http://attacker:8080\@cam:554/s",
        "http://attacker:8080/@cam:554/s",
        "http://attacker:8080#@cam:554/s",
        "http://attacker:8080?@cam:554/s",
        "http://ca\tm:554/s",
        "rtsps://cam:554/s",
        "not a url @cam:554",
    ] {
        let error = copy(url).unwrap_err();
        assert_eq!(error.location, Some(Location::field("password")), "{url}");
    }
    // A saved stream whose own URL cannot be read sent the camera's login nowhere, so it approves only the camera's own
    // address, and keeps its unreadable URL as it is without asking for the login again.
    let broken_url = r"http://attacker:8080\@cam:554/s";
    let broken = LiveRecipe {
        url: Some(broken_url.into()),
        ..saved.clone()
    };
    let moves = |url: Option<&str>| {
        let mut new = NewLive {
            url: url.map(Into::into),
            ..host("cam")
        };
        new.copy_login(&broken, &defaults(), LoginUse::Stream)
    };
    assert!(moves(None).is_ok());
    assert!(moves(Some(broken_url)).is_ok());
    assert!(moves(Some("rtsp://cam/s")).is_ok());
    assert!(moves(Some("http://attacker:8080/s")).is_err());
}

/// A stream whose URL has a login of its own never sent the camera's login to that URL's host, so editing the URL to
/// drop its login must not hand the camera's password to that host: the camera login is trusted where it was entered.
#[test]
fn a_url_with_its_own_login_does_not_make_its_host_a_destination_for_the_camera_login() {
    let saved = |route, url: &str| LiveRecipe {
        host: "cam".into(),
        username: "cam-user".into(),
        password: Secret::new("CAMERA-PASSWORD"),
        protocol: Protocol::Https,
        port: 0,
        head: 1,
        rtsp_port: 554,
        rtsp_route: route,
        resolution: None,
        url: Some(url.into()),
        overlay: LiveOverlay::None,
        kept: Default::default(),
    };
    let edit = |saved: &LiveRecipe, url: &str| {
        let mut new = NewLive {
            url: Some(url.into()),
            ..host("cam")
        };
        new.copy_login(saved, &defaults(), LoginUse::Stream)
            .map(|()| new.password.is_some())
    };
    // Directly and through the web API.
    for (route, with_login, without, on_camera, camera_without) in [
        (
            RtspRoute::Direct,
            "rtsp://url-user:URL-PASSWORD@other:8554/s",
            "rtsp://other:8554/s",
            "rtsp://url-user:URL-PASSWORD@cam:554/s",
            "rtsp://cam/s",
        ),
        (
            RtspRoute::WebApi,
            "https://url-user:URL-PASSWORD@other/s",
            "https://other/s",
            "https://url-user:URL-PASSWORD@cam/s",
            "https://cam/s",
        ),
    ] {
        let elsewhere = saved(route, with_login);
        let error = edit(&elsewhere, without).unwrap_err();
        assert_eq!(
            error.location,
            Some(Location::field("password")),
            "{without}"
        );
        // Keeping the URL's login is not a copy of the camera's, and the camera's own address still takes it.
        assert!(edit(&elsewhere, with_login).is_ok());
        let at_camera = saved(route, on_camera);
        assert!(
            edit(&at_camera, camera_without).unwrap(),
            "{camera_without}"
        );
        // Without a login of its own the URL's host is where the camera login went, so it stays trusted there.
        assert!(edit(&saved(route, without), without).unwrap(), "{without}");
    }
}

/// A stream with no login has none to protect: moving it to another route or address copies two empty texts.
#[test]
fn an_anonymous_stream_may_change_route_or_address_and_one_with_a_login_may_not() {
    let saved = |username: &str, password: &str| LiveRecipe {
        host: "cam".into(),
        username: username.into(),
        password: Secret::new(password),
        protocol: Protocol::Https,
        port: 0,
        head: 1,
        rtsp_port: 554,
        rtsp_route: RtspRoute::Direct,
        resolution: None,
        url: None,
        overlay: LiveOverlay::None,
        kept: Default::default(),
    };
    let moved = |saved: &LiveRecipe| {
        let mut new = NewLive {
            rtsp_route: Some(RtspRoute::WebApi),
            url: Some("rtsp://other/s".into()),
            ..host("cam")
        };
        new.copy_login(saved, &defaults(), LoginUse::Stream)
    };
    assert!(moved(&saved("", "")).is_ok());
    for (user, password) in [("root", "literal"), ("", "literal"), ("root", "")] {
        let error = moved(&saved(user, password)).unwrap_err();
        assert_eq!(
            error.location,
            Some(Location::field("password")),
            "{user:?}"
        );
    }
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
        rtsp_route: RtspRoute::Direct,
        resolution: None,
        url: Some("rtsp://[2001:db8::1]/s".into()),
        overlay: LiveOverlay::None,
        kept: Default::default(),
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

#[test]
fn a_blank_broker_password_is_copied_only_for_the_same_broker_and_user() {
    let saved = LiveRecipe {
        host: "cam.local".into(),
        username: "root".into(),
        password: Secret::new("literal"),
        protocol: Protocol::Https,
        port: 0,
        head: 1,
        rtsp_port: 554,
        rtsp_route: RtspRoute::Direct,
        resolution: None,
        url: None,
        overlay: LiveOverlay::Mqtt {
            handler: "ADF_V1_FRAME".into(),
            broker_host: "broker.local".into(),
            broker_port: 1884,
            broker_user: "mqtt".into(),
            broker_pass: Secret::new("broker-secret"),
            source_key: "key".into(),
        },
        kept: Default::default(),
    };
    let copy = |change: fn(&mut NewMqtt)| {
        let mut mqtt = NewMqtt {
            broker_host: Some("BROKER.local".into()),
            broker_port: Some(1884),
            broker_user: Some("mqtt".into()),
            ..NewMqtt::default()
        };
        change(&mut mqtt);
        let mut new = NewLive {
            overlay: Some(NewLiveOverlay::Mqtt(mqtt)),
            ..host("other.local")
        };
        new.copy_login(&saved, &defaults(), LoginUse::Stream)
            .unwrap();
        let Some(NewLiveOverlay::Mqtt(mqtt)) = new.overlay else {
            panic!("an MQTT overlay");
        };
        mqtt.broker_pass.map(|secret| secret.expose().to_owned())
    };

    assert_eq!(copy(|_| {}).as_deref(), Some("broker-secret"));
    assert_eq!(
        copy(|mqtt| mqtt.broker_pass = Some(Secret::new("typed"))).as_deref(),
        Some("typed")
    );
    for change in [
        (|mqtt: &mut NewMqtt| mqtt.broker_host = Some("other.local".into())) as fn(&mut NewMqtt),
        |mqtt| mqtt.broker_port = Some(1883),
        |mqtt| mqtt.broker_user = Some("viewer".into()),
    ] {
        assert_eq!(copy(change), None);
    }
}
