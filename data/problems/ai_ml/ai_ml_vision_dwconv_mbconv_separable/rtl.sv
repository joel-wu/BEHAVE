module DwConvMbConvSeparable (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [71:0]  in_patch0,
    input  logic [71:0]  in_patch1,
    input  logic [71:0]  in_patch2,
    input  logic [71:0]  in_patch3,
    input  logic [71:0]  in_dw_w0,
    input  logic [71:0]  in_dw_w1,
    input  logic [71:0]  in_dw_w2,
    input  logic [71:0]  in_dw_w3,
    input  logic [7:0]   in_se0,
    input  logic [7:0]   in_se1,
    input  logic [7:0]   in_se2,
    input  logic [7:0]   in_se3,
    input  logic [31:0]  in_pw_row0,
    input  logic [31:0]  in_pw_row1,
    input  logic [31:0]  in_pw_row2,
    input  logic [31:0]  in_pw_row3,
    input  logic         in_use_se,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_y0,
    output logic [15:0]  out_y1,
    output logic [15:0]  out_y2,
    output logic [15:0]  out_y3
);

logic busy;
wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

function automatic logic signed [7:0] get_s8_72(input logic [71:0] word, input integer idx);
    logic [7:0] tmp;
    begin
        tmp = word[idx*8 +: 8];
        get_s8_72 = $signed(tmp);
    end
endfunction

function automatic logic signed [7:0] get_s8_32(input logic [31:0] word, input integer idx);
    logic [7:0] tmp;
    begin
        tmp = word[idx*8 +: 8];
        get_s8_32 = $signed(tmp);
    end
endfunction

function automatic logic signed [31:0] trunc_div256_toward_zero(input logic signed [31:0] n);
    begin
        if (n >= 0)
            trunc_div256_toward_zero = n / 32'sd256;
        else
            trunc_div256_toward_zero = -((-n) / 32'sd256);
    end
endfunction

logic signed [31:0] dw_sum0, dw_sum1, dw_sum2, dw_sum3;
logic signed [31:0] scaled0, scaled1, scaled2, scaled3;
logic signed [31:0] pw_sum0, pw_sum1, pw_sum2, pw_sum3;

integer i;
logic signed [31:0] acc0, acc1, acc2, acc3;
logic signed [31:0] se_prod0, se_prod1, se_prod2, se_prod3;

always_comb begin
    acc0 = 32'sd0;
    acc1 = 32'sd0;
    acc2 = 32'sd0;
    acc3 = 32'sd0;
    for (i = 0; i < 9; i = i + 1) begin
        acc0 = acc0 + get_s8_72(in_patch0, i) * get_s8_72(in_dw_w0, i);
        acc1 = acc1 + get_s8_72(in_patch1, i) * get_s8_72(in_dw_w1, i);
        acc2 = acc2 + get_s8_72(in_patch2, i) * get_s8_72(in_dw_w2, i);
        acc3 = acc3 + get_s8_72(in_patch3, i) * get_s8_72(in_dw_w3, i);
    end
    dw_sum0 = acc0;
    dw_sum1 = acc1;
    dw_sum2 = acc2;
    dw_sum3 = acc3;

    se_prod0 = dw_sum0 * $signed({1'b0, in_se0});
    se_prod1 = dw_sum1 * $signed({1'b0, in_se1});
    se_prod2 = dw_sum2 * $signed({1'b0, in_se2});
    se_prod3 = dw_sum3 * $signed({1'b0, in_se3});

    if (in_use_se) begin
        scaled0 = trunc_div256_toward_zero(se_prod0);
        scaled1 = trunc_div256_toward_zero(se_prod1);
        scaled2 = trunc_div256_toward_zero(se_prod2);
        scaled3 = trunc_div256_toward_zero(se_prod3);
    end else begin
        scaled0 = dw_sum0;
        scaled1 = dw_sum1;
        scaled2 = dw_sum2;
        scaled3 = dw_sum3;
    end

    pw_sum0 =
        scaled0 * get_s8_32(in_pw_row0, 0) +
        scaled1 * get_s8_32(in_pw_row0, 1) +
        scaled2 * get_s8_32(in_pw_row0, 2) +
        scaled3 * get_s8_32(in_pw_row0, 3);

    pw_sum1 =
        scaled0 * get_s8_32(in_pw_row1, 0) +
        scaled1 * get_s8_32(in_pw_row1, 1) +
        scaled2 * get_s8_32(in_pw_row1, 2) +
        scaled3 * get_s8_32(in_pw_row1, 3);

    pw_sum2 =
        scaled0 * get_s8_32(in_pw_row2, 0) +
        scaled1 * get_s8_32(in_pw_row2, 1) +
        scaled2 * get_s8_32(in_pw_row2, 2) +
        scaled3 * get_s8_32(in_pw_row2, 3);

    pw_sum3 =
        scaled0 * get_s8_32(in_pw_row3, 0) +
        scaled1 * get_s8_32(in_pw_row3, 1) +
        scaled2 * get_s8_32(in_pw_row3, 2) +
        scaled3 * get_s8_32(in_pw_row3, 3);
end

always_ff @(posedge clk or posedge rst) begin
    if (rst) begin
        busy   <= 1'b0;
        out_y0 <= 16'h0000;
        out_y1 <= 16'h0000;
        out_y2 <= 16'h0000;
        out_y3 <= 16'h0000;
    end else if (input_accepted) begin
        out_y0 <= pw_sum0[15:0];
        out_y1 <= pw_sum1[15:0];
        out_y2 <= pw_sum2[15:0];
        out_y3 <= pw_sum3[15:0];
        busy   <= 1'b1;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule