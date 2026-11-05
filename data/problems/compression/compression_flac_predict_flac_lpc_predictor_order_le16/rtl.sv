module compression_flac_predict_flac_lpc_predictor_order_le16 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic  [4:0]  in_order,
    input  logic  [4:0]  in_lp_quantization,
    input  logic  [31:0] in_sample,
    input  logic  [31:0] in_warmup0,
    input  logic  [31:0] in_warmup1,
    input  logic  [31:0] in_warmup2,
    input  logic  [31:0] in_warmup3,
    input  logic  [31:0] in_warmup4,
    input  logic  [31:0] in_warmup5,
    input  logic  [31:0] in_warmup6,
    input  logic  [31:0] in_warmup7,
    input  logic  [31:0] in_warmup8,
    input  logic  [31:0] in_warmup9,
    input  logic  [31:0] in_warmup10,
    input  logic  [31:0] in_warmup11,
    input  logic  [31:0] in_warmup12,
    input  logic  [31:0] in_warmup13,
    input  logic  [31:0] in_warmup14,
    input  logic  [31:0] in_warmup15,
    input  logic  [31:0] in_qlp_coeff0,
    input  logic  [31:0] in_qlp_coeff1,
    input  logic  [31:0] in_qlp_coeff2,
    input  logic  [31:0] in_qlp_coeff3,
    input  logic  [31:0] in_qlp_coeff4,
    input  logic  [31:0] in_qlp_coeff5,
    input  logic  [31:0] in_qlp_coeff6,
    input  logic  [31:0] in_qlp_coeff7,
    input  logic  [31:0] in_qlp_coeff8,
    input  logic  [31:0] in_qlp_coeff9,
    input  logic  [31:0] in_qlp_coeff10,
    input  logic  [31:0] in_qlp_coeff11,
    input  logic  [31:0] in_qlp_coeff12,
    input  logic  [31:0] in_qlp_coeff13,
    input  logic  [31:0] in_qlp_coeff14,
    input  logic  [31:0] in_qlp_coeff15,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_residual
);

logic busy;
wire input_accepted;
wire output_consumed;

logic signed [31:0] sample_s;
logic signed [31:0] history0_s,  history1_s,  history2_s,  history3_s;
logic signed [31:0] history4_s,  history5_s,  history6_s,  history7_s;
logic signed [31:0] history8_s,  history9_s,  history10_s, history11_s;
logic signed [31:0] history12_s, history13_s, history14_s, history15_s;
logic signed [31:0] coeff0_s,    coeff1_s,    coeff2_s,    coeff3_s;
logic signed [31:0] coeff4_s,    coeff5_s,    coeff6_s,    coeff7_s;
logic signed [31:0] coeff8_s,    coeff9_s,    coeff10_s,   coeff11_s;
logic signed [31:0] coeff12_s,   coeff13_s,   coeff14_s,   coeff15_s;

logic signed [63:0] prod0,  prod1,  prod2,  prod3;
logic signed [63:0] prod4,  prod5,  prod6,  prod7;
logic signed [63:0] prod8,  prod9,  prod10, prod11;
logic signed [63:0] prod12, prod13, prod14, prod15;

logic signed [67:0] total;
logic signed [67:0] prediction;
logic signed [67:0] residual_wide;
logic [31:0] residual_bits;

assign in_ready = ~busy | output_consumed;
assign out_valid = busy;

assign input_accepted = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

