//! The Overlay envelope (kind 2): the Drawing Instructions of one lane for one video frame and view revision. The
//! layout is documented with the constants in `api_types::playback`, and `frontend/src/overlay/instructions.ts`
//! reads it.
//!
//! Every table is written as little-endian 4-byte words, one record after the other, so the receiver views a
//! section as typed arrays without copying. A section's `byte_len` is the length of its records, without the padding
//! that starts the next section on a multiple of 8.

use super::envelope::head;
use api_types::playback::{
    KIND_OVERLAY, OVERLAY_HEADER_BYTES, OVERLAY_MATCH_EMPTY, OVERLAY_MATCH_EXACT,
    OVERLAY_MATCH_NONE, OVERLAY_MATCH_RETAINED, OVERLAY_MATCH_TOLERATED, OVERLAY_TAG_BOXES,
    OVERLAY_TAG_CIRCLES, OVERLAY_TAG_LABEL_STYLES, OVERLAY_TAG_LABELS, OVERLAY_TAG_LINES,
    OVERLAY_TAG_PAINTS, OVERLAY_TAG_PATH_POINTS, OVERLAY_TAG_POINTS, OVERLAY_TAG_POLYGONS,
    OVERLAY_TAG_POLYLINES, OVERLAY_TAG_RUNS, OVERLAY_TAG_STRINGS, OVERLAY_TAG_TEXTS,
};
use bytes::{BufMut, Bytes, BytesMut};
use drawing::{DrawingInstructions, PathRow};
use overlay_sync::{MatchKind, Selection};
use overlays::LaneOverlay;

/// The byte of the overlay header that says how the drawn sample belongs to the frame.
fn match_byte(selection: &Selection) -> u8 {
    match selection {
        Selection::None => OVERLAY_MATCH_NONE,
        Selection::Empty => OVERLAY_MATCH_EMPTY,
        Selection::Sample { kind, .. } => match kind {
            MatchKind::Exact => OVERLAY_MATCH_EXACT,
            MatchKind::Tolerated => OVERLAY_MATCH_TOLERATED,
            MatchKind::Retained => OVERLAY_MATCH_RETAINED,
        },
    }
}

