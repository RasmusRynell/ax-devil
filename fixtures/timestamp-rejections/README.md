# Deliberately rejected timestamps

Recorded through the unchanged Python plugins' `parse_timestamp` and `datetime_to_us`
functions on CPython 3.12.15. Each JSON-escaped input has its measured UTC microseconds
in a comment. All are rejected by Rust at a6bd41dada6f2889e35c527d3c312296cbff3b15.

The finite corpus is the full known rejection list from the round-2 and round-3 searches,
not an exhaustive grammar. Round 3 compared 32,040 inputs: the previous table, C0/C1
controls and NUL at every position after a complete hour prefix in basic/extended clocks,
all Unicode whitespace before Z and offsets, extra colon fields after seconds, and
combinations with the original spellings. It found 905 additional rejections and no
wrong instants or supported ISO forms rejected by Rust.

Generated for this repository (MIT). Rejection policy and attribution are in
[the BIP-002 deviation](../../docs/parity/app-deviations.md).

- [owner-1.txt](owner-1.txt): original 46, owner decision.
- [ai-1.txt](ai-1.txt): additional cases, [AI] step 2.
- [ai-2.txt](ai-2.txt): additional cases, [AI] step 2.
- [ai-3.txt](ai-3.txt): additional cases, [AI] step 2.
- [ai-4.txt](ai-4.txt): additional cases, [AI] step 2.
