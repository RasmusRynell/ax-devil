//! `catalog check` and `catalog render` as a user runs them: the real command, the real Electron render page on SwiftShader, and the PNG it
//! writes. The Overview sheet's geometry is exact at known pixels; how it looks is not asserted. Skipped without Electron (see
//! `common::export_kit::can_export`).

mod common;

use common::export_kit::{can_export, read_png};
use std::{fs, path::Path, process::Command};

const WIDTH: usize = 1280;
const HEIGHT: usize = 720;

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
fn catalog_render_writes_the_overview_with_its_boxes_where_the_sheet_puts_them() {
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

    // The first object, a human, is a box from (0.04, 0.06) of the picture, 0.12 wide and 0.2 high, drawn with an
    // outline: each edge is within a pixel of its place and differs from the picture.
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
        assert!(
            away_from_grey(&rows, x, y) > 0,
            "{name} edge is drawn over the picture"
        );
    }
    assert_eq!(
        away_from_grey(&rows, centre, middle),
        0,
        "the inside of the box is the picture"
    );
}

#[test]
fn catalog_check_reports_a_catalog_or_every_problem_in_it_located_and_render_draws_nothing_for_one_that_does_not_compile()
 {
    if !can_export() {
        return;
    }
    let catalog = include_str!("../assets/catalogs/minimal.v4.json");
    let folder = tempfile::tempdir().unwrap();
    let write = |name: &str, text: &str| {
        let path = folder.path().join(name);
        fs::write(&path, text).unwrap();
        path
    };
    let good = write("good.json", catalog);
    let bad = write(
        "bad.json",
        &catalog.replace("\"stroke\": \"color\"", "\"stroke\": \"colr\""),
    );
    let run = |args: &[&str], file: &Path| {
        Command::new(env!("CARGO_BIN_EXE_ax-devil"))
            .args(["catalog"])
            .args(args)
            .arg(file)
            .output()
            .unwrap()
    };
    let text = |bytes: &[u8]| String::from_utf8_lossy(bytes).into_owned();

    let checked = run(&["check"], &good);
    assert!(checked.status.success(), "{}", text(&checked.stderr));
    let report = text(&checked.stdout);
    assert!(
        report.contains("Minimal") && report.contains("overview: "),
        "the catalog and the sheet it drew: {report}"
    );

    let checked = run(&["check"], &bad);
    assert!(!checked.status.success());
    let problems = text(&checked.stderr);
    assert!(
        problems.contains("/layers/0/marks/0/stroke: unknown name 'colr'"),
        "{problems}"
    );
    assert!(problems.contains("  17:"), "line and column: {problems}");

    // Rendering lists the same problems and draws nothing.
    let out = folder.path().join("sheets");
    let rendered = Command::new(env!("CARGO_BIN_EXE_ax-devil"))
        .args(["catalog", "render"])
        .arg(&bad)
        .arg("--out")
        .arg(&out)
        .output()
        .unwrap();
    assert!(!rendered.status.success());
    assert_eq!(text(&rendered.stderr), problems);
    assert!(!out.exists());

    // A catalog that compiles but skips a mark on the example sheet fails `check`; `render` draws it and lists the
    // problem.
    let skipping = write(
        "skipping.json",
        r#"{"schema":4,"name":"T","description":"d","layers":[{"marks":[{"mark":"text","text":"id",
        "color":"top(attr.tint)","size":"10px","place":[{"at":"top-left","anchor":"bottom-left"}]}]}]}"#,
    );
    let checked = run(&["check"], &skipping);
    assert!(!checked.status.success());
    assert!(
        text(&checked.stderr).contains("skipped"),
        "{}",
        text(&checked.stderr)
    );
    let sheets = folder.path().join("skipping-sheets");
    let rendered = Command::new(env!("CARGO_BIN_EXE_ax-devil"))
        .args(["catalog", "render"])
        .arg(&skipping)
        .arg("--out")
        .arg(&sheets)
        .output()
        .unwrap();
    assert!(rendered.status.success(), "{}", text(&rendered.stderr));
    assert!(sheets.join("overview.png").is_file());
    let printed = text(&rendered.stdout);
    assert!(
        printed.contains("  drawing error: ") && printed.contains("skipped"),
        "{printed}"
    );

    let missing = run(&["check"], &folder.path().join("missing.json"));
    assert!(!missing.status.success());
    assert!(text(&missing.stderr).contains("missing.json"));
}

#[test]
fn catalog_use_makes_a_catalog_of_the_folder_the_default_and_new_copies_a_catalog_file_from_anywhere()
 {
    if !can_export() {
        return;
    }
    let home = tempfile::tempdir().unwrap();
    let folder = home.path().join("ax-devil/catalogs");
    fs::create_dir_all(&folder).unwrap();
    let night = folder.join("Night.json");
    fs::write(&night, include_str!("../assets/catalogs/minimal.v4.json")).unwrap();
    let settings = home.path().join("settings.json");
    fs::write(&settings, r#"{"version": 1}"#).unwrap();
    let run = |args: &[&str]| {
        Command::new(env!("CARGO_BIN_EXE_ax-devil"))
            .arg("--config")
            .arg(&settings)
            .args(["catalog"])
            .args(args)
            .env("XDG_DATA_HOME", home.path())
            .output()
            .unwrap()
    };
    let text = |bytes: &[u8]| String::from_utf8_lossy(bytes).into_owned();

    let standard = run(&["check"]);
    assert!(standard.status.success(), "{}", text(&standard.stderr));
    assert!(text(&standard.stdout).contains("built-in:standard: Standard"));

    let used = run(&["use", night.to_str().unwrap()]);
    assert!(used.status.success(), "{}", text(&used.stderr));
    assert_eq!(
        text(&used.stdout),
        format!("Default catalog: {}\n", night.display())
    );
    assert!(
        fs::read_to_string(&settings)
            .unwrap()
            .contains("user:Night.json")
    );
    let checked = run(&["check"]);
    assert!(
        text(&checked.stdout).contains("Minimal"),
        "{}",
        text(&checked.stderr)
    );

    // `new` takes any catalog file, not only the folder's: here the one beside the settings.
    let elsewhere = home.path().join("elsewhere.json");
    fs::write(
        &elsewhere,
        include_str!("../assets/catalogs/minimal.v4.json"),
    )
    .unwrap();
    let copied = run(&["new", "Copy", "--from", elsewhere.to_str().unwrap()]);
    assert!(copied.status.success(), "{}", text(&copied.stderr));
    let copy = folder.join("copy.json");
    assert_eq!(text(&copied.stdout), format!("{}\n", copy.display()));
    assert!(
        fs::read_to_string(&copy)
            .unwrap()
            .contains("\"description\": \"Based on Minimal.\"")
    );
}