/// The Overlay envelope of `overlay`.
pub(super) fn overlay(overlay: &LaneOverlay) -> Bytes {
    let drawing = &overlay.drawing;
    let opacity = match overlay.selection {
        Selection::Sample { opacity, .. } => opacity,
        Selection::None | Selection::Empty => 1.0,
    };
    let mut out = BytesMut::with_capacity(size_hint(drawing));
    head(
        &mut out,
        KIND_OVERLAY,
        OVERLAY_HEADER_BYTES,
        overlay.frame,
        0,
    );
    out.put_u16_le(overlay.lane);
    out.put_u8(match_byte(&overlay.selection));
    out.put_u8(0);
    out.put_u32_le(overlay.revision);
    out.put_f32_le(opacity);
    out.put_u32_le(overlay.target.width);
    out.put_u32_le(overlay.target.height);
    out.put_i64_le(overlay.key);
    // The section count is known once the sections are written.
    let count_at = out.len();
    out.put_u32_le(0);
    out.put_u32_le(0);
    debug_assert_eq!(out.len(), usize::from(OVERLAY_HEADER_BYTES));

    let mut sections = 0_u32;
    let mut section = |tag: u16, empty: bool, write: &dyn Fn(&mut BytesMut)| {
        if empty {
            return;
        }
        let at = out.len();
        out.put_u16_le(tag);
        out.put_u16_le(0);
        out.put_u32_le(0);
        write(&mut out);
        let length = out.len() - at - 8;
        // A section is far below 4 GiB: its rows came from one lane frame.
        out[at + 4..at + 8]
            .copy_from_slice(&u32::try_from(length).unwrap_or(u32::MAX).to_le_bytes());
        out.resize(out.len().next_multiple_of(8), 0);
        sections += 1;
    };
    section(OVERLAY_TAG_PAINTS, drawing.paints.is_empty(), &|out| {
        for paint in &drawing.paints {
            out.extend_from_slice(&paint.pen);
            out.put_u8(u8::from(paint.dashed));
            out.put_f32_le(paint.pen_width);
            out.extend_from_slice(&paint.brush);
            out.put_u32_le(paint.font_family.0);
            out.put_f32_le(paint.font_size);
            out.put_f32_le(paint.radius);
        }
    });
    section(OVERLAY_TAG_BOXES, drawing.boxes.is_empty(), &|out| {
        for row in &drawing.boxes {
            floats(out, &[row.x, row.y, row.w, row.h]);
            out.put_u32_le(row.paint);
        }
    });
    section(OVERLAY_TAG_LINES, drawing.lines.is_empty(), &|out| {
        for row in &drawing.lines {
            floats(out, &[row.x1, row.y1, row.x2, row.y2]);
            out.put_u32_le(row.paint);
        }
    });
    section(OVERLAY_TAG_CIRCLES, drawing.circles.is_empty(), &|out| {
        for row in &drawing.circles {
            floats(out, &[row.x, row.y, row.radius]);
            out.put_u32_le(row.paint);
        }
    });
    section(OVERLAY_TAG_POINTS, drawing.points.is_empty(), &|out| {
        for row in &drawing.points {
            floats(out, &[row.x, row.y]);
            out.put_u32_le(row.paint);
        }
    });
    section(
        OVERLAY_TAG_PATH_POINTS,
        drawing.path_points.is_empty(),
        &|out| {
            for point in &drawing.path_points {
                floats(out, point);
            }
        },
    );
    section(OVERLAY_TAG_POLYGONS, drawing.polygons.is_empty(), &|out| {
        paths(out, &drawing.polygons);
    });
    section(
        OVERLAY_TAG_POLYLINES,
        drawing.polylines.is_empty(),
        &|out| paths(out, &drawing.polylines),
    );
    section(OVERLAY_TAG_TEXTS, drawing.texts.is_empty(), &|out| {
        for row in &drawing.texts {
            floats(out, &[row.x, row.y]);
            out.put_u32_le(row.text.0);
            anchor(out, row.anchor);
            out.put_u32_le(row.paint);
        }
    });
    section(
        OVERLAY_TAG_LABEL_STYLES,
        drawing.label_styles.is_empty(),
        &|out| {
            for style in &drawing.label_styles {
                out.put_f32_le(style.size);
                out.put_u32_le(style.family.0);
                out.extend_from_slice(&style.background);
                floats(
                    out,
                    &[style.padding_x, style.padding_y, style.radius, style.gap],
                );
            }
        },
    );
    section(OVERLAY_TAG_LABELS, drawing.labels.is_empty(), &|out| {
        for row in &drawing.labels {
            floats(out, &[row.x, row.y]);
            anchor(out, row.anchor);
            out.put_u32_le(row.style);
            out.put_u32_le(row.first_run);
            out.put_u32_le(row.runs);
        }
    });
    section(OVERLAY_TAG_RUNS, drawing.runs.is_empty(), &|out| {
        for run in &drawing.runs {
            out.put_u32_le(run.text.0);
            out.put_f32_le(run.bar);
            out.extend_from_slice(&run.rgb);
            out.put_u8(run.weight as u8);
        }
    });
    let offsets = drawing.strings.offsets();
    section(OVERLAY_TAG_STRINGS, offsets.len() <= 1, &|out| {
        // `offsets` holds one more entry than there are strings.
        out.put_u32_le(u32::try_from(offsets.len() - 1).unwrap_or(u32::MAX));
        for offset in offsets {
            out.put_u32_le(*offset);
        }
        out.extend_from_slice(drawing.strings.blob().as_bytes());
    });
    out[count_at..count_at + 4].copy_from_slice(&sections.to_le_bytes());
    out.freeze()
}

/// A rough size of the message, so the buffer is rarely grown: 20 bytes covers a row of any table.
fn size_hint(drawing: &DrawingInstructions) -> usize {
    let rows = drawing.boxes.len()
        + drawing.lines.len()
        + drawing.circles.len()
        + drawing.points.len()
        + drawing.path_points.len()
        + drawing.polygons.len()
        + drawing.polylines.len()
        + drawing.texts.len()
        + drawing.labels.len()
        + drawing.runs.len();
    usize::from(OVERLAY_HEADER_BYTES) + rows * 20 + drawing.strings.blob().len() + 256
}

fn floats(out: &mut BytesMut, values: &[f32]) {
    for value in values {
        out.put_f32_le(*value);
    }
}

/// The anchor byte and its three bytes of padding.
fn anchor(out: &mut BytesMut, anchor: drawing::Anchor) {
    out.put_u8(anchor as u8);
    out.extend_from_slice(&[0; 3]);
}

