module signal_processing_isp_aux_bnf (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_p00,
    input  logic [15:0]  in_p01,
    input  logic [15:0]  in_p02,
    input  logic [15:0]  in_p03,
    input  logic [15:0]  in_p04,
    input  logic [15:0]  in_p10,
    input  logic [15:0]  in_p11,
    input  logic [15:0]  in_p12,
    input  logic [15:0]  in_p13,
    input  logic [15:0]  in_p14,
    input  logic [15:0]  in_p20,
    input  logic [15:0]  in_p21,
    input  logic [15:0]  in_p22,
    input  logic [15:0]  in_p23,
    input  logic [15:0]  in_p24,
    input  logic [15:0]  in_p30,
    input  logic [15:0]  in_p31,
    input  logic [15:0]  in_p32,
    input  logic [15:0]  in_p33,
    input  logic [15:0]  in_p34,
    input  logic [15:0]  in_p40,
    input  logic [15:0]  in_p41,
    input  logic [15:0]  in_p42,
    input  logic [15:0]  in_p43,
    input  logic [15:0]  in_p44,

    input  logic [15:0]  in_dw00,
    input  logic [15:0]  in_dw01,
    input  logic [15:0]  in_dw02,
    input  logic [15:0]  in_dw03,
    input  logic [15:0]  in_dw04,
    input  logic [15:0]  in_dw10,
    input  logic [15:0]  in_dw11,
    input  logic [15:0]  in_dw12,
    input  logic [15:0]  in_dw13,
    input  logic [15:0]  in_dw14,
    input  logic [15:0]  in_dw20,
    input  logic [15:0]  in_dw21,
    input  logic [15:0]  in_dw22,
    input  logic [15:0]  in_dw23,
    input  logic [15:0]  in_dw24,
    input  logic [15:0]  in_dw30,
    input  logic [15:0]  in_dw31,
    input  logic [15:0]  in_dw32,
    input  logic [15:0]  in_dw33,
    input  logic [15:0]  in_dw34,
    input  logic [15:0]  in_dw40,
    input  logic [15:0]  in_dw41,
    input  logic [15:0]  in_dw42,
    input  logic [15:0]  in_dw43,
    input  logic [15:0]  in_dw44,

    input  logic [15:0]  in_rw0,
    input  logic [15:0]  in_rw1,
    input  logic [15:0]  in_rw2,
    input  logic [15:0]  in_rw3,

    input  logic [15:0]  in_rthres0,
    input  logic [15:0]  in_rthres1,
    input  logic [15:0]  in_rthres2,

    input  logic [15:0]  in_clip,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_pixel
);

function automatic [15:0] abs_diff_u16(
    input logic [15:0] a,
    input logic [15:0] b
);
    begin
        if (a >= b) abs_diff_u16 = a - b;
        else        abs_diff_u16 = b - a;
    end
endfunction

function automatic [15:0] select_rw(
    input logic [15:0] d,
    input logic [15:0] rthres0,
    input logic [15:0] rthres1,
    input logic [15:0] rthres2,
    input logic [15:0] rw0,
    input logic [15:0] rw1,
    input logic [15:0] rw2,
    input logic [15:0] rw3
);
    begin
        if (d >= rthres0)                          select_rw = rw0;
        else if ((d < rthres0) && (d >= rthres1)) select_rw = rw1;
        else if ((d < rthres1) && (d >= rthres2)) select_rw = rw2;
        else                                      select_rw = rw3;
    end
endfunction

logic        busy;
wire         input_accepted;
wire         output_consumed;

logic [15:0] result_pixel_comb;
logic [15:0] result_pixel_reg;

logic [15:0] c_comb;

logic [15:0] d00, d01, d02, d03, d04;
logic [15:0] d10, d11, d12, d13, d14;
logic [15:0] d20, d21, d22, d23, d24;
logic [15:0] d30, d31, d32, d33, d34;
logic [15:0] d40, d41, d42, d43, d44;

logic [15:0] rwsel00, rwsel01, rwsel02, rwsel03, rwsel04;
logic [15:0] rwsel10, rwsel11, rwsel12, rwsel13, rwsel14;
logic [15:0] rwsel20, rwsel21, rwsel22, rwsel23, rwsel24;
logic [15:0] rwsel30, rwsel31, rwsel32, rwsel33, rwsel34;
logic [15:0] rwsel40, rwsel41, rwsel42, rwsel43, rwsel44;

logic [31:0] w00, w01, w02, w03, w04;
logic [31:0] w10, w11, w12, w13, w14;
logic [31:0] w20, w21, w22, w23, w24;
logic [31:0] w30, w31, w32, w33, w34;
logic [31:0] w40, w41, w42, w43, w44;

logic [47:0] pw00, pw01, pw02, pw03, pw04;
logic [47:0] pw10, pw11, pw12, pw13, pw14;
logic [47:0] pw20, pw21, pw22, pw23, pw24;
logic [47:0] pw30, pw31, pw32, pw33, pw34;
logic [47:0] pw40, pw41, pw42, pw43, pw44;

