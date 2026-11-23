//! Stream admission: ticket lifetime, seat/session caps and cleanup before successor admission.

use crate::{Rig, authority, stream};
use playback::Authority;
use sessions::{
    CursorOptions,
    session::{Access, Command, ContentRef, Control, Refusal, Reply, ViewerId},
};
use std::time::Duration;
use tokio::time::advance;
use workspace::store::{ContentGen, DocCommand};

#[tokio::test(start_paused = true)]
async fn a_ticket_opens_one_stream_once_and_only_for_the_seat_and_key_it_was_issued_for() {
    let rig = Rig::new();
    let (owner, guest) = rig.shared(Access::Watch);
    let (_, key) = rig.open_video(&owner, "a.mp4");
    let Reply::Ticket(ticket) = guest
        .command(Command::RequestTicket(key.clone(), vec![]))
        .unwrap()
    else {
        panic!("a ticket");
    };

    let lease = rig.service.redeem(&ticket).unwrap();
    assert!(matches!(authority(&lease), Authority::Follow { .. }));
    assert_eq!(
        rig.service.redeem(&ticket).err(),
        Some(Refusal::BadTicket),
        "single use"
    );

    let Reply::Ticket(late) = guest
        .command(Command::RequestTicket(key.clone(), vec![]))
        .unwrap()
    else {
        panic!("a ticket");
    };
    advance(Duration::from_secs(11)).await;
    assert_eq!(
        rig.service.redeem(&late).err(),
        Some(Refusal::BadTicket),
        "expired"
    );

    let Reply::Ticket(bound) = guest
        .command(Command::RequestTicket(key.clone(), vec![]))
        .unwrap()
    else {
        panic!("a ticket");
    };
    let newer = rig.service.resume(guest.resume_token()).unwrap();
    assert_eq!(
        rig.service.redeem(&bound).err(),
        Some(Refusal::BadTicket),
        "bound to the lease it was asked under"
    );
    let Reply::Ticket(for_key) = newer
        .command(Command::RequestTicket(key.clone(), vec![]))
        .unwrap()
    else {
        panic!("a ticket");
    };
    let item = *owner.state().doc.workspace.open.iter().next().unwrap();
    owner
        .command(Command::Content(DocCommand::CloseViewer(item)))
        .unwrap();
    owner
        .command(Command::Content(DocCommand::OpenViewer(item)))
        .unwrap();
    rig.jobs.finish_all();
    assert_eq!(
        rig.service.redeem(&for_key).err(),
        Some(Refusal::BadTicket),
        "bound to the generation in its key"
    );
}

#[tokio::test(start_paused = true)]
async fn a_seat_holds_eight_streams_and_a_session_sixty_four() {
    let rig = Rig::new();
    let owner = rig
        .service
        .create(true, CursorOptions::default(), Default::default())
        .unwrap();
    owner
        .command(Command::Control(Control::Share {
            access: Access::Watch,
        }))
        .unwrap();
    let (_, key) = rig.open_video(&owner, "a.mp4");
    let seats: Vec<_> = (0..8)
        .map(|_| {
            rig.service
                .attach(owner.session(), true, CursorOptions::default())
                .unwrap()
        })
        .collect();
    let mut leases = Vec::new();

    for seat in &seats {
        for viewer in 1..=8 {
            leases.push(
                stream(
                    &rig.service,
                    seat,
                    &ContentRef {
                        viewer: ViewerId(viewer),
                        ..key.clone()
                    },
                )
                .unwrap(),
            );
        }
        assert_eq!(
            stream(
                &rig.service,
                seat,
                &ContentRef {
                    viewer: ViewerId(9),
                    ..key.clone()
                }
            )
            .err(),
            Some(Refusal::TooManyStreams)
        );
    }
    assert_eq!(
        stream(&rig.service, &owner, &key).err(),
        Some(Refusal::TooManyStreams),
        "the session is full"
    );

    leases.pop();
    assert!(
        stream(&rig.service, &owner, &key).is_ok(),
        "a released stream frees its place"
    );
}

#[tokio::test]
async fn releasing_and_replacing_viewers_frees_grants_and_pending_tickets_at_the_cap() {
    let rig = Rig::new();
    let (owner, _) = rig.shared(Access::Watch);
    let (_, key) = rig.open_video(&owner, "a.mp4");
    let leases: Vec<_> = (1..=8)
        .map(|viewer| {
            stream(
                &rig.service,
                &owner,
                &ContentRef {
                    viewer: ViewerId(viewer),
                    ..key.clone()
                },
            )
            .unwrap()
        })
        .collect();
    let ticket = |viewer, release| {
        let Reply::Ticket(ticket) = owner
            .command(Command::RequestTicket(
                ContentRef {
                    viewer: ViewerId(viewer),
                    ..key.clone()
                },
                release,
            ))
            .unwrap()
        else {
            panic!("a ticket");
        };
        ticket
    };
    assert_eq!(
        owner
            .command(Command::RequestTicket(key.clone(), vec![ViewerId(8); 9]))
            .err(),
        Some(Refusal::Invalid)
    );
    // A stale successor must not roll back the predecessor's cleanup.
    assert_eq!(
        owner
            .command(Command::RequestTicket(
                ContentRef {
                    viewer: ViewerId(9),
                    generation: ContentGen(0),
                    ..key.clone()
                },
                vec![ViewerId(8)]
            ))
            .err(),
        Some(Refusal::ContentUnavailable)
    );
    assert_eq!(authority(&leases[7]), Authority::Revoked);
    let cancelled = ticket(9, vec![]);
    let last = ticket(10, vec![ViewerId(9)]);
    assert_eq!(authority(&leases[7]), Authority::Revoked);
    assert_eq!(
        rig.service.redeem(&cancelled).err(),
        Some(Refusal::BadTicket)
    );
    let last = rig.service.redeem(&last).unwrap();
    let first = ticket(10, vec![]);
    assert_eq!(authority(&last), Authority::Revoked);
    let second = ticket(10, vec![]);
    assert_eq!(rig.service.redeem(&first).err(), Some(Refusal::BadTicket));
    assert!(rig.service.redeem(&second).is_ok());
}