fn paths(out: &mut BytesMut, rows: &[PathRow]) {
    for row in rows {
        out.put_u32_le(row.first);
        out.put_u32_le(row.count);
        out.put_u32_le(row.paint);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use drawing::{
        Anchor, BoxRow, CircleRow, LabelRow, LabelStyle, LineRow, Paint, PointRow, RunRow, StrId,
        TextRow, Weight,
    };
    use foundation::{FrameId, MediaTime};
    use overlays::TargetSize;
    use std::{collections::BTreeMap, sync::Arc};

    fn f(value: f32) -> Vec<u8> {
        value.to_le_bytes().to_vec()
    }

    fn u(value: u32) -> Vec<u8> {
        value.to_le_bytes().to_vec()
    }

    fn cat(parts: &[Vec<u8>]) -> Vec<u8> {
        parts.concat()
    }

    /// One of everything, with values that make a swapped field or a wrong offset visible.
    fn full_drawing() -> DrawingInstructions {
        let mut drawing = DrawingInstructions::default();
        let mono = drawing.strings.intern("mono");
        let car = drawing.strings.intern("car");
        let paint = drawing.paint(Paint {
            pen: [1, 2, 3],
            dashed: true,
            pen_width: 0.5,
            brush: [4, 5, 6, 7],
            font_family: mono,
            font_size: 0.25,
            radius: 0.125,
        });
        drawing.boxes.push(BoxRow {
            x: 0.1,
            y: 0.2,
            w: 0.3,
            h: 0.4,
            paint,
        });
        drawing.lines.push(LineRow {
            x1: 0.1,
            y1: 0.2,
            x2: 0.3,
            y2: 0.4,
            paint,
        });
        drawing.circles.push(CircleRow {
            x: 0.5,
            y: 0.6,
            radius: 0.07,
            paint,
        });
        drawing.points.push(PointRow {
            x: 0.8,
            y: 0.9,
            paint,
        });
        drawing.path_points = vec![[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]];
        drawing.polygons.push(PathRow {
            first: 0,
            count: 3,
            paint,
        });
        drawing.polylines.push(PathRow {
            first: 1,
            count: 2,
            paint,
        });
        drawing.texts.push(TextRow {
            x: 0.2,
            y: 0.3,
            text: car,
            anchor: Anchor::BottomRight,
            paint,
        });
        drawing.label_styles.push(LabelStyle {
            size: 0.02,
            family: StrId::NONE,
            background: [9, 10, 11, 12],
            padding_x: 0.01,
            padding_y: 0.02,
            radius: 0.03,
            gap: 0.04,
        });
        drawing.labels.push(LabelRow {
            x: 0.4,
            y: 0.5,
            anchor: Anchor::Top,
            style: 0,
            first_run: 0,
            runs: 2,
        });
        drawing.runs = vec![
            RunRow {
                text: car,
                bar: f32::NAN,
                rgb: [13, 14, 15],
                weight: Weight::Semibold,
            },
            RunRow {
                text: StrId::NONE,
                bar: 0.5,
                rgb: [16, 17, 18],
                weight: Weight::Bold,
            },
        ];
        drawing
    }

    /// The envelope split by the documented layout: the head fields and every section's records by tag. Checks that
    /// sections start on multiples of 8, are padded with zeros and end exactly where the message does.
    fn decode(bytes: &[u8]) -> (Vec<u8>, BTreeMap<u16, Vec<u8>>) {
        let word = |at: usize| u32::from_le_bytes(bytes[at..at + 4].try_into().unwrap());
        assert_eq!(&bytes[..4], b"AXDV");
        assert_eq!((bytes[4], bytes[5]), (1, 2), "version, kind Overlay");
        assert_eq!(u16::from_le_bytes([bytes[6], bytes[7]]), 64);
        let count = word(56);
        assert_eq!(word(60), 0, "pad");
        let mut sections = BTreeMap::new();
        let mut at = 64;
        for _ in 0..count {
            let tag = u16::from_le_bytes([bytes[at], bytes[at + 1]]);
            assert_eq!(&bytes[at + 2..at + 4], &[0, 0], "pad");
            let length = word(at + 4) as usize;
            let records = bytes[at + 8..at + 8 + length].to_vec();
            let next = (at + 8 + length).next_multiple_of(8);
            assert!(bytes[at + 8 + length..next].iter().all(|byte| *byte == 0));
            at = next;
            assert!(sections.insert(tag, records).is_none(), "tag {tag} twice");
        }
        assert_eq!(at, bytes.len(), "the sections end where the message ends");
        (bytes[..64].to_vec(), sections)
    }

    fn lane_overlay(selection: Selection, drawing: DrawingInstructions) -> LaneOverlay {
        let key = if matches!(selection, Selection::Sample { .. }) {
            -9
        } else {
            0
        };
        LaneOverlay {
            lane: 3,
            frame: FrameId {
                sequence_id: 12,
                timestamp: MediaTime::from_us(400_000),
            },
            revision: 7,
            target: TargetSize {
                width: 640,
                height: 360,
            },
            selection,
            key,
            drawing: Arc::new(drawing),
        }
    }

    #[test]
    fn every_section_is_the_documented_records_at_the_documented_offsets() {
        let selection = Selection::Sample {
            index: 4,
            kind: MatchKind::Retained,
            opacity: 0.5,
        };
        let message = overlay(&lane_overlay(selection, full_drawing()));

        let (head, sections) = decode(&message);

        assert_eq!(&head[8..16], &12_u64.to_le_bytes(), "sequence id");
        assert_eq!(&head[16..24], &400_000_i64.to_le_bytes(), "timestamp");
        assert_eq!(&head[24..28], &[0; 4], "flags");
        assert_eq!(&head[28..30], &3_u16.to_le_bytes(), "lane");
        assert_eq!(head[30], 3, "retained");
        assert_eq!(head[31], 0, "pad");
        assert_eq!(&head[32..36], &7_u32.to_le_bytes(), "revision");
        assert_eq!(&head[36..40], &0.5_f32.to_le_bytes(), "opacity");
        assert_eq!(&head[40..48], &cat(&[u(640), u(360)]), "target");
        assert_eq!(&head[48..56], &(-9_i64).to_le_bytes(), "sample key");
        assert_eq!(&head[56..60], &13_u32.to_le_bytes(), "section count");

        let expected: Vec<(u16, Vec<u8>)> = vec![
            (
                OVERLAY_TAG_PAINTS,
                cat(&[
                    vec![1, 2, 3, 1],
                    f(0.5),
                    vec![4, 5, 6, 7],
                    u(0),
                    f(0.25),
                    f(0.125),
                ]),
            ),
            (
                OVERLAY_TAG_BOXES,
                cat(&[f(0.1), f(0.2), f(0.3), f(0.4), u(0)]),
            ),
            (
                OVERLAY_TAG_LINES,
                cat(&[f(0.1), f(0.2), f(0.3), f(0.4), u(0)]),
            ),
            (OVERLAY_TAG_CIRCLES, cat(&[f(0.5), f(0.6), f(0.07), u(0)])),
            (OVERLAY_TAG_POINTS, cat(&[f(0.8), f(0.9), u(0)])),
            (
                OVERLAY_TAG_PATH_POINTS,
                cat(&[f(0.1), f(0.2), f(0.3), f(0.4), f(0.5), f(0.6)]),
            ),
            (OVERLAY_TAG_POLYGONS, cat(&[u(0), u(3), u(0)])),
            (OVERLAY_TAG_POLYLINES, cat(&[u(1), u(2), u(0)])),
            (
                OVERLAY_TAG_TEXTS,
                cat(&[f(0.2), f(0.3), u(1), vec![8, 0, 0, 0], u(0)]),
            ),
            (
                OVERLAY_TAG_LABEL_STYLES,
                cat(&[
                    f(0.02),
                    u(u32::MAX),
                    vec![9, 10, 11, 12],
                    f(0.01),
                    f(0.02),
                    f(0.03),
                    f(0.04),
                ]),
            ),
            (
                OVERLAY_TAG_LABELS,
                cat(&[f(0.4), f(0.5), vec![9, 0, 0, 0], u(0), u(0), u(2)]),
            ),
            (
                OVERLAY_TAG_RUNS,
                cat(&[
                    u(1),
                    f(f32::NAN),
                    vec![13, 14, 15, 2],
                    u(u32::MAX),
                    f(0.5),
                    vec![16, 17, 18, 3],
                ]),
            ),
            (
                OVERLAY_TAG_STRINGS,
                cat(&[u(2), u(0), u(4), u(7), b"monocar".to_vec()]),
            ),
        ];
        assert_eq!(sections.keys().copied().collect::<Vec<_>>(), {
            let mut tags: Vec<u16> = expected.iter().map(|(tag, _)| *tag).collect();
            tags.sort_unstable();
            tags
        });
        for (tag, records) in expected {
            assert_eq!(sections[&tag], records, "section {tag}");
        }
    }

    #[test]
    fn nothing_drawn_is_the_head_alone_and_the_match_byte_follows_the_selection() {
        let none = overlay(&lane_overlay(
            Selection::None,
            DrawingInstructions::default(),
        ));
        let (head, sections) = decode(&none);
        assert_eq!(none.len(), 64);
        assert!(sections.is_empty());
        assert_eq!((head[30], &head[36..40]), (0, &1.0_f32.to_le_bytes()[..]));

        let matched = |kind| Selection::Sample {
            index: 0,
            kind,
            opacity: 1.0,
        };
        let byte = |selection| {
            let message = overlay(&lane_overlay(selection, DrawingInstructions::default()));
            message[30]
        };
        assert_eq!(byte(Selection::Empty), 4);
        assert_eq!(byte(matched(MatchKind::Exact)), 1);
        assert_eq!(byte(matched(MatchKind::Tolerated)), 2);
        assert_eq!(byte(matched(MatchKind::Retained)), 3);
    }
}
