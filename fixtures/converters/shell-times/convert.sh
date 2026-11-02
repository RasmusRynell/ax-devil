#!/bin/sh
# Converts lines of "op time_us id class left top right bottom" (box in 0-1) into ax-devil Scenes keyed by time.
# op "s" adds a sample, "r" replaces whatever its time already held. Lines are not in time order, which ax-devil
# sorts out. POSIX sh and awk only; the request line on stdin is not needed.
read -r request
awk '
/^#/ || NF == 0 { next }
NF != 8 { printf "{\"skipped\":\"line %d: expected 8 fields\"}\n", NR; next }
{
  kind = ($1 == "r") ? "replace" : "sample"
  printf "{\"%s\":{\"time\":{\"start\":{\"us\":%s},\"end\":{\"us\":%s}},", kind, $2, $2
  printf "\"entities\":[{\"id\":\"%s\",\"end_reason\":null,\"motion_state\":null,", $3
  printf "\"observations\":[{\"time\":{\"us\":%s},\"geometry\":{\"box\":[%s,%s,%s,%s]},", $2, $5, $6, $7, $8
  printf "\"classifications\":[{\"type\":\"%s\",\"score\":null,\"confidence\":null}]}]}]}}\n", $4
}' "$1"
