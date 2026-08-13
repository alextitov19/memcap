load helper

setup() { setup_common;
  # shellcheck source=/dev/null
  source "$MEMCAP_ROOT/libexec/budget.sh";
}

@test "reserve is clamped to a 6 GB floor" { run mc_reserve_gb 8;  [ "${output%.*}" = "6" ]; }
@test "reserve is 35 percent mid-range"    { run mc_reserve_gb 24; [ "${output%.*}" = "8" ]; }
@test "reserve is clamped to a 16 GB cap"  { run mc_reserve_gb 64; [ "${output%.*}" = "16" ]; }

@test "cap for 16 GB is 10" { run mc_cap_gb 16; [ "$output" = "10" ]; }
@test "cap for 24 GB is 16" { run mc_cap_gb 24; [ "$output" = "16" ]; }
@test "cap for 32 GB is 21" { run mc_cap_gb 32; [ "$output" = "21" ]; }
@test "cap for 64 GB is 48" { run mc_cap_gb 64; [ "$output" = "48" ]; }

@test "docker slice for 16 GB machine is 4" { run mc_docker_gb 10; [ "$output" = "4" ]; }
@test "docker slice for 24 GB machine is 6" { run mc_docker_gb 16; [ "$output" = "6" ]; }
@test "docker slice for 32 GB machine is 8" { run mc_docker_gb 21; [ "$output" = "8" ]; }
@test "docker slice is clamped to 12 on big machines" { run mc_docker_gb 48; [ "$output" = "12" ]; }

@test "tiny machines still get a usable cap" {
  run mc_cap_gb 8
  [ "$output" -ge 3 ]
}

@test "stacks profile gives docker the larger share" {
  run mc_profile_split 16 stacks
  [ "$output" = "10 6" ]
}

@test "mobile profile gives agents the larger share" {
  run mc_profile_split 16 mobile
  [ "$output" = "4 12" ]
}
