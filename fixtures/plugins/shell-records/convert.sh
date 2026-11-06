#!/bin/sh
# Converts records of "time_us id class left top right bottom" (box in 0-1) into ax-devil Scenes keyed by time, one
# sample per record. The first stdin line is the request; every later line is {"record":{"text":"..."}} (a record
# that is not UTF-8 arrives as {"record":{"base64":"..."}} and is skipped here). The answer to a record is its
# lines and then {"done":{}}. POSIX sh only; the records of this plugin contain no characters JSON escapes.
set -f
read -r request
while read -r line; do
  text=${line#'{"record":{"text":"'}
  text=${text%'"}}'}
  # shellcheck disable=SC2086 # splitting on whitespace is the point
  set -- $text
  if [ "$#" -ne 7 ] || [ "$text" = "$line" ]; then
    printf '{"skipped":"expected 7 fields"}\n'
  else
    printf '{"sample":{"time":{"start":{"us":%s},"end":{"us":%s}},' "$1" "$1"
    printf '"entities":[{"id":"%s","end_reason":null,"motion_state":null,' "$2"
    printf '"observations":[{"time":{"us":%s},"geometry":{"box":[%s,%s,%s,%s]},' "$1" "$4" "$5" "$6" "$7"
    printf '"classifications":[{"type":"%s","score":null,"confidence":null}]}]}]}}\n' "$3"
  fi
  printf '{"done":{}}\n'
done