always_comb begin
    sample_s    = $signed(in_sample);

    history0_s  = $signed(in_warmup0);
    history1_s  = $signed(in_warmup1);
    history2_s  = $signed(in_warmup2);
    history3_s  = $signed(in_warmup3);
    history4_s  = $signed(in_warmup4);
    history5_s  = $signed(in_warmup5);
    history6_s  = $signed(in_warmup6);
    history7_s  = $signed(in_warmup7);
    history8_s  = $signed(in_warmup8);
    history9_s  = $signed(in_warmup9);
    history10_s = $signed(in_warmup10);
    history11_s = $signed(in_warmup11);
    history12_s = $signed(in_warmup12);
    history13_s = $signed(in_warmup13);
    history14_s = $signed(in_warmup14);
    history15_s = $signed(in_warmup15);

    coeff0_s    = $signed(in_qlp_coeff0);
    coeff1_s    = $signed(in_qlp_coeff1);
    coeff2_s    = $signed(in_qlp_coeff2);
    coeff3_s    = $signed(in_qlp_coeff3);
    coeff4_s    = $signed(in_qlp_coeff4);
    coeff5_s    = $signed(in_qlp_coeff5);
    coeff6_s    = $signed(in_qlp_coeff6);
    coeff7_s    = $signed(in_qlp_coeff7);
    coeff8_s    = $signed(in_qlp_coeff8);
    coeff9_s    = $signed(in_qlp_coeff9);
    coeff10_s   = $signed(in_qlp_coeff10);
    coeff11_s   = $signed(in_qlp_coeff11);
    coeff12_s   = $signed(in_qlp_coeff12);
    coeff13_s   = $signed(in_qlp_coeff13);
    coeff14_s   = $signed(in_qlp_coeff14);
    coeff15_s   = $signed(in_qlp_coeff15);

    prod0  = (5'd0  < in_order) ? (coeff0_s  * history0_s)  : 64'sd0;
    prod1  = (5'd1  < in_order) ? (coeff1_s  * history1_s)  : 64'sd0;
    prod2  = (5'd2  < in_order) ? (coeff2_s  * history2_s)  : 64'sd0;
    prod3  = (5'd3  < in_order) ? (coeff3_s  * history3_s)  : 64'sd0;
    prod4  = (5'd4  < in_order) ? (coeff4_s  * history4_s)  : 64'sd0;
    prod5  = (5'd5  < in_order) ? (coeff5_s  * history5_s)  : 64'sd0;
    prod6  = (5'd6  < in_order) ? (coeff6_s  * history6_s)  : 64'sd0;
    prod7  = (5'd7  < in_order) ? (coeff7_s  * history7_s)  : 64'sd0;
    prod8  = (5'd8  < in_order) ? (coeff8_s  * history8_s)  : 64'sd0;
    prod9  = (5'd9  < in_order) ? (coeff9_s  * history9_s)  : 64'sd0;
    prod10 = (5'd10 < in_order) ? (coeff10_s * history10_s) : 64'sd0;
    prod11 = (5'd11 < in_order) ? (coeff11_s * history11_s) : 64'sd0;
    prod12 = (5'd12 < in_order) ? (coeff12_s * history12_s) : 64'sd0;
    prod13 = (5'd13 < in_order) ? (coeff13_s * history13_s) : 64'sd0;
    prod14 = (5'd14 < in_order) ? (coeff14_s * history14_s) : 64'sd0;
    prod15 = (5'd15 < in_order) ? (coeff15_s * history15_s) : 64'sd0;

    total =
        {{4{prod0[63]}},  prod0 } +
        {{4{prod1[63]}},  prod1 } +
        {{4{prod2[63]}},  prod2 } +
        {{4{prod3[63]}},  prod3 } +
        {{4{prod4[63]}},  prod4 } +
        {{4{prod5[63]}},  prod5 } +
        {{4{prod6[63]}},  prod6 } +
        {{4{prod7[63]}},  prod7 } +
        {{4{prod8[63]}},  prod8 } +
        {{4{prod9[63]}},  prod9 } +
        {{4{prod10[63]}}, prod10} +
        {{4{prod11[63]}}, prod11} +
        {{4{prod12[63]}}, prod12} +
        {{4{prod13[63]}}, prod13} +
        {{4{prod14[63]}}, prod14} +
        {{4{prod15[63]}}, prod15};

    prediction = total >>> in_lp_quantization;
    residual_wide = {{36{sample_s[31]}}, sample_s} - prediction;
    residual_bits = residual_wide[31:0];
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy         <= 1'b0;
        out_residual <= 32'h00000000;
    end else if (input_accepted) begin
        busy         <= 1'b1;
        out_residual <= residual_bits;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule