load helper
setup() {
  setup_common
  # shellcheck source=/dev/null
  source "$MEMCAP_ROOT/libexec/common.sh"
  # shellcheck source=/dev/null
  source "$MEMCAP_ROOT/libexec/roots.sh"
}

@test "recording a root makes it readable back" {
  mc_record_root "$HOME/code/projectA"
  run mc_sweep_roots
  assert_contains "$output" "$HOME/code/projectA"
}

@test "roots are deduplicated" {
  mc_record_root "$HOME/code/projectA"
  mc_record_root "$HOME/code/projectA"
  run bash -c "source '$MEMCAP_ROOT/libexec/common.sh'; source '$MEMCAP_ROOT/libexec/roots.sh'; mc_sweep_roots | wc -l"
  [ "$(echo "$output" | tr -d ' ')" = "1" ]
}

@test "HOME itself is rejected as too broad" {
  run mc_root_is_safe "$HOME"
  [ "$status" -ne 0 ]
}

@test "root directory is rejected" {
  run mc_root_is_safe "/"
  [ "$status" -ne 0 ]
}

@test "a project two levels below HOME is accepted" {
  run mc_root_is_safe "$HOME/code/projectA"
  [ "$status" -eq 0 ]
}

@test "a path escaping HOME with .. is rejected" {
  run mc_root_is_safe "$HOME/../etc"
  [ "$status" -ne 0 ]
}

@test "a path climbing to the filesystem root with .. is rejected" {
  run mc_root_is_safe "$HOME/x/y/../../../../"
  [ "$status" -ne 0 ]
}

@test "a symlink pointing outside HOME is rejected" {
  mkdir -p "$HOME/.memcap-test-$$"
  ln -sfn /etc "$HOME/.memcap-test-$$/link"
  run mc_root_is_safe "$HOME/.memcap-test-$$/link"
  rm -rf "$HOME/.memcap-test-$$"
  [ "$status" -ne 0 ]
}

@test "an intermediate symlink with a missing leaf is rejected" {
  ln -sfn /tmp "$HOME/.memcap-test-link-$$"
  run mc_root_is_safe "$HOME/.memcap-test-link-$$/nonexistent"
  rm -f "$HOME/.memcap-test-link-$$"
  [ "$status" -ne 0 ]
}

@test "a trailing slash cannot pass one level off as two" {
  run mc_root_is_safe "$HOME/code/"
  [ "$status" -ne 0 ]
}

@test "a relative path is rejected without hanging" {
  run mc_root_is_safe "relative/path"
  [ "$status" -ne 0 ]
}

@test "a path containing a newline is rejected" {
  run mc_root_is_safe "$(printf '%s/a\nb/c' "$HOME")"
  [ "$status" -ne 0 ]
}

@test "a recorded root is stored resolved, not as given" {
  mkdir -p "$HOME/.memcap-real-$$/proj"
  ln -sfn "$HOME/.memcap-real-$$" "$HOME/.memcap-link-$$"
  mc_record_root "$HOME/.memcap-link-$$/proj"
  run mc_sweep_roots
  rm -rf "$HOME/.memcap-real-$$" "$HOME/.memcap-link-$$"
  assert_contains "$output" ".memcap-real-$$/proj"
  assert_not_contains "$output" ".memcap-link-$$"
}

@test "a path containing a carriage return is rejected" {
  run mc_root_is_safe "$HOME/code/proj"$'\r'"sneaky"
  [ "$status" -ne 0 ]
}

@test "a symlink to a directory whose name contains a newline is rejected" {
  real_dir="$HOME/.memcap-nl-$$/a"$'\n'"b"
  mkdir -p "$real_dir"
  ln -sfn "$real_dir" "$HOME/.memcap-nllink-$$"
  run mc_root_is_safe "$HOME/.memcap-nllink-$$/proj"
  rm -rf "$HOME/.memcap-nl-$$" "$HOME/.memcap-nllink-$$"
  [ "$status" -ne 0 ]
}

@test "an unsafe root is never recorded" {
  mc_record_root "$HOME"
  run mc_sweep_roots
  [ -z "$output" ]
}

# --- Audit: the roots file grew forever ---------------------------------------
# mc_record_root only ever appended and nothing anywhere pruned. The author's
# machine reached 40 rows, all still resolving, with every new client project
# permanently adding one. Tier 1 is O(orphans x roots) at a measured 5,121us per
# pair, so 388 orphans x 40 roots is 79s of work against a 60s service interval.

# A path in the form mc_record_root would actually have stored it: resolved.
# Comparing against a raw "$HOME/..." string breaks under a symlinked $HOME --
# /tmp and /var are symlinks on macOS, so `env HOME=$(mktemp -d) bats tests/`
# hits this on every run. See mc_home_real.
rp() { printf '%s/%s' "$(mc_home_real)" "$1"; }

seed_row() {  # seed_row AGE_DAYS PATH -- writes one row directly, bypassing mc_record_root
  local f; f="$(mc_roots_file)"
  mkdir -p "$(dirname "$f")"
  printf '%s\t%s\n' "$(( $(date +%s) - ($1 * 86400) ))" "$2" >> "$f"
}

@test "re-recording a root refreshes it and moves it to the front" {
  mc_record_root "$(rp code/projectA)"
  mc_record_root "$(rp code/projectB)"
  mc_record_root "$(rp code/projectA)"
  run mc_sweep_roots
  # Still deduplicated...
  [ "$(printf '%s\n' "$output" | grep -c projectA)" = "1" ]
  # ...and now first, so tier 1 tries the directory an agent is actually working
  # in before the rest, and so the cap below discards the oldest rows.
  [ "$(printf '%s\n' "$output" | head -1)" = "$(rp code/projectA)" ]
}

@test "a root not seen within the TTL is pruned" {
  seed_row 40 "$(rp code/abandoned)"
  ROOT_TTL_DAYS=14 mc_prune_roots
  run mc_sweep_roots
  [ -z "$output" ]
}

@test "a root seen inside the TTL survives pruning" {
  seed_row 3 "$(rp code/recent)"
  ROOT_TTL_DAYS=14 mc_prune_roots
  run mc_sweep_roots
  assert_contains "$output" "$(rp code/recent)"
}

# The "cannot delete a root that is about to be used" case, which is the whole
# reason mc_record_roots prunes AFTER its refresh loop rather than before it: a
# root that has been stale for 40 days but has a live agent session sitting in
# it right now is re-stamped first, so no TTL can reach it on this pass.
@test "a stale root re-registered by a live session this pass is NOT pruned" {
  seed_row 40 "$(rp code/backFromTheDead)"
  mc_record_root "$(rp code/backFromTheDead)"
  ROOT_TTL_DAYS=1 mc_prune_roots
  run mc_sweep_roots
  assert_contains "$output" "$(rp code/backFromTheDead)"
}

@test "mc_record_roots prunes only after refreshing every live root" {
  seed_row 40 "$(rp code/abandoned)"
  # No pids to refresh, so nothing is re-stamped and the stale row goes.
  ROOT_TTL_DAYS=14 mc_record_roots ""
  run mc_sweep_roots
  assert_not_contains "$output" "abandoned"
}

@test "the cap drops the least-recently-seen roots, not arbitrary ones" {
  seed_row 9 "$(rp code/oldest)"
  mc_record_root "$(rp code/middle)"
  mc_record_root "$(rp code/newest)"
  ROOT_MAX=2 mc_prune_roots
  run mc_sweep_roots
  assert_contains "$output" "$(rp code/newest)"
  assert_contains "$output" "$(rp code/middle)"
  assert_not_contains "$output" "oldest"
}

# Backward compatibility with the plain-path file this machine actually has:
# 40 rows, no timestamps. A legacy row must be a valid root, not junk, and must
# get a FULL TTL from first sight rather than reading as infinitely old and
# being pruned wholesale on the very pass that upgrades the format.
@test "legacy plain-path rows are still read as roots" {
  f="$(mc_roots_file)"; mkdir -p "$(dirname "$f")"
  printf '%s\n%s\n' "$(rp code/legacyA)" "$(rp code/legacyB)" > "$f"
  run mc_sweep_roots
  assert_contains "$output" "$(rp code/legacyA)"
  assert_contains "$output" "$(rp code/legacyB)"
}

@test "legacy plain-path rows survive the first prune and are re-stamped" {
  f="$(mc_roots_file)"; mkdir -p "$(dirname "$f")"
  printf '%s\n%s\n' "$(rp code/legacyA)" "$(rp code/legacyB)" > "$f"
  ROOT_TTL_DAYS=1 mc_prune_roots
  run mc_sweep_roots
  assert_contains "$output" "$(rp code/legacyA)"
  assert_contains "$output" "$(rp code/legacyB)"
  # Every row now carries a numeric last-seen column.
  run bash -c "awk -F'\t' '\$1 ~ /^[0-9]+\$/ && NF == 2' '$f' | wc -l"
  [ "$(echo "$output" | tr -d ' ')" = "2" ]
}

