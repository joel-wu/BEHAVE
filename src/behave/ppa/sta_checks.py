"""All STA sanity checks, with constant-aware endpoint handling.

Pinned OpenSTA aa598a2 lacks the isConstant exclusion in CheckTiming's
unconstrained-endpoint check (present upstream). Its own constant propagator
can identify these endpoints. Keep every other diagnostic, and never impose
case analysis, false paths, or extra timing exceptions to make a check pass.
"""

SETUP_CHECKS = r'''set behave_checks [sta::check_timing_cmd 1 1 1 1 1 1 1]
foreach finding $behave_checks {
  set message [lindex $finding 0]
  if {[regexp {^Warning: There (is|are) [0-9]+ unconstrained endpoints?\.$} $message]} {
    set pending {}
    foreach name [lrange $finding 1 end] {
      set pin [sta::find_pin $name]
      set value X
      if {$pin != "NULL"} { set value [sta::pin_sim_logic_value $pin] }
      if {$value eq "0" || $value eq "1"} {
        puts "BEHAVE_PPA_CONSTANT_ENDPOINT $name $value"
      } else {
        lappend pending $name
      }
    }
    if {[llength $pending] == 0} { continue }
    set finding [linsert $pending 0 "Warning: There are [llength $pending] unconstrained endpoints."]
  }
  puts [lindex $finding 0]
  foreach name [lrange $finding 1 end] { puts "  $name" }
}
'''
