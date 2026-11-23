use super::*;
use std::io::Write;

struct FullDisk;
impl Write for FullDisk {
    fn write(&mut self, _: &[u8]) -> io::Result<usize> {
        Err(io::ErrorKind::StorageFull.into())
    }
    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

#[test]
fn a_failed_first_write_removes_the_reservation_but_a_failed_update_keeps_recovery() {
    let dir = tempfile::tempdir().unwrap();
    let folder = RecoveryFolder::new(dir.path().to_owned());
    for previously_written in [false, true] {
        let copy = folder.reserve("Untitled").unwrap();
        let path = copy.path().to_owned();
        assert!(path.is_file(), "reservation succeeds before the disk fills");
        if previously_written {
            copy.write(&Workspace::default(), None).unwrap();
        }
        let changed = Workspace {
            name: Some("Unsaved changes".into()),
            ..Workspace::default()
        };
        let error = copy
            .write_with(&changed, None, |_, document| {
                serde_json::to_writer(&mut FullDisk, document).map_err(io::Error::other)
            })
            .unwrap_err();
        assert_eq!(error.code, ErrorCode::SaveFailed);
        assert_eq!(
            copy.is_written(),
            previously_written,
            "only a written copy may be published in Recents"
        );
        drop(copy);
        if previously_written {
            let recovered = folder.claim(&path).unwrap();
            assert_eq!(recovered.read().unwrap().0, Workspace::default());
            recovered.delete().unwrap();
        } else {
            assert!(!path.exists());
            assert!(!lock_path(&path).exists());
            let retry = folder.reserve("Untitled").unwrap();
            retry.delete().unwrap();
            let reused = folder.reserve("Untitled").unwrap();
            drop(retry);
            assert!(
                reused.path().is_file(),
                "dropping a deleted reservation cannot remove a reused name"
            );
            let retry = reused;
            assert_eq!(
                retry.path(),
                path,
                "the failed reservation released its name and lock"
            );
        }
    }
}
