module real_fft_split_q15_16 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_pSrc_0,
    input  logic [15:0]  in_pSrc_1,
    input  logic [15:0]  in_pSrc_2,
    input  logic [15:0]  in_pSrc_3,
    input  logic [15:0]  in_pSrc_4,
    input  logic [15:0]  in_pSrc_5,
    input  logic [15:0]  in_pSrc_6,
    input  logic [15:0]  in_pSrc_7,
    input  logic [15:0]  in_pSrc_8,
    input  logic [15:0]  in_pSrc_9,
    input  logic [15:0]  in_pSrc_10,
    input  logic [15:0]  in_pSrc_11,
    input  logic [15:0]  in_pSrc_12,
    input  logic [15:0]  in_pSrc_13,
    input  logic [15:0]  in_pSrc_14,
    input  logic [15:0]  in_pSrc_15,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_pDst_0,
    output logic [15:0]  out_pDst_1,
    output logic [15:0]  out_pDst_2,
    output logic [15:0]  out_pDst_3,
    output logic [15:0]  out_pDst_4,
    output logic [15:0]  out_pDst_5,
    output logic [15:0]  out_pDst_6,
    output logic [15:0]  out_pDst_7,
    output logic [15:0]  out_pDst_8,
    output logic [15:0]  out_pDst_9,
    output logic [15:0]  out_pDst_10,
    output logic [15:0]  out_pDst_11,
    output logic [15:0]  out_pDst_12,
    output logic [15:0]  out_pDst_13,
    output logic [15:0]  out_pDst_14,
    output logic [15:0]  out_pDst_15,
    output logic [15:0]  out_pDst_16,
    output logic [15:0]  out_pDst_17
);

logic busy;
wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

function automatic logic [15:0] split_outR(
    input logic [15:0] src1_r_u,
    input logic [15:0] src1_i_u,
    input logic [15:0] src2_r_u,
    input logic [15:0] src2_i_u,
    input logic signed [15:0] A_r,
    input logic signed [15:0] A_i,
    input logic signed [15:0] B_r,
    input logic signed [15:0] B_i
);
    logic signed [15:0] src1_r_s;
    logic signed [15:0] src1_i_s;
    logic signed [15:0] src2_r_s;
    logic signed [15:0] src2_i_s;
    logic signed [31:0] acc;
    logic signed [31:0] shifted;
    begin
        src1_r_s = $signed(src1_r_u);
        src1_i_s = $signed(src1_i_u);
        src2_r_s = $signed(src2_r_u);
        src2_i_s = $signed(src2_i_u);

        acc = src1_r_s * A_r;
        acc = acc - (src1_i_s * A_i);
        acc = acc + (src2_r_s * B_r);
        acc = acc + (src2_i_s * B_i);
        shifted = acc >>> 16;
        split_outR = shifted[15:0];
    end
endfunction

function automatic logic [15:0] split_outI(
    input logic [15:0] src1_r_u,
    input logic [15:0] src1_i_u,
    input logic [15:0] src2_r_u,
    input logic [15:0] src2_i_u,
    input logic signed [15:0] A_r,
    input logic signed [15:0] A_i,
    input logic signed [15:0] B_r,
    input logic signed [15:0] B_i
);
    logic signed [15:0] src1_r_s;
    logic signed [15:0] src1_i_s;
    logic signed [15:0] src2_r_s;
    logic signed [15:0] src2_i_s;
    logic signed [31:0] acc;
    logic signed [31:0] shifted;
    begin
        src1_r_s = $signed(src1_r_u);
        src1_i_s = $signed(src1_i_u);
        src2_r_s = $signed(src2_r_u);
        src2_i_s = $signed(src2_i_u);

        acc = src2_r_s * B_i;
        acc = acc - (src2_i_s * B_r);
        acc = acc + (src1_i_s * A_r);
        acc = acc + (src1_r_s * A_i);
        shifted = acc >>> 16;
        split_outI = shifted[15:0];
    end
endfunction

function automatic logic [15:0] half_sum(
    input logic [15:0] a_u,
    input logic [15:0] b_u
);
    logic signed [15:0] a_s;
    logic signed [15:0] b_s;
    logic signed [16:0] tmp;
    logic signed [16:0] shifted;
    begin
        a_s = $signed(a_u);
        b_s = $signed(b_u);
        tmp = a_s + b_s;
        shifted = tmp >>> 1;
        half_sum = shifted[15:0];
    end
endfunction

