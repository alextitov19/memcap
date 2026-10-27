# Sourced off-camera by docs/demo.tape. `memcap status` drops only the header and
# the rows that print local file paths; everything else is real output.
memcap() {
  if [ "$1" = status ]; then
    command memcap status | sed 1d | grep -v -e 'snapshots' -e 'background service'
  else
    command memcap "$@"
  fi
}
