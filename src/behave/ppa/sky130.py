"""Pinned SKY130 HD physical profile shared by PPA measurement stages.

Cell exclusions and RC: OpenROAD-flow-scripts revision
4328a028fde2d83984fda06afd37389feff16758, flow/platforms/sky130hd.
The placement profile is pre-CTS, with placement-estimated signal parasitics.
"""

PROFILE = 'sky130hd-placement-rc/3'
DONT_USE_CELLS = tuple('sky130_fd_sc_hd__' + name for name in (
    'probe_p_8', 'probec_p_8', 'lpflow_bleeder_1',
    'lpflow_clkbufkapwr_1', 'lpflow_clkbufkapwr_16', 'lpflow_clkbufkapwr_2',
    'lpflow_clkbufkapwr_4', 'lpflow_clkbufkapwr_8',
    'lpflow_clkinvkapwr_1', 'lpflow_clkinvkapwr_16', 'lpflow_clkinvkapwr_2',
    'lpflow_clkinvkapwr_4', 'lpflow_clkinvkapwr_8',
    'lpflow_decapkapwr_12', 'lpflow_decapkapwr_3', 'lpflow_decapkapwr_4',
    'lpflow_decapkapwr_6', 'lpflow_decapkapwr_8',
    'lpflow_inputiso0n_1', 'lpflow_inputiso0p_1', 'lpflow_inputiso1n_1',
    'lpflow_inputiso1p_1', 'lpflow_inputisolatch_1',
    'lpflow_isobufsrc_1', 'lpflow_isobufsrc_16', 'lpflow_isobufsrc_2',
    'lpflow_isobufsrc_4', 'lpflow_isobufsrc_8', 'lpflow_isobufsrckapwr_16',
    'lpflow_lsbuf_lh_hl_isowell_tap_1', 'lpflow_lsbuf_lh_hl_isowell_tap_2',
    'lpflow_lsbuf_lh_hl_isowell_tap_4', 'lpflow_lsbuf_lh_isowell_4',
    'lpflow_lsbuf_lh_isowell_tap_1', 'lpflow_lsbuf_lh_isowell_tap_2',
    'lpflow_lsbuf_lh_isowell_tap_4',
))

RC = '''set_layer_rc -layer li1 -capacitance 1.499e-04 -resistance 7.176e-02
set_layer_rc -layer met1 -capacitance 1.72375E-04 -resistance 1.20565E-03
set_layer_rc -layer met2 -capacitance 1.36233E-04 -resistance 1.22132E-03
set_layer_rc -layer met3 -capacitance 2.14962E-04 -resistance 1.66281E-04
set_layer_rc -layer met4 -capacitance 1.48128E-04 -resistance 1.68093E-04
set_layer_rc -layer met5 -capacitance 1.54087E-04 -resistance 1.83558E-05
set_layer_rc -via mcon -resistance 9.249146E-3
set_layer_rc -via via -resistance 4.5E-3
set_layer_rc -via via2 -resistance 3.368786E-3
set_layer_rc -via via3 -resistance 0.376635E-3
set_layer_rc -via via4 -resistance 0.00580E-3
set_wire_rc -signal -layer met1
set_wire_rc -clock -layer met3
'''

PLACEMENT = '''initialize_floorplan -utilization 40 -aspect_ratio 1 -core_space 10 -site unithd
make_tracks
place_pins -hor_layers met3 -ver_layers met2
global_placement -density 0.60
detailed_placement
estimate_parasitics -placement
repair_design
detailed_placement
check_placement -verbose
estimate_parasitics -placement
'''
