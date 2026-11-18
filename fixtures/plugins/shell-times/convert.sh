#!/bin/sh
# Converts lines of "op time_us id class left top right bottom" (box in 0-1) into ax-devil Scenes keyed by time.
# op "s" adds a sample, "r" replaces whatever its time already held. Lines are not in time order, which ax-devil
# sorts out. POSIX sh and awk only. The request is one JSON line on stdin; awk finds the file's path in it and
# decodes the JSON string escapes, so any file name works. A \uXXXX escape is decoded for ASCII only, and any
# other one is an error.
read -r request
REQUEST="$request" awk '
function fail(message) {
  print message | "cat 1>&2"
  exit 1
}
# The JSON string that starts at the beginning of text, with its escapes decoded.
function json_string(text,    out, i, c, hex, code, j, digit) {
  out = ""
  for (i = 1; i <= length(text); i++) {
    c = substr(text, i, 1)
    if (c == "\"") return out
    if (c != "\\") { out = out c; continue }
    i++
    c = substr(text, i, 1)
    if (c == "\"" || c == "\\" || c == "/") out = out c
    else if (c == "n") out = out "\n"
    else if (c == "t") out = out "\t"
    else if (c == "r") out = out "\r"
    else if (c == "b") out = out sprintf("%c", 8)
    else if (c == "f") out = out sprintf("%c", 12)
    else if (c == "u") {
      hex = tolower(substr(text, i + 1, 4))
      if (hex !~ /^[0-9a-f][0-9a-f][0-9a-f][0-9a-f]$/) fail("the request has a malformed \\u escape")
      code = 0
      for (j = 1; j <= 4; j++) {
        digit = index("0123456789abcdef", substr(hex, j, 1)) - 1
        code = code * 16 + digit
      }
      if (code == 0 || code > 127) fail("the request has a \\u escape outside 0001 to 007F, which this plugin cannot decode")
      out = out sprintf("%c", code)
      i += 4
    }
    else fail("the request has an unknown escape")
  }
  fail("the request ends inside the input path")
}
BEGIN {
  anchor = "\"input\":{\"path\":\""
  at = index(ENVIRON["REQUEST"], anchor)
  if (at == 0) fail("the request names no input path")
  path = json_string(substr(ENVIRON["REQUEST"], at + length(anchor)))
  line = 0
  while ((status = (getline text < path)) > 0) {
    line++
    n = split(text, f)
    if (text ~ /^#/ || n == 0) continue
    if (n != 8) { printf "{\"skipped\":\"line %d: expected 8 fields\"}\n", line; continue }
    kind = (f[1] == "r") ? "replace" : "sample"
    printf "{\"%s\":{\"time\":{\"start\":{\"us\":%s},\"end\":{\"us\":%s}},", kind, f[2], f[2]
    printf "\"entities\":[{\"id\":\"%s\",\"end_reason\":null,\"motion_state\":null,", f[3]
    printf "\"observations\":[{\"time\":{\"us\":%s},\"geometry\":{\"box\":[%s,%s,%s,%s]},", f[2], f[5], f[6], f[7], f[8]
    printf "\"classifications\":[{\"type\":\"%s\",\"score\":null,\"confidence\":null}]}]}]}}\n", f[4]
  }
  if (status < 0) fail("cannot read the input file")
}'
