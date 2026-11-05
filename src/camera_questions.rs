//! The questions the live form asks a camera: which stream sizes it offers and which analytics and DataHub topics it
//! publishes. Each contacts the camera with its login; the live routes are adapters over these functions.

use std::time::Duration;

use api_types::live::{
    LiveHeadResolutions, LiveReason, LiveResolution, LiveResolutions, LiveSources as SourcesWire,
};
use camera::Device;
use workspace::ResolvedCamera;

use crate::live_sources::{camera_of, reason};

fn device(camera: &ResolvedCamera, timeout: Duration) -> Result<Device, LiveReason> {
    camera_of(camera, timeout)
        .and_then(|camera| Device::new(&camera))
        .map_err(|error| {
            tracing::warn!("A camera cannot be addressed: {error}");
            reason(error.kind)
        })
}

fn failed(what: &str, error: &camera::CameraError) -> LiveReason {
    tracing::warn!("Asking a camera for {what} failed: {error}");
    reason(error.kind)
}

/// The stream sizes of every head of `camera`. Contacts the camera, with its login.
pub async fn resolutions(
    camera: &ResolvedCamera,
    timeout: Duration,
) -> Result<LiveResolutions, LiveReason> {
    let found = device(camera, timeout)?
        .image_sizes()
        .await
        .map_err(|error| failed("its resolutions", &error))?;
    let wire = |sizes: Vec<camera::Resolution>| {
        sizes
            .into_iter()
            .map(|size| LiveResolution {
                width: size.width,
                height: size.height,
            })
            .collect()
    };
    Ok(LiveResolutions {
        listed: found.listed,
        common: wire(found.common),
        heads: found
            .heads
            .into_iter()
            .map(|(head, sizes)| LiveHeadResolutions {
                head: head.number(),
                resolutions: wire(sizes),
            })
            .collect(),
    })
}

/// The analytics data sources `camera` can publish over MQTT. Contacts the camera, with its login.
pub async fn analytics_sources(
    camera: &ResolvedCamera,
    timeout: Duration,
) -> Result<SourcesWire, LiveReason> {
    let sources = device(camera, timeout)?
        .analytics_sources()
        .await
        .map_err(|error| failed("its analytics sources", &error))?;
    Ok(SourcesWire {
        sources: sources.into_iter().map(|source| source.0).collect(),
    })
}

/// The DataHub topics `camera` offers. Contacts the camera, with its login.
pub async fn datahub_topics(
    camera: &ResolvedCamera,
    timeout: Duration,
) -> Result<Vec<String>, LiveReason> {
    device(camera, timeout)?
        .datahub_topics()
        .await
        .map_err(|error| failed("its DataHub topics", &error))
}
