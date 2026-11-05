"""Predictive Nangate45 profile, not a foundry manufacturing PDK.

RC/LEFs: OpenROAD-flow-scripts 4328a028fde2d83984fda06afd37389feff16758.
This is the shared pre-CTS, placement-estimated flow, not post-route signoff.
"""

PROFILE = 'nangate45-placement-rc/3'
# Functional exclusions from the upstream platform configuration.
DONT_USE_CELLS = ('AOI211_X1', 'OAI211_X1')

RC = '''set_layer_rc -layer metal1 -resistance 5.4286e-03 -capacitance 7.41819E-02
set_layer_rc -layer metal2 -resistance 3.57167E-03 -capacitance 8.33611E-02
set_layer_rc -layer metal3 -resistance 3.57147E-03 -capacitance 1.03981E-01
set_layer_rc -layer metal4 -resistance 1.50001E-03 -capacitance 1.19150E-01
set_layer_rc -layer metal5 -resistance 1.50000E-03 -capacitance 1.09256E-01
set_layer_rc -layer metal6 -resistance 1.50000E-03 -capacitance 1.14168E-01
set_layer_rc -layer metal7 -resistance 1.87501E-04 -capacitance 1.17491E-01
set_layer_rc -layer metal8 -resistance 1.87501E-04 -capacitance 9.45346E-02
set_layer_rc -layer metal9 -resistance 3.74996E-05 -capacitance 1.06091E-01
set_layer_rc -layer metal10 -resistance 3.75000E-05 -capacitance 7.37095E-01
set_wire_rc -signal -layer metal3
set_wire_rc -clock -layer metal5
'''

# Utilization/density and repair recipe match SKY130; site/layers are native.
PLACEMENT = '''initialize_floorplan -utilization 40 -aspect_ratio 1 -core_space 10 -site FreePDK45_38x28_10R_NP_162NW_34O
make_tracks
place_pins -hor_layers metal5 -ver_layers metal6
global_placement -density 0.60
detailed_placement
estimate_parasitics -placement
repair_design
detailed_placement
check_placement -verbose
estimate_parasitics -placement
'''
