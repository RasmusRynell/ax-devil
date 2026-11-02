//! `catalog render` as a user runs it: the real command, the real Electron render page on SwiftShader, and the PNG it
//! writes. The Overview sheet's geometry is exact at known pixels; the rest of the picture is compared with a golden
//! that tolerates the antialiasing and glyph differences of other machines' fonts. Skipped without Electron (see
//! `common::export_kit::can_export`).

mod common;

use common::export_kit::{can_export, read_png};
use std::{fs, path::Path, process::Command};

const WIDTH: usize = 1280;
const HEIGHT: usize = 720;
/// The most pixels that may differ by more than `DELTA` in any channel from the golden.
const DIFFERING: f64 = 0.02;
const DELTA: i32 = 24;
const GOLDEN: &str = concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/fixtures/render/standard-overview.png"
);

fn pixel(rows: &[u8], x: usize, y: usize) -> [i32; 3] {
    let at = (y * WIDTH + x) * 4;
    [rows[at], rows[at + 1], rows[at + 2]].map(i32::from)
}

/// How far a pixel is from the sheet's mid grey, which is what the page makes of the sheet's grey picture.
fn away_from_grey(rows: &[u8], x: usize, y: usize) -> i32 {
    let grey = pixel(rows, 2, 2);
    let at = pixel(rows, x, y);
    (0..3).map(|c| (at[c] - grey[c]).abs()).sum()
}

#[test]
fn catalog_render_writes_the_overview_with_its_boxes_where_the_sheet_puts_them_and_as_the_golden_looks()
 {
    if !can_export() {
        return;
    }
    let out = tempfile::tempdir().unwrap();
    // The command's own scratch space: the render page's user data folder must be gone when it exits.
    let scratch = tempfile::tempdir().unwrap();
    let catalog = Path::new(env!("CARGO_MANIFEST_DIR")).join("assets/catalogs/standard.v4.json");
    let run = Command::new(env!("CARGO_BIN_EXE_ax-devil"))
        .args(["catalog", "render"])
        .arg(&catalog)
        .arg("--out")
        .arg(out.path())
        .env("TMPDIR", scratch.path())
        .output()
        .unwrap();
    assert!(
        run.status.success(),
        "{}",
        String::from_utf8_lossy(&run.stderr)
    );
    assert_eq!(
        fs::read_dir(scratch.path()).unwrap().count(),
        0,
        "the render page left its folder behind"
    );
    let png = out.path().join("overview.png");
    assert!(png.is_file(), "{}", String::from_utf8_lossy(&run.stdout));
    let (width, height, rows) = read_png(&png);
    assert_eq!((width as usize, height as usize), (WIDTH, HEIGHT));

    // The first object, a human, is a box from (0.04, 0.06) of the picture, 0.12 wide and 0.2 high, drawn in the
    // Standard catalog's amber outline: each edge is within a pixel of its place and amber.
    let (left, right) = (0.04 * WIDTH as f64, 0.16 * WIDTH as f64);
    let (top, bottom) = (0.06 * HEIGHT as f64, 0.26 * HEIGHT as f64);
    let strongest = |positions: Vec<(usize, usize)>| {
        positions
            .into_iter()
            .max_by_key(|(x, y)| away_from_grey(&rows, *x, *y))
            .unwrap()
    };
    // A row above the relation line and the motion arrow, which run through the middle of the box.
    let middle = (top + 0.25 * (bottom - top)) as usize;
    let centre = (left + right) as usize / 2;
    let edges = [
        (
            "left",
            strongest(
                (left as usize - 2..=left as usize + 2)
                    .map(|x| (x, middle))
                    .collect(),
            ),
            left,
            0,
        ),
        (
            "right",
            strongest(
                (right as usize - 2..=right as usize + 2)
                    .map(|x| (x, middle))
                    .collect(),
            ),
            right,
            0,
        ),
        (
            "top",
            strongest(
                (top as usize - 2..=top as usize + 2)
                    .map(|y| (centre, y))
                    .collect(),
            ),
            top,
            1,
        ),
        (
            "bottom",
            strongest(
                (bottom as usize - 2..=bottom as usize + 2)
                    .map(|y| (centre, y))
                    .collect(),
            ),
            bottom,
            1,
        ),
    ];
    for (name, (x, y), expected, axis) in edges {
        let at = [x, y][axis] as f64;
        assert!(
            (at - expected).abs() <= 1.0,
            "{name} edge at {at}, expected {expected}"
        );
        let [r, g, b] = pixel(&rows, x, y);
        assert!(
            r > 180 && (100..200).contains(&g) && b < 90,
            "{name} edge is amber: {r},{g},{b}"
        );
    }
    assert_eq!(
        away_from_grey(&rows, centre, middle),
        0,
        "the inside of the box is the picture"
    );

    if std::env::var_os("UPDATE_GOLDENS").is_some() {
        fs::create_dir_all(Path::new(GOLDEN).parent().unwrap()).unwrap();
        fs::copy(&png, GOLDEN).unwrap();
    }
    let (gold_width, gold_height, gold) = read_png(Path::new(GOLDEN));
    assert_eq!((gold_width, gold_height), (width, height));
    let differing = rows
        .chunks(4)
        .zip(gold.chunks(4))
        .filter(|(a, b)| (0..3).any(|c| (i32::from(a[c]) - i32::from(b[c])).abs() > DELTA))
        .count();
    let share = differing as f64 / (WIDTH * HEIGHT) as f64;
    assert!(
        share <= DIFFERING,
        "{:.2}% of the pixels differ from {GOLDEN}; regenerate with UPDATE_GOLDENS=1 make tests-backend TEST=catalog_render",
        share * 100.0
    );
}
