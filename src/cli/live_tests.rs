use super::{
    tests::{parse, stream_defaults},
    *,
};
use workspace::{Environment, Source};

/// What `ax-devil live` with `args` opens when the variables of the process are `variables`.
fn live(variables: &[(&str, &str)], args: &[&str]) -> Result<Option<Launch>, String> {
    let variables: Vec<(String, String)> = variables
        .iter()
        .map(|(name, value)| ((*name).into(), (*value).into()))
        .collect();
    let env = Environment::new(move |name| {
        variables
            .iter()
            .find(|(set, _)| set == name)
            .map(|(_, value)| value.clone())
    });
    let recovery = RecoveryFolder::new(std::path::PathBuf::from("unused"));
    launch::launch(
        parse(&[&["live"], args].concat()).unwrap().command,
        &Handlers::built_in(),
        &recovery,
        &env,
        stream_defaults,
    )
}

#[test]
fn a_host_reference_that_expands_to_nothing_is_refused_before_launch_and_names_the_reference() {
    for variables in [&[][..], &[("AX_DEVIL_TARGET_ADDR", "")]] {
        let error = live(variables, &[]).err().unwrap();
        assert!(error.contains("$AX_DEVIL_TARGET_ADDR"), "{error}");
    }
    let error = live(&[("AX_DEVIL_TARGET_ADDR", "")], &["--host", "$OTHER"])
        .err()
        .unwrap();
    assert!(error.contains("$OTHER"), "{error}");
    let mqtt = ["--overlay", "mqtt", "--host", "192.0.2.7"];
    let error = live(&[], &mqtt).err().unwrap();
    assert!(error.contains("$AX_DEVIL_MQTT_BROKER_ADDR"), "{error}");
}

#[test]
fn an_expanded_host_launches_and_the_item_keeps_the_references_as_written() {
    let variables = [
        ("AX_DEVIL_TARGET_ADDR", "192.0.2.7"),
        ("AX_DEVIL_TARGET_PASS", "hunter2-secret"),
    ];
    let Some(Launch::Items(items)) = live(&variables, &[]).unwrap() else {
        panic!("a live command opens its item");
    };
    let Source::Live(recipe) = &items[0].recipe.source else {
        panic!("a live item has a live recipe");
    };
    assert_eq!(recipe.host, "$AX_DEVIL_TARGET_ADDR");
    assert_eq!(recipe.password.expose(), "$AX_DEVIL_TARGET_PASS");
}

#[test]
fn a_datahub_channel_past_32_bits_is_refused_and_the_largest_one_opens() {
    let websocket = |channel: &'static str| {
        let flags = [
            "--overlay",
            "websocket",
            "--topic",
            "t",
            "--channel-id",
            channel,
        ];
        live(&[("AX_DEVIL_TARGET_ADDR", "192.0.2.7")], &flags)
    };
    let error = websocket("4294967296").err().unwrap();
    assert!(
        error.contains("Channel ID must be at most 4294967295."),
        "{error}"
    );
    assert!(websocket("4294967295").is_ok());
}

#[test]
fn the_rtsp_route_is_spelled_as_the_python_app_does_in_any_case() {
    let route = |flag: &'static str| {
        let flags = ["--rtsp-route", flag];
        let Ok(Some(Launch::Items(items))) = live(&[("AX_DEVIL_TARGET_ADDR", "192.0.2.7")], &flags)
        else {
            return None;
        };
        let Source::Live(recipe) = &items[0].recipe.source else {
            return None;
        };
        Some(recipe.rtsp_route)
    };
    assert_eq!(route("web_api"), Some(workspace::RtspRoute::WebApi));
    assert_eq!(route("WEB_API"), Some(workspace::RtspRoute::WebApi));
    assert_eq!(route("direct"), Some(workspace::RtspRoute::Direct));
}