function automatic logic [15:0] half_diff(
    input logic [15:0] a_u,
    input logic [15:0] b_u
);
    logic signed [15:0] a_s;
    logic signed [15:0] b_s;
    logic signed [16:0] tmp;
    logic signed [16:0] shifted;
    begin
        a_s = $signed(a_u);
        b_s = $signed(b_u);
        tmp = a_s - b_s;
        shifted = tmp >>> 1;
        half_diff = shifted[15:0];
    end
endfunction

always_ff @(posedge clk) begin
    if (rst) begin
        busy        <= 1'b0;
        out_pDst_0  <= 16'h0000;
        out_pDst_1  <= 16'h0000;
        out_pDst_2  <= 16'h0000;
        out_pDst_3  <= 16'h0000;
        out_pDst_4  <= 16'h0000;
        out_pDst_5  <= 16'h0000;
        out_pDst_6  <= 16'h0000;
        out_pDst_7  <= 16'h0000;
        out_pDst_8  <= 16'h0000;
        out_pDst_9  <= 16'h0000;
        out_pDst_10 <= 16'h0000;
        out_pDst_11 <= 16'h0000;
        out_pDst_12 <= 16'h0000;
        out_pDst_13 <= 16'h0000;
        out_pDst_14 <= 16'h0000;
        out_pDst_15 <= 16'h0000;
        out_pDst_16 <= 16'h0000;
        out_pDst_17 <= 16'h0000;
    end else if (input_accepted) begin
        out_pDst_0  <= half_sum(in_pSrc_0, in_pSrc_1);
        out_pDst_1  <= 16'h0000;

        out_pDst_2  <= split_outR(in_pSrc_2, in_pSrc_3, in_pSrc_14, in_pSrc_15, 16'sd10114, -16'sd15137, 16'sd22654, 16'sd15137);
        out_pDst_3  <= split_outI(in_pSrc_2, in_pSrc_3, in_pSrc_14, in_pSrc_15, 16'sd10114, -16'sd15137, 16'sd22654, 16'sd15137);

        out_pDst_4  <= split_outR(in_pSrc_4, in_pSrc_5, in_pSrc_12, in_pSrc_13, 16'sd4799, -16'sd11585, 16'sd27969, 16'sd11585);
        out_pDst_5  <= split_outI(in_pSrc_4, in_pSrc_5, in_pSrc_12, in_pSrc_13, 16'sd4799, -16'sd11585, 16'sd27969, 16'sd11585);

        out_pDst_6  <= split_outR(in_pSrc_6, in_pSrc_7, in_pSrc_10, in_pSrc_11, 16'sd1247, -16'sd6270, 16'sd31521, 16'sd6270);
        out_pDst_7  <= split_outI(in_pSrc_6, in_pSrc_7, in_pSrc_10, in_pSrc_11, 16'sd1247, -16'sd6270, 16'sd31521, 16'sd6270);

        out_pDst_8  <= split_outR(in_pSrc_8, in_pSrc_9, in_pSrc_8, in_pSrc_9, 16'sd0, 16'sd0, 16'sd32767, 16'sd0);
        out_pDst_9  <= split_outI(in_pSrc_8, in_pSrc_9, in_pSrc_8, in_pSrc_9, 16'sd0, 16'sd0, 16'sd32767, 16'sd0);

        out_pDst_10 <= split_outR(in_pSrc_10, in_pSrc_11, in_pSrc_6, in_pSrc_7, 16'sd1247, 16'sd6270, 16'sd31521, -16'sd6270);
        out_pDst_11 <= split_outI(in_pSrc_10, in_pSrc_11, in_pSrc_6, in_pSrc_7, 16'sd1247, 16'sd6270, 16'sd31521, -16'sd6270);

        out_pDst_12 <= split_outR(in_pSrc_12, in_pSrc_13, in_pSrc_4, in_pSrc_5, 16'sd4799, 16'sd11585, 16'sd27969, -16'sd11585);
        out_pDst_13 <= split_outI(in_pSrc_12, in_pSrc_13, in_pSrc_4, in_pSrc_5, 16'sd4799, 16'sd11585, 16'sd27969, -16'sd11585);

        out_pDst_14 <= split_outR(in_pSrc_14, in_pSrc_15, in_pSrc_2, in_pSrc_3, 16'sd10114, 16'sd15137, 16'sd22654, -16'sd15137);
        out_pDst_15 <= split_outI(in_pSrc_14, in_pSrc_15, in_pSrc_2, in_pSrc_3, 16'sd10114, 16'sd15137, 16'sd22654, -16'sd15137);

        out_pDst_16 <= half_diff(in_pSrc_0, in_pSrc_1);
        out_pDst_17 <= 16'h0000;

        busy <= 1'b1;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule
