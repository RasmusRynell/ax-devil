//! The wire types that mirror a `sessions` type, converted both ways where the data travels both ways. Every
//! conversion is an exhaustive `match` or a destructure without `..`, so a variant or field added on either side does
//! not compile until the other side answers it.

use crate::{
    Access, ClockIntent, ControlCommand, EndedReason, RefusalCode, SeatAccess, SeatMode, SeatRole,
    Sidebar, SidebarView, View, ViewerView,
};
use foundation::ItemId;
use sessions::{
    EndReason, Kind, Mode,
    session::{self, Control, Refusal},
};
use std::{collections::BTreeMap, fmt};

/// A view named an item that is not an item id.
#[derive(Debug, PartialEq, Eq)]
pub struct InvalidItemId;

impl fmt::Display for InvalidItemId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("that is not an item id")
    }
}

impl std::error::Error for InvalidItemId {}

impl From<session::SidebarView> for SidebarView {
    fn from(view: session::SidebarView) -> Self {
        match view {
            session::SidebarView::Workspace => Self::Workspace,
            session::SidebarView::Live => Self::Live,
            session::SidebarView::Offline => Self::Offline,
            session::SidebarView::Bookmarks => Self::Bookmarks,
            session::SidebarView::Plugins => Self::Plugins,
            session::SidebarView::Sessions => Self::Sessions,
        }
    }
}

impl From<SidebarView> for session::SidebarView {
    fn from(view: SidebarView) -> Self {
        match view {
            SidebarView::Workspace => Self::Workspace,
            SidebarView::Live => Self::Live,
            SidebarView::Offline => Self::Offline,
            SidebarView::Bookmarks => Self::Bookmarks,
            SidebarView::Plugins => Self::Plugins,
            SidebarView::Sessions => Self::Sessions,
        }
    }
}

impl From<session::Access> for Access {
    fn from(access: session::Access) -> Self {
        match access {
            session::Access::Watch => Self::Watch,
            session::Access::Explore => Self::Explore,
            session::Access::Edit => Self::Edit,
        }
    }
}

impl From<Access> for session::Access {
    fn from(access: Access) -> Self {
        match access {
            Access::Watch => Self::Watch,
            Access::Explore => Self::Explore,
            Access::Edit => Self::Edit,
        }
    }
}

impl From<Access> for SeatAccess {
    fn from(access: Access) -> Self {
        match access {
            Access::Watch => Self::Watch,
            Access::Explore => Self::Explore,
            Access::Edit => Self::Edit,
        }
    }
}

impl From<playback::ClockIntent> for ClockIntent {
    fn from(intent: playback::ClockIntent) -> Self {
        let playback::ClockIntent {
            frame,
            playing,
            speed_centi,
            at_us,
        } = intent;
        Self {
            frame,
            playing,
            speed_centi,
            at_us,
        }
    }
}

impl From<ClockIntent> for playback::ClockIntent {
    fn from(intent: ClockIntent) -> Self {
        let ClockIntent {
            frame,
            playing,
            speed_centi,
            at_us,
        } = intent;
        Self {
            frame,
            playing,
            speed_centi,
            at_us,
        }
    }
}

impl From<session::Sidebar> for Sidebar {
    fn from(sidebar: session::Sidebar) -> Self {
        let session::Sidebar { view, open } = sidebar;
        Self {
            view: view.into(),
            open,
        }
    }
}

impl From<Sidebar> for session::Sidebar {
    fn from(sidebar: Sidebar) -> Self {
        let Sidebar { view, open } = sidebar;
        Self {
            view: view.into(),
            open,
        }
    }
}

impl From<session::ViewerView> for ViewerView {
    fn from(viewer: session::ViewerView) -> Self {
        let session::ViewerView { clock } = viewer;
        Self {
            clock: clock.map(Into::into),
        }
    }
}

impl From<ViewerView> for session::ViewerView {
    fn from(viewer: ViewerView) -> Self {
        let ViewerView { clock } = viewer;
        Self {
            clock: clock.map(Into::into),
        }
    }
}

impl From<&session::View> for View {
    fn from(view: &session::View) -> Self {
        let session::View {
            sidebar,
            active,
            viewers,
        } = view;
        Self {
            sidebar: (*sidebar).into(),
            active: active.map(|item| item.to_string()),
            viewers: viewers
                .iter()
                .map(|(item, viewer)| (item.to_string(), (*viewer).into()))
                .collect(),
        }
    }
}

impl TryFrom<View> for session::View {
    type Error = InvalidItemId;

    fn try_from(view: View) -> Result<Self, InvalidItemId> {
        let View {
            sidebar,
            active,
            viewers,
        } = view;
        let item = |text: &str| ItemId::parse(text).ok_or(InvalidItemId);
        let viewers = viewers
            .into_iter()
            .map(|(text, viewer)| Ok((item(&text)?, viewer.into())))
            .collect::<Result<BTreeMap<_, _>, _>>()?;
        Ok(Self {
            sidebar: sidebar.into(),
            active: active.as_deref().map(item).transpose()?,
            viewers,
        })
    }
}

impl From<ControlCommand> for Control {
    fn from(control: ControlCommand) -> Self {
        match control {
            ControlCommand::Share { access } => Self::Share(access.into()),
            ControlCommand::SetAccess { access } => Self::SetAccess(access.into()),
            ControlCommand::RegenerateLink => Self::RegenerateLink,
            ControlCommand::Stop => Self::Stop,
        }
    }
}

impl From<Kind> for SeatRole {
    fn from(kind: Kind) -> Self {
        match kind {
            Kind::Owner => Self::Owner,
            Kind::Watcher => Self::Watcher,
        }
    }
}

impl From<Mode> for SeatMode {
    fn from(mode: Mode) -> Self {
        match mode {
            Mode::Following => Self::Following,
            Mode::Exploring => Self::Exploring,
        }
    }
}

impl From<EndReason> for EndedReason {
    fn from(reason: EndReason) -> Self {
        match reason {
            EndReason::Stopped => Self::Stopped,
            EndReason::OwnerGone => Self::OwnerGone,
            EndReason::Replaced => Self::Replaced,
        }
    }
}

impl From<Refusal> for RefusalCode {
    fn from(refusal: Refusal) -> Self {
        match refusal {
            Refusal::SeatGone => Self::SeatGone,
            Refusal::NotAllowed => Self::NotAllowed,
            Refusal::NotSharing => Self::NotSharing,
            Refusal::AlreadySharing => Self::AlreadySharing,
            Refusal::ContentUnavailable => Self::ContentUnavailable,
            Refusal::TooManyStreams => Self::TooManyStreams,
            Refusal::BadTicket => Self::BadTicket,
            Refusal::Unsupported => Self::Unsupported,
            Refusal::Link => Self::Link,
            Refusal::Full => Self::Full,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_view_keeps_everything_it_shows_through_the_wire_and_back() {
        let (front, behind) = (ItemId::generate(), ItemId::generate());
        let view = session::View {
            sidebar: session::Sidebar {
                view: session::SidebarView::Sessions,
                open: false,
            },
            active: Some(front),
            viewers: BTreeMap::from([
                (front, session::ViewerView::default()),
                (
                    behind,
                    session::ViewerView {
                        clock: Some(playback::ClockIntent {
                            frame: 7,
                            playing: true,
                            speed_centi: 150,
                            at_us: -3,
                        }),
                    },
                ),
            ]),
        };
        let wire = View::from(&view);
        let json = serde_json::to_string(&wire).unwrap();
        let received: View = serde_json::from_str(&json).unwrap();
        assert_eq!(session::View::try_from(received), Ok(view));

        let mut bad = wire;
        bad.active = Some("not an item".into());
        assert_eq!(session::View::try_from(bad), Err(InvalidItemId));

        for view in [
            session::SidebarView::Workspace,
            session::SidebarView::Live,
            session::SidebarView::Offline,
            session::SidebarView::Bookmarks,
            session::SidebarView::Plugins,
            session::SidebarView::Sessions,
        ] {
            assert_eq!(session::SidebarView::from(SidebarView::from(view)), view);
        }
    }
}