logic [53:0] den_comb;
logic [63:0] num_comb;
logic [63:0] f_comb;
logic [63:0] clipped_comb;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;
assign out_pixel = result_pixel_reg;

always_comb begin
    c_comb = in_p22;

    d00 = abs_diff_u16(in_p00, c_comb);
    d01 = abs_diff_u16(in_p01, c_comb);
    d02 = abs_diff_u16(in_p02, c_comb);
    d03 = abs_diff_u16(in_p03, c_comb);
    d04 = abs_diff_u16(in_p04, c_comb);
    d10 = abs_diff_u16(in_p10, c_comb);
    d11 = abs_diff_u16(in_p11, c_comb);
    d12 = abs_diff_u16(in_p12, c_comb);
    d13 = abs_diff_u16(in_p13, c_comb);
    d14 = abs_diff_u16(in_p14, c_comb);
    d20 = abs_diff_u16(in_p20, c_comb);
    d21 = abs_diff_u16(in_p21, c_comb);
    d22 = abs_diff_u16(in_p22, c_comb);
    d23 = abs_diff_u16(in_p23, c_comb);
    d24 = abs_diff_u16(in_p24, c_comb);
    d30 = abs_diff_u16(in_p30, c_comb);
    d31 = abs_diff_u16(in_p31, c_comb);
    d32 = abs_diff_u16(in_p32, c_comb);
    d33 = abs_diff_u16(in_p33, c_comb);
    d34 = abs_diff_u16(in_p34, c_comb);
    d40 = abs_diff_u16(in_p40, c_comb);
    d41 = abs_diff_u16(in_p41, c_comb);
    d42 = abs_diff_u16(in_p42, c_comb);
    d43 = abs_diff_u16(in_p43, c_comb);
    d44 = abs_diff_u16(in_p44, c_comb);

    rwsel00 = select_rw(d00, in_rthres0, in_rthres1, in_rthres2, in_rw0, in_rw1, in_rw2, in_rw3);
    rwsel01 = select_rw(d01, in_rthres0, in_rthres1, in_rthres2, in_rw0, in_rw1, in_rw2, in_rw3);
    rwsel02 = select_rw(d02, in_rthres0, in_rthres1, in_rthres2, in_rw0, in_rw1, in_rw2, in_rw3);
    rwsel03 = select_rw(d03, in_rthres0, in_rthres1, in_rthres2, in_rw0, in_rw1, in_rw2, in_rw3);
    rwsel04 = select_rw(d04, in_rthres0, in_rthres1, in_rthres2, in_rw0, in_rw1, in_rw2, in_rw3);
    rwsel10 = select_rw(d10, in_rthres0, in_rthres1, in_rthres2, in_rw0, in_rw1, in_rw2, in_rw3);
    rwsel11 = select_rw(d11, in_rthres0, in_rthres1, in_rthres2, in_rw0, in_rw1, in_rw2, in_rw3);
    rwsel12 = select_rw(d12, in_rthres0, in_rthres1, in_rthres2, in_rw0, in_rw1, in_rw2, in_rw3);
    rwsel13 = select_rw(d13, in_rthres0, in_rthres1, in_rthres2, in_rw0, in_rw1, in_rw2, in_rw3);
    rwsel14 = select_rw(d14, in_rthres0, in_rthres1, in_rthres2, in_rw0, in_rw1, in_rw2, in_rw3);
    rwsel20 = select_rw(d20, in_rthres0, in_rthres1, in_rthres2, in_rw0, in_rw1, in_rw2, in_rw3);
    rwsel21 = select_rw(d21, in_rthres0, in_rthres1, in_rthres2, in_rw0, in_rw1, in_rw2, in_rw3);
    rwsel22 = select_rw(d22, in_rthres0, in_rthres1, in_rthres2, in_rw0, in_rw1, in_rw2, in_rw3);
    rwsel23 = select_rw(d23, in_rthres0, in_rthres1, in_rthres2, in_rw0, in_rw1, in_rw2, in_rw3);
    rwsel24 = select_rw(d24, in_rthres0, in_rthres1, in_rthres2, in_rw0, in_rw1, in_rw2, in_rw3);
    rwsel30 = select_rw(d30, in_rthres0, in_rthres1, in_rthres2, in_rw0, in_rw1, in_rw2, in_rw3);
    rwsel31 = select_rw(d31, in_rthres0, in_rthres1, in_rthres2, in_rw0, in_rw1, in_rw2, in_rw3);
    rwsel32 = select_rw(d32, in_rthres0, in_rthres1, in_rthres2, in_rw0, in_rw1, in_rw2, in_rw3);
    rwsel33 = select_rw(d33, in_rthres0, in_rthres1, in_rthres2, in_rw0, in_rw1, in_rw2, in_rw3);
    rwsel34 = select_rw(d34, in_rthres0, in_rthres1, in_rthres2, in_rw0, in_rw1, in_rw2, in_rw3);
    rwsel40 = select_rw(d40, in_rthres0, in_rthres1, in_rthres2, in_rw0, in_rw1, in_rw2, in_rw3);
    rwsel41 = select_rw(d41, in_rthres0, in_rthres1, in_rthres2, in_rw0, in_rw1, in_rw2, in_rw3);
    rwsel42 = select_rw(d42, in_rthres0, in_rthres1, in_rthres2, in_rw0, in_rw1, in_rw2, in_rw3);
    rwsel43 = select_rw(d43, in_rthres0, in_rthres1, in_rthres2, in_rw0, in_rw1, in_rw2, in_rw3);
    rwsel44 = select_rw(d44, in_rthres0, in_rthres1, in_rthres2, in_rw0, in_rw1, in_rw2, in_rw3);

    w00 = rwsel00 * in_dw00;
    w01 = rwsel01 * in_dw01;
    w02 = rwsel02 * in_dw02;
    w03 = rwsel03 * in_dw03;
    w04 = rwsel04 * in_dw04;
    w10 = rwsel10 * in_dw10;
    w11 = rwsel11 * in_dw11;
    w12 = rwsel12 * in_dw12;
    w13 = rwsel13 * in_dw13;
    w14 = rwsel14 * in_dw14;
    w20 = rwsel20 * in_dw20;
    w21 = rwsel21 * in_dw21;
    w22 = rwsel22 * in_dw22;
    w23 = rwsel23 * in_dw23;
    w24 = rwsel24 * in_dw24;
    w30 = rwsel30 * in_dw30;
    w31 = rwsel31 * in_dw31;
    w32 = rwsel32 * in_dw32;
    w33 = rwsel33 * in_dw33;
    w34 = rwsel34 * in_dw34;
    w40 = rwsel40 * in_dw40;
    w41 = rwsel41 * in_dw41;
    w42 = rwsel42 * in_dw42;
    w43 = rwsel43 * in_dw43;
    w44 = rwsel44 * in_dw44;

    pw00 = in_p00 * w00;
    pw01 = in_p01 * w01;
    pw02 = in_p02 * w02;
    pw03 = in_p03 * w03;
    pw04 = in_p04 * w04;
    pw10 = in_p10 * w10;
    pw11 = in_p11 * w11;
    pw12 = in_p12 * w12;
    pw13 = in_p13 * w13;
    pw14 = in_p14 * w14;
    pw20 = in_p20 * w20;
    pw21 = in_p21 * w21;
    pw22 = in_p22 * w22;
    pw23 = in_p23 * w23;
    pw24 = in_p24 * w24;
    pw30 = in_p30 * w30;
    pw31 = in_p31 * w31;
    pw32 = in_p32 * w32;
    pw33 = in_p33 * w33;
    pw34 = in_p34 * w34;
    pw40 = in_p40 * w40;
    pw41 = in_p41 * w41;
    pw42 = in_p42 * w42;
    pw43 = in_p43 * w43;
    pw44 = in_p44 * w44;

    den_comb =
        {22'd0, w00} + {22'd0, w01} + {22'd0, w02} + {22'd0, w03} + {22'd0, w04} +
        {22'd0, w10} + {22'd0, w11} + {22'd0, w12} + {22'd0, w13} + {22'd0, w14} +
        {22'd0, w20} + {22'd0, w21} + {22'd0, w22} + {22'd0, w23} + {22'd0, w24} +
        {22'd0, w30} + {22'd0, w31} + {22'd0, w32} + {22'd0, w33} + {22'd0, w34} +
        {22'd0, w40} + {22'd0, w41} + {22'd0, w42} + {22'd0, w43} + {22'd0, w44};

    num_comb =
        {16'd0, pw00} + {16'd0, pw01} + {16'd0, pw02} + {16'd0, pw03} + {16'd0, pw04} +
        {16'd0, pw10} + {16'd0, pw11} + {16'd0, pw12} + {16'd0, pw13} + {16'd0, pw14} +
        {16'd0, pw20} + {16'd0, pw21} + {16'd0, pw22} + {16'd0, pw23} + {16'd0, pw24} +
        {16'd0, pw30} + {16'd0, pw31} + {16'd0, pw32} + {16'd0, pw33} + {16'd0, pw34} +
        {16'd0, pw40} + {16'd0, pw41} + {16'd0, pw42} + {16'd0, pw43} + {16'd0, pw44};

    if (den_comb == 54'd0) f_comb = 64'd0;
    else                   f_comb = num_comb / den_comb;

    if (f_comb > {48'd0, in_clip}) clipped_comb = {48'd0, in_clip};
    else                            clipped_comb = f_comb;

    result_pixel_comb = clipped_comb[15:0];
end

always_ff @(posedge clk or posedge rst) begin
    if (rst) begin
        busy             <= 1'b0;
        result_pixel_reg <= 16'h0000;
    end else if (input_accepted) begin
        busy             <= 1'b1;
        result_pixel_reg <= result_pixel_comb;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule