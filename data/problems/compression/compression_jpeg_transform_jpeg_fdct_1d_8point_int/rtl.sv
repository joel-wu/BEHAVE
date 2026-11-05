module compression_jpeg_transform_jpeg_fdct_1d_8point_int (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_s0,
    input  logic [15:0]  in_s1,
    input  logic [15:0]  in_s2,
    input  logic [15:0]  in_s3,
    input  logic [15:0]  in_s4,
    input  logic [15:0]  in_s5,
    input  logic [15:0]  in_s6,
    input  logic [15:0]  in_s7,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_y0,
    output logic [15:0]  out_y1,
    output logic [15:0]  out_y2,
    output logic [15:0]  out_y3,
    output logic [15:0]  out_y4,
    output logic [15:0]  out_y5,
    output logic [15:0]  out_y6,
    output logic [15:0]  out_y7
);

    localparam logic signed [15:0] FIX_0_298631336 = 16'sd2446;
    localparam logic signed [15:0] FIX_0_390180644 = 16'sd3196;
    localparam logic signed [15:0] FIX_0_541196100 = 16'sd4433;
    localparam logic signed [15:0] FIX_0_765366865 = 16'sd6270;
    localparam logic signed [15:0] FIX_0_899976223 = 16'sd7373;
    localparam logic signed [15:0] FIX_1_175875602 = 16'sd9633;
    localparam logic signed [15:0] FIX_1_501321110 = 16'sd12299;
    localparam logic signed [15:0] FIX_1_847759065 = 16'sd15137;
    localparam logic signed [15:0] FIX_1_961570560 = 16'sd16069;
    localparam logic signed [15:0] FIX_2_053119869 = 16'sd16819;
    localparam logic signed [15:0] FIX_2_562915447 = 16'sd20995;
    localparam logic signed [15:0] FIX_3_072711026 = 16'sd25172;
    localparam logic signed [31:0] DESCALE_BIAS   = 32'sd1024;

    logic busy;
    wire input_accepted;
    wire output_consumed;

    logic signed [15:0] s0, s1, s2, s3, s4, s5, s6, s7;
    logic signed [17:0] tmp0, tmp1, tmp2, tmp3, tmp4, tmp5, tmp6, tmp7;
    logic signed [18:0] tmp10, tmp11, tmp12, tmp13;
    logic signed [33:0] z1e;
    logic signed [33:0] y2_full, y6_full;
    logic signed [18:0] z1, z2, z3, z4;
    logic signed [34:0] z5;
    logic signed [33:0] tmp4m, tmp5m, tmp6m, tmp7m, z1m, z2m;
    logic signed [34:0] z3m, z4m;
    logic signed [35:0] y7_full, y5_full, y3_full, y1_full;
    logic signed [20:0] y0_full, y4_full;
    logic signed [33:0] y2_shifted, y6_shifted;
    logic signed [35:0] y7_shifted, y5_shifted, y3_shifted, y1_shifted;

    logic [15:0] calc_y0, calc_y1, calc_y2, calc_y3, calc_y4, calc_y5, calc_y6, calc_y7;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_comb begin
        s0 = $signed(in_s0);
        s1 = $signed(in_s1);
        s2 = $signed(in_s2);
        s3 = $signed(in_s3);
        s4 = $signed(in_s4);
        s5 = $signed(in_s5);
        s6 = $signed(in_s6);
        s7 = $signed(in_s7);

        tmp0 = s0 + s7;
        tmp7 = s0 - s7;
        tmp1 = s1 + s6;
        tmp6 = s1 - s6;
        tmp2 = s2 + s5;
        tmp5 = s2 - s5;
        tmp3 = s3 + s4;
        tmp4 = s3 - s4;

        tmp10 = tmp0 + tmp3;
        tmp13 = tmp0 - tmp3;
        tmp11 = tmp1 + tmp2;
        tmp12 = tmp1 - tmp2;

        y0_full = (tmp10 + tmp11) <<< 2;
        y4_full = (tmp10 - tmp11) <<< 2;

        z1e     = (tmp12 + tmp13) * FIX_0_541196100;
        y2_full = z1e + (tmp13 * FIX_0_765366865) + DESCALE_BIAS;
        y6_full = z1e + (tmp12 * (-FIX_1_847759065)) + DESCALE_BIAS;

        z1 = tmp4 + tmp7;
        z2 = tmp5 + tmp6;
        z3 = tmp4 + tmp6;
        z4 = tmp5 + tmp7;
        z5 = (z3 + z4) * FIX_1_175875602;

        tmp4m = tmp4 * FIX_0_298631336;
        tmp5m = tmp5 * FIX_2_053119869;
        tmp6m = tmp6 * FIX_3_072711026;
        tmp7m = tmp7 * FIX_1_501321110;
        z1m   = z1 * (-FIX_0_899976223);
        z2m   = z2 * (-FIX_2_562915447);
        z3m   = (z3 * (-FIX_1_961570560)) + z5;
        z4m   = (z4 * (-FIX_0_390180644)) + z5;

        y7_full = tmp4m + z1m + z3m + DESCALE_BIAS;
        y5_full = tmp5m + z2m + z4m + DESCALE_BIAS;
        y3_full = tmp6m + z2m + z3m + DESCALE_BIAS;
        y1_full = tmp7m + z1m + z4m + DESCALE_BIAS;

        y2_shifted = y2_full >>> 11;
        y6_shifted = y6_full >>> 11;
        y7_shifted = y7_full >>> 11;
        y5_shifted = y5_full >>> 11;
        y3_shifted = y3_full >>> 11;
        y1_shifted = y1_full >>> 11;

        calc_y0 = y0_full[15:0];
        calc_y4 = y4_full[15:0];
        calc_y2 = y2_shifted[15:0];
        calc_y6 = y6_shifted[15:0];
        calc_y7 = y7_shifted[15:0];
        calc_y5 = y5_shifted[15:0];
        calc_y3 = y3_shifted[15:0];
        calc_y1 = y1_shifted[15:0];
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy   <= 1'b0;
            out_y0 <= 16'h0000;
            out_y1 <= 16'h0000;
            out_y2 <= 16'h0000;
            out_y3 <= 16'h0000;
            out_y4 <= 16'h0000;
            out_y5 <= 16'h0000;
            out_y6 <= 16'h0000;
            out_y7 <= 16'h0000;
        end else if (input_accepted) begin
            busy   <= 1'b1;
            out_y0 <= calc_y0;
            out_y1 <= calc_y1;
            out_y2 <= calc_y2;
            out_y3 <= calc_y3;
            out_y4 <= calc_y4;
            out_y5 <= calc_y5;
            out_y6 <= calc_y6;
            out_y7 <= calc_y7;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule