//! The detail line of rows: where a camera is, and what tells sibling rows that share a label apart.

use std::{
    collections::HashMap,
    path::{Path, PathBuf},
};

use workspace::{DisplayHost, VideoRef};

use crate::{Row, RowKind, RowStatus};

/// Where a row's thing is read from.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Location {
    /// A file: a video, its image sequence or an overlay.
    File(PathBuf),
    /// A live stream's camera: the host, and the head when it is not the first.
    Camera {
        /// The host, a plain one: a resolved host that is not never becomes a location.
        host: DisplayHost,
        /// The video channel, from 1.
        head: u16,
    },
}

impl Location {
    /// Where `video` is read from.
    pub fn of_video(video: &VideoRef) -> Self {
        match video {
            VideoRef::File(path) => Self::File(path.clone()),
            VideoRef::ImageSequence(sequence) => Self::File(sequence.pattern.clone()),
        }
    }

    /// The location as text: a file's path, or a camera's host and head.
    pub fn text(&self) -> String {
        match self {
            Self::File(path) => path.to_string_lossy().into_owned(),
            Self::Camera { host, head: 1 } => host.to_string(),
            Self::Camera { host, head } => format!("{host} · head {head}"),
        }
    }
}

/// The detail line of each of `rows`, empty for most. A camera that can be opened has its host (and head, when it is
/// not the first) unless that is its label. Among siblings that can be opened and share a label, a row gets the
/// shortest trailing folders that tell its file apart (or else the file names), a camera its host, and rows with the
/// same location are numbered in order, after the location. Rows still resolving or failed get nothing, and neither
/// do rows in different sections, which do not compete. Without `locations`, which only an audience that may know
/// the host's files and cameras has, rows are numbered alone and a camera has no line.
pub fn details(rows: &[Row], locations: bool) -> Vec<String> {
    // The row each one is inside, as the tree nests them: playlist entries in their playlist, lanes in their entry or
    // else the video.
    let mut parents = Vec::with_capacity(rows.len());
    let (mut top, mut entry) = (None, None);
    for (at, row) in rows.iter().enumerate() {
        parents.push(match row.kind {
            RowKind::Item | RowKind::Playlist => {
                (top, entry) = (Some(at), None);
                None
            }
            RowKind::Entry => {
                entry = Some(at);
                top
            }
            RowKind::Lane => entry.or(top),
        });
    }
    // Siblings by section (a camera is not a video), then by label.
    let mut same: HashMap<(Option<usize>, RowKind, bool, &str), Vec<usize>> = HashMap::new();
    for (at, row) in rows.iter().enumerate() {
        if row.status == RowStatus::Ready {
            let camera = matches!(row.location, Some(Location::Camera { .. }));
            same.entry((parents[at], row.kind, camera, row.label.as_str()))
                .or_default()
                .push(at);
        }
    }
    let mut details = vec![String::new(); rows.len()];
    for group in same.values() {
        if let [at] = group[..] {
            if let Some(place @ Location::Camera { .. }) =
                rows[at].location.as_ref().filter(|_| locations)
                && place.text() != rows[at].label
            {
                details[at] = place.text();
            }
            continue;
        }
        let places: Vec<_> = group
            .iter()
            .map(|&at| rows[at].location.as_ref().filter(|_| locations))
            .collect();
        for (&at, hint) in group.iter().zip(hints(&places)) {
            details[at] = hint;
        }
    }
    details
}

/// A hint for each of `places`; places that repeat are numbered in order of appearance.
fn hints(places: &[Option<&Location>]) -> Vec<String> {
    let texts: Vec<String> = places
        .iter()
        .map(|place| place.map(Location::text).unwrap_or_default())
        .collect();
    let mut files: Vec<&PathBuf> = Vec::new();
    for place in places {
        if let Some(Location::File(path)) = place
            && !files.contains(&path)
        {
            files.push(path);
        }
    }
    let mut hint_of: HashMap<&str, String> = texts
        .iter()
        .map(|text| (text.as_str(), text.clone()))
        .collect();
    let paths: Vec<&Path> = files.iter().map(|path| path.as_path()).collect();
    let file_texts: Vec<String> = paths
        .iter()
        .map(|path| path.to_string_lossy().into_owned())
        .collect();
    for (text, tail) in file_texts.iter().zip(shortest_distinct_tails(&paths)) {
        hint_of.insert(text.as_str(), tail);
    }
    let mut totals: HashMap<&str, usize> = HashMap::new();
    for text in &texts {
        *totals.entry(text.as_str()).or_default() += 1;
    }
    let mut seen: HashMap<&str, usize> = HashMap::new();
    texts
        .iter()
        .map(|text| {
            let hint = &hint_of[text.as_str()];
            if totals[text.as_str()] == 1 {
                return hint.clone();
            }
            let number = seen.entry(text.as_str()).or_default();
            *number += 1;
            format!("{hint} ({number})").trim().to_owned()
        })
        .collect()
}

/// The shortest trailing folders, or else trailing path, of each of `files` that tells them apart; the whole paths if
/// nothing does.
fn shortest_distinct_tails(files: &[&Path]) -> Vec<String> {
    if let [file] = files {
        let folder = file.parent().and_then(Path::file_name);
        return vec![folder.map_or_else(String::new, |name| name.to_string_lossy().into_owned())];
    }
    let folders: Vec<Vec<_>> = files
        .iter()
        .map(|file| {
            file.parent()
                .unwrap_or(Path::new(""))
                .components()
                .collect()
        })
        .collect();
    let sources: Vec<Vec<_>> = files
        .iter()
        .map(|file| file.components().collect())
        .collect();
    for lists in [&folders, &sources] {
        for depth in 1..=lists.iter().map(Vec::len).max().unwrap_or(0) {
            let tails: Vec<String> = lists
                .iter()
                .map(|list| {
                    let from = list.len().saturating_sub(depth);
                    let tail: PathBuf = list[from..].iter().collect();
                    tail.to_string_lossy().into_owned()
                })
                .collect();
            if tails.iter().collect::<std::collections::HashSet<_>>().len() == tails.len() {
                return tails;
            }
        }
    }
    files
        .iter()
        .map(|file| file.to_string_lossy().into_owned())
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use workspace::ItemId;

    fn row(kind: RowKind, label: &str, location: Option<Location>) -> Row {
        Row {
            key: label.to_owned(),
            item: ItemId::generate(),
            kind,
            label: label.to_owned(),
            status: RowStatus::Ready,
            hidden: false,
            entry: None,
            warnings: Vec::new(),
            location,
        }
    }

    fn file(path: &str) -> Option<Location> {
        Some(Location::File(path.into()))
    }

    fn camera(host: &str, head: u16) -> Option<Location> {
        Some(Location::Camera {
            host: DisplayHost::of(host).expect("a plain host"),
            head,
        })
    }

    fn videos(places: Vec<Option<Location>>) -> Vec<Row> {
        places
            .into_iter()
            .map(|place| row(RowKind::Item, "clip.mp4", place))
            .collect()
    }

    #[test]
    fn same_named_files_get_the_shortest_folders_that_tell_them_apart_else_their_names() {
        let apart = videos(vec![
            file("/a/clip.mp4"),
            file("/b/x/clip.mp4"),
            file("/b/y/clip.mp4"),
        ]);
        assert_eq!(details(&apart, true), ["a", "x", "y"]);
        let deep = videos(vec![file("/s/a/day1/clip.mp4"), file("/s/b/day1/clip.mp4")]);
        assert_eq!(details(&deep, true), ["a/day1", "b/day1"]);
        let together = vec![
            row(RowKind::Item, "Camera", file("/clips/front.mp4")),
            row(RowKind::Item, "Camera", file("/clips/rear.mp4")),
            row(RowKind::Item, "Other", file("/clips/other.mp4")),
        ];
        assert_eq!(details(&together, true), ["front.mp4", "rear.mp4", ""]);
    }

    #[test]
    fn a_repeated_location_is_numbered_after_its_hint_and_a_missing_one_alone() {
        let repeated = videos(vec![
            file("/a/clip.mp4"),
            file("/a/clip.mp4"),
            file("/b/clip.mp4"),
        ]);
        assert_eq!(details(&repeated, true), ["a (1)", "a (2)", "b"]);
        let cameras = videos(vec![camera("cam.local", 2), camera("cam.local", 2)]);
        assert_eq!(
            details(&cameras, true),
            ["cam.local · head 2 (1)", "cam.local · head 2 (2)"]
        );
        let none = vec![
            row(RowKind::Playlist, "Folder Pair", None),
            row(RowKind::Playlist, "Folder Pair", None),
        ];
        assert_eq!(details(&none, true), ["(1)", "(2)"]);
    }

    #[test]
    fn cameras_are_told_apart_by_host_and_head_and_not_from_videos() {
        let mut rows = videos(vec![
            camera("cam", 1),
            camera("cam", 2),
            file("/a/clip.mp4"),
        ]);
        assert_eq!(details(&rows, true), ["cam", "cam · head 2", ""]);
        rows.push(row(RowKind::Item, "clip.mp4", file("/b/clip.mp4")));
        assert_eq!(details(&rows, true), ["cam", "cam · head 2", "a", "b"]);
    }

    #[test]
    fn a_camera_shows_its_host_unless_that_is_its_label_and_only_to_an_audience_that_may_know_it() {
        let rows = vec![
            row(RowKind::Item, "Lobby", camera("10.0.0.5", 1)),
            row(RowKind::Item, "10.0.0.6", camera("10.0.0.6", 1)),
            row(RowKind::Item, "10.0.0.7", camera("10.0.0.7", 2)),
            row(RowKind::Item, "clip.mp4", file("/a/clip.mp4")),
        ];
        assert_eq!(
            details(&rows, true),
            ["10.0.0.5", "", "10.0.0.7 · head 2", ""]
        );
        assert!(details(&rows, false).iter().all(String::is_empty));
    }

    #[test]
    fn rows_that_are_not_ready_and_audiences_without_locations_get_no_location() {
        let mut rows = videos(vec![
            file("/a/clip.mp4"),
            file("/b/clip.mp4"),
            file("/c/clip.mp4"),
        ]);
        rows[2].status = RowStatus::Pending;
        assert_eq!(details(&rows, true), ["a", "b", ""]);
        assert_eq!(details(&rows, false), ["(1)", "(2)", ""]);
    }

    #[test]
    fn siblings_compete_inside_their_own_parent_only() {
        let rows = vec![
            row(RowKind::Playlist, "A", None),
            row(RowKind::Entry, "clip", file("/x/clip.mp4")),
            row(RowKind::Lane, "gt", file("/x/gt.txt")),
            row(RowKind::Playlist, "B", None),
            row(RowKind::Entry, "clip", file("/y/clip.mp4")),
            row(RowKind::Lane, "gt", file("/y/gt.txt")),
        ];
        assert!(details(&rows, true).iter().all(String::is_empty));
        let lanes = vec![
            row(RowKind::Item, "v", file("/v.mp4")),
            row(RowKind::Lane, "tracks", file("/det_a/tracks.txt")),
            row(RowKind::Lane, "tracks", file("/det_b/tracks.txt")),
        ];
        assert_eq!(details(&lanes, true), ["", "det_a", "det_b"]);
    }
}
