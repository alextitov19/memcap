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

@test "agents keep at least 1 GB at every machine size" {
  for m in 8 16 24 32 64; do
    cap=$(mc_cap_gb "$m")
    d=$(mc_docker_gb "$cap")
    [ $((cap - d)) -ge 1 ]
  done
}

@test "stacks profile gives docker the larger share" {
  run mc_profile_split 16 stacks
  [ "$output" = "10 6" ]
}

@test "mobile profile gives agents the larger share" {
  run mc_profile_split 16 mobile
  [ "$output" = "4 12" ]
}

# --- C1: sims count toward the combined cap but cannot drive tier 2 ----------
# classify.sh folds sim footprint into AGENT_KB, so tier 2 (which cannot touch
# sims -- only tier 3 can) must trigger on AGENT_KB net of SIM_KB, not on the
# gross figure.
@test "agent net is agent minus sim" {
  run mc_agent_net_kb 12000000 9000000
  [ "$output" = "3000000" ]
}

@test "agent net floors at zero rather than going negative" {
  run mc_agent_net_kb 1000 5000
  [ "$output" = "0" ]
}

@test "agent net with no sim footprint equals the gross figure" {
  run mc_agent_net_kb 5000000 0
  [ "$output" = "5000000" ]
}

@test "agent net defaults both inputs to zero" {
  run mc_agent_net_kb
  [ "$output" = "0" ]
}
