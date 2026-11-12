use super::*;

#[test]
fn expanded_live_defaults_never_appear_in_refusals() {
    const SENTINEL: &str = "AX-SYNTHETIC-SECRET";
    let fields: [fn(&mut LiveDefaults); 5] = [
        |d| d.rtsp_handler = SENTINEL.into(),
        |d| d.mqtt.handler = SENTINEL.into(),
        |d| d.datahub.handler = SENTINEL.into(),
        |d| d.mqtt.source_key = SENTINEL.into(),
        |d| d.datahub.topic = SENTINEL.into(),
    ];
    let mut refusals = 0;
    for field in fields {
        for mode in [
            LiveMode::None,
            LiveMode::Rtsp,
            LiveMode::Mqtt,
            LiveMode::DataHub,
        ] {
            let mut values = defaults();
            field(&mut values);
            values.overlay = mode;
            if let Err(error) = live(host("127.0.0.1"), &values, &Handlers::built_in()) {
                assert!(!error.reason.contains(SENTINEL), "{error}");
                assert!(!error.to_string().contains(SENTINEL), "{error}");
                assert_eq!(error.location, Some(Location::field("handler")));
                refusals += 1;
            }
        }
    }
    assert_eq!(
        refusals, 3,
        "each invalid handler is refused in its own mode"
    );
}
