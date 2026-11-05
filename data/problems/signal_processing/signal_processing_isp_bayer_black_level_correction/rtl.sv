module signal_processing_isp_bayer_black_level_correction (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_pixel,
    input  logic        in_row_lsb,
    input  logic        in_col_lsb,
    input  logic [1:0]  in_bayer_pattern,
    input  logic        in_is_linear,
    input  logic [4:0]  in_bit_depth,

    input  logic [15:0] in_r_offset,
    input  logic [15:0] in_gr_offset,
    input  logic [15:0] in_gb_offset,
    input  logic [15:0] in_b_offset,

    input  logic [15:0] in_r_sat,
    input  logic [15:0] in_gr_sat,
    input  logic [15:0] in_gb_sat,
    input  logic [15:0] in_b_sat,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [15:0] out_pixel
);

logic busy;
logic [15:0] offsets [0:3], saturation [0:3];
logic [1:0] plane, phase, pattern_mask;
logic [15:0] full_scale, next_pixel;
logic signed [63:0] corrected, denominator, factor, value, rounded;

assign in_ready = !busy || out_ready;
assign out_valid = busy;

always_comb begin
    offsets[0]=in_r_offset; offsets[1]=in_gr_offset;
    offsets[2]=in_gb_offset; offsets[3]=in_b_offset;
    saturation[0]=in_r_sat; saturation[1]=in_gr_sat;
    saturation[2]=in_gb_sat; saturation[3]=in_b_sat;
    phase = {in_row_lsb,in_col_lsb};
    case (in_bayer_pattern)
        2'd0: pattern_mask=2'd0;
        2'd1: pattern_mask=2'd3;
        2'd2: pattern_mask=2'd1;
        default: pattern_mask=2'd2;
    endcase
    plane = phase ^ pattern_mask;
    if (in_bit_depth >= 16) full_scale=16'hffff;
    else full_scale=(16'd1 << in_bit_depth)-16'd1;
    corrected = {48'b0,in_pixel};
    corrected = corrected - {48'b0,offsets[plane]};
    denominator = {48'b0,saturation[plane]};
    denominator = denominator - {48'b0,offsets[plane]};
    factor=0;
    if (denominator > 0) begin
        factor={48'b0,full_scale};
        factor=(factor <<< 14)/denominator;
        if (factor > 65535) factor=65535;
    end
    value=corrected*factor;
    if (!in_is_linear) rounded=corrected;
    else if (value >= 0) rounded=(value+64'sd8192)/64'sd16384;
    else rounded=-((-value+64'sd8192)/64'sd16384);
    if (rounded < 0) next_pixel=0;
    else if (rounded > {48'b0,full_scale}) next_pixel=full_scale;
    else next_pixel=rounded[15:0];
end

always_ff @(posedge clk or posedge rst) begin
    if (rst) begin
        busy<=1'b0;
        out_pixel<=16'd0;
    end else if (in_valid && in_ready) begin
        busy<=1'b1;
        out_pixel<=next_pixel;
    end else if (out_valid && out_ready) busy<=1'b0;
end
endmodule