@test "mc_sweep_roots emits the path only, never the timestamp column" {
  mc_record_root "$(rp code/projectA)"
  run mc_sweep_roots
  [ "$output" = "$(rp code/projectA)" ]
}

# Tier 1 already handles a root with a space in it (`~/dev/foo bar`); the
# timestamped format must not break that. Tab is safe as the separator precisely
# because mc_canonicalize rejects any path containing a control character.
@test "a root containing a space round-trips through the timestamped format" {
  mc_record_root "$(rp 'code/foo bar')"
  ROOT_TTL_DAYS=14 mc_prune_roots
  run mc_sweep_roots
  [ "$output" = "$(rp 'code/foo bar')" ]
}

# The direct guard for the raw-vs-canonical comparison that this prune path
# briefly carried: prune judged the STORED string against a canonicalized $HOME
# as well as the resolved form. Rows memcap writes are always canonical, so that
# only bit a row that arrived some other way -- and README documents the roots
# file as a plain list of paths, so a hand-added row is a supported way in. One
# written through a symlink resolves somewhere safe and must survive; deleting
# it silently is the same class of failure as v0.1.2 rejecting every root.
@test "a hand-added row that is not already canonical is kept, not silently dropped" {
  mkdir -p "$HOME/.mc-handreal-$$/proj"
  ln -sfn "$HOME/.mc-handreal-$$" "$HOME/.mc-handlink-$$"
  seed_row 0 "$HOME/.mc-handlink-$$/proj"
  ROOT_TTL_DAYS=14 mc_prune_roots
  run mc_sweep_roots
  rm -rf "$HOME/.mc-handreal-$$" "$HOME/.mc-handlink-$$"
  assert_contains "$output" ".mc-handlink-$$/proj"
}

# The v0.1.3 defect class, applied to the new prune path rather than to
# recording. mc_record_root stores the RESOLVED path, so prune has to re-resolve
# and compare canonical-to-canonical. A raw-vs-canonical comparison here would
# silently drop the root of any user whose project sits behind a symlink -- tier
# 1 left with nothing to sweep, no error anywhere, exactly the v0.1.2 failure.
# Uses a real symlinked project directory, not a symlinked $HOME, so it fails in
# NORMAL mode too rather than only under `env HOME=$(mktemp -d)`.
@test "a root recorded through a symlink survives pruning, not just recording" {
  mkdir -p "$HOME/.mc-symreal-$$/proj"
  ln -sfn "$HOME/.mc-symreal-$$" "$HOME/.mc-symlink-$$"
  mc_record_root "$HOME/.mc-symlink-$$/proj"
  ROOT_TTL_DAYS=14 mc_prune_roots
  run mc_sweep_roots
  rm -rf "$HOME/.mc-symreal-$$" "$HOME/.mc-symlink-$$"
  assert_contains "$output" ".mc-symreal-$$/proj"
  assert_not_contains "$output" ".mc-symlink-$$"
}

@test "a root that now resolves outside HOME is pruned regardless of age" {
  seed_row 0 "$(rp .mc-out-$$)/proj"
  ln -sfn /etc "$HOME/.mc-out-$$"
  ROOT_TTL_DAYS=14 mc_prune_roots
  run mc_sweep_roots
  rm -f "$HOME/.mc-out-$$"
  [ -z "$output" ]
}

# Fail closed on a garbage knob: a non-numeric ROOT_TTL_DAYS must fall back to
# the default, not reach arithmetic as a zero (which would prune every root on
# the next pass) or abort the prune mid-file.
@test "a non-numeric ROOT_TTL_DAYS falls back to the default instead of pruning everything" {
  seed_row 3 "$(rp code/recent)"
  seed_row 40 "$(rp code/abandoned)"
  ROOT_TTL_DAYS="abc" mc_prune_roots
  run mc_sweep_roots
  assert_contains "$output" "$(rp code/recent)"
  assert_not_contains "$output" "abandoned"
}

@test "a row with a corrupt timestamp is treated as seen now, not as infinitely old" {
  f="$(mc_roots_file)"; mkdir -p "$(dirname "$f")"
  printf '%s\t%s\n' "notanumber" "$(rp code/projectA)" > "$f"
  ROOT_TTL_DAYS=1 mc_prune_roots
  run mc_sweep_roots
  assert_contains "$output" "$(rp code/projectA)"
}

@test "pruning leaves no temp file behind for a sweep to trip over" {
  mc_record_root "$(rp code/projectA)"
  ROOT_TTL_DAYS=14 mc_prune_roots
  run bash -c "ls -a '$(dirname "$(mc_roots_file)")'"
  assert_not_contains "$output" ".tmp"
}
