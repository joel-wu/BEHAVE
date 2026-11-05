module block_reflector_triangular_factor4x4 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic signed [15:0] in_v00,
    input  logic signed [15:0] in_v01,
    input  logic signed [15:0] in_v02,
    input  logic signed [15:0] in_v03,
    input  logic signed [15:0] in_v10,
    input  logic signed [15:0] in_v11,
    input  logic signed [15:0] in_v12,
    input  logic signed [15:0] in_v13,
    input  logic signed [15:0] in_v20,
    input  logic signed [15:0] in_v21,
    input  logic signed [15:0] in_v22,
    input  logic signed [15:0] in_v23,
    input  logic signed [15:0] in_v30,
    input  logic signed [15:0] in_v31,
    input  logic signed [15:0] in_v32,
    input  logic signed [15:0] in_v33,
    input  logic signed [15:0] in_tau0,
    input  logic signed [15:0] in_tau1,
    input  logic signed [15:0] in_tau2,
    input  logic signed [15:0] in_tau3,

    output logic         out_valid,
    input  logic         out_ready,
    output logic signed [15:0] out_t00,
    output logic signed [15:0] out_t01,
    output logic signed [15:0] out_t02,
    output logic signed [15:0] out_t03,
    output logic signed [15:0] out_t10,
    output logic signed [15:0] out_t11,
    output logic signed [15:0] out_t12,
    output logic signed [15:0] out_t13,
    output logic signed [15:0] out_t20,
    output logic signed [15:0] out_t21,
    output logic signed [15:0] out_t22,
    output logic signed [15:0] out_t23,
    output logic signed [15:0] out_t30,
    output logic signed [15:0] out_t31,
    output logic signed [15:0] out_t32,
    output logic signed [15:0] out_t33
);

    logic busy;
    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    function automatic logic signed [15:0] trunc16_mul(
        input logic signed [15:0] a,
        input logic signed [15:0] b
    );
        logic signed [31:0] prod;
        begin
            prod = a * b;
            trunc16_mul = prod[15:0];
        end
    endfunction

    function automatic logic signed [15:0] trunc16_add(
        input logic signed [15:0] a,
        input logic signed [15:0] b
    );
        logic signed [16:0] sum;
        begin
            sum = a + b;
            trunc16_add = sum[15:0];
        end
    endfunction

    function automatic logic signed [15:0] trunc16_neg(
        input logic signed [15:0] a
    );
        logic signed [16:0] negv;
        begin
            negv = -a;
            trunc16_neg = negv[15:0];
        end
    endfunction

    logic signed [15:0] t11_00, t11_01, t11_11;
    logic signed [15:0] t22_00, t22_01, t22_11;
    logic signed [15:0] s00, s01, s10, s11;
    logic signed [15:0] r00, r01, r10, r11;
    logic signed [15:0] l00, l01, l10, l11;
    logic signed [15:0] t12_00, t12_01, t12_10, t12_11;
    logic signed [15:0] tmp0, tmp1;

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            busy    <= 1'b0;
            out_t00 <= 16'sd0;
            out_t01 <= 16'sd0;
            out_t02 <= 16'sd0;
            out_t03 <= 16'sd0;
            out_t10 <= 16'sd0;
            out_t11 <= 16'sd0;
            out_t12 <= 16'sd0;
            out_t13 <= 16'sd0;
            out_t20 <= 16'sd0;
            out_t21 <= 16'sd0;
            out_t22 <= 16'sd0;
            out_t23 <= 16'sd0;
            out_t30 <= 16'sd0;
            out_t31 <= 16'sd0;
            out_t32 <= 16'sd0;
            out_t33 <= 16'sd0;
        end else if (input_accepted) begin
            t11_00 = in_tau0;
            t11_11 = in_tau1;
            tmp0 = trunc16_add(in_v10, trunc16_add(trunc16_mul(in_v20, in_v21), trunc16_mul(in_v30, in_v31)));
            tmp0 = trunc16_mul(trunc16_neg(in_tau0), tmp0);
            t11_01 = trunc16_mul(tmp0, in_tau1);

            t22_00 = in_tau2;
            t22_11 = in_tau3;
            tmp0   = trunc16_mul(trunc16_neg(in_tau2), in_v32);
            t22_01 = trunc16_mul(tmp0, in_tau3);

            s00 = in_v20;
            s01 = in_v30;
            s10 = in_v21;
            s11 = in_v31;

            tmp0 = trunc16_mul(s01, in_v32);
            r00  = trunc16_add(s00, tmp0);
            r01  = s01;
            tmp0 = trunc16_mul(s11, in_v32);
            r10  = trunc16_add(s10, tmp0);
            r11  = s11;

            tmp0 = trunc16_mul(t11_00, r00);
            tmp1 = trunc16_mul(t11_01, r10);
            l00  = trunc16_neg(trunc16_add(tmp0, tmp1));

            tmp0 = trunc16_mul(t11_00, r01);
            tmp1 = trunc16_mul(t11_01, r11);
            l01  = trunc16_neg(trunc16_add(tmp0, tmp1));

            tmp0 = trunc16_mul(t11_11, r10);
            l10  = trunc16_neg(tmp0);

            tmp0 = trunc16_mul(t11_11, r11);
            l11  = trunc16_neg(tmp0);

            t12_00 = trunc16_mul(l00, t22_00);
            t12_10 = trunc16_mul(l10, t22_00);

            tmp0   = trunc16_mul(l00, t22_01);
            tmp1   = trunc16_mul(l01, t22_11);
            t12_01 = trunc16_add(tmp0, tmp1);

            tmp0   = trunc16_mul(l10, t22_01);
            tmp1   = trunc16_mul(l11, t22_11);
            t12_11 = trunc16_add(tmp0, tmp1);

            out_t00 <= t11_00;
            out_t01 <= t11_01;
            out_t02 <= t12_00;
            out_t03 <= t12_01;
            out_t10 <= 16'sd0;
            out_t11 <= t11_11;
            out_t12 <= t12_10;
            out_t13 <= t12_11;
            out_t20 <= 16'sd0;
            out_t21 <= 16'sd0;
            out_t22 <= t22_00;
            out_t23 <= t22_01;
            out_t30 <= 16'sd0;
            out_t31 <= 16'sd0;
            out_t32 <= 16'sd0;
            out_t33 <= t22_11;
            busy    <= 1'b1;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
