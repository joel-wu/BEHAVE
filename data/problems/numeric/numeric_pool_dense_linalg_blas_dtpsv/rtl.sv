module PackedTriangularSolve4 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic         in_uplo,
    input  logic         in_trans,
    input  logic         in_diag,
    input  logic [63:0]  in_ap0,
    input  logic [63:0]  in_ap1,
    input  logic [63:0]  in_ap2,
    input  logic [63:0]  in_ap3,
    input  logic [63:0]  in_ap4,
    input  logic [63:0]  in_ap5,
    input  logic [63:0]  in_ap6,
    input  logic [63:0]  in_ap7,
    input  logic [63:0]  in_ap8,
    input  logic [63:0]  in_ap9,
    input  logic [63:0]  in_x0,
    input  logic [63:0]  in_x1,
    input  logic [63:0]  in_x2,
    input  logic [63:0]  in_x3,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [63:0]  out_x0,
    output logic [63:0]  out_x1,
    output logic [63:0]  out_x2,
    output logic [63:0]  out_x3
);

logic busy;
wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;
assign in_ready        = ~busy | output_consumed;
assign out_valid       = busy;

function automatic logic signed [63:0] q32_32_sub(
    input logic signed [63:0] a,
    input logic signed [63:0] b
);
    logic signed [64:0] tmp;
    begin
        tmp = a - b;
        q32_32_sub = tmp[63:0];
    end
endfunction

function automatic logic signed [63:0] q32_32_mul(
    input logic signed [63:0] a,
    input logic signed [63:0] b
);
    logic signed [127:0] prod;
    logic signed [127:0] shifted;
    begin
        prod = a * b;
        shifted = prod >>> 32;
        q32_32_mul = shifted[63:0];
    end
endfunction

function automatic logic [127:0] udiv128(
    input logic [127:0] n,
    input logic [63:0]  d
);
    logic [127:0] q;
    logic [63:0]  r;
    integer i;
    begin
        q = 128'd0;
        r = 64'd0;
        for (i = 127; i >= 0; i = i - 1) begin
            r = {r[62:0], n[i]};
            if (r >= d) begin
                r = r - d;
                q[i] = 1'b1;
            end
        end
        udiv128 = q;
    end
endfunction

function automatic logic signed [63:0] q32_32_div(
    input logic signed [63:0] a,
    input logic signed [63:0] b
);
    logic signed [63:0] den_s;
    logic signed [63:0] num_s;
    logic [63:0] abs_den;
    logic [63:0] abs_num64;
    logic [127:0] num128;
    logic [127:0] q_unsigned;
    logic sign_diff;
    logic signed [127:0] q_signed128;
    begin
        den_s = b;
        num_s = a;
        q32_32_div = 64'sd0;
        if (den_s == 64'sd0) begin
            q32_32_div = 64'sd0;
        end else begin
            if (den_s < 0)
                abs_den = -den_s;
            else
                abs_den = den_s;

            if (num_s < 0)
                abs_num64 = -num_s;
            else
                abs_num64 = num_s;

            num128 = {abs_num64, 32'd0};
            q_unsigned = udiv128(num128, abs_den);
            sign_diff = num_s[63] ^ den_s[63];

            if (sign_diff)
                q_signed128 = -$signed(q_unsigned);
            else
                q_signed128 = $signed(q_unsigned);

            q32_32_div = q_signed128[63:0];
        end
    end
endfunction

logic signed [63:0] a00, a01, a02, a03;
logic signed [63:0] a10, a11, a12, a13;
logic signed [63:0] a20, a21, a22, a23;
logic signed [63:0] a30, a31, a32, a33;
logic signed [63:0] b0, b1, b2, b3;

logic signed [63:0] x0_pre, x1_pre, x2_pre, x3_pre;
logic signed [63:0] x0_calc, x1_calc, x2_calc, x3_calc;

always_comb begin
    a00 = 64'sd0; a01 = 64'sd0; a02 = 64'sd0; a03 = 64'sd0;
    a10 = 64'sd0; a11 = 64'sd0; a12 = 64'sd0; a13 = 64'sd0;
    a20 = 64'sd0; a21 = 64'sd0; a22 = 64'sd0; a23 = 64'sd0;
    a30 = 64'sd0; a31 = 64'sd0; a32 = 64'sd0; a33 = 64'sd0;

    if (in_uplo) begin
        a00 = $signed(in_ap0);
        a01 = $signed(in_ap1);
        a11 = $signed(in_ap2);
        a02 = $signed(in_ap3);
        a12 = $signed(in_ap4);
        a22 = $signed(in_ap5);
        a03 = $signed(in_ap6);
        a13 = $signed(in_ap7);
        a23 = $signed(in_ap8);
        a33 = $signed(in_ap9);
    end else begin
        a00 = $signed(in_ap0);
        a10 = $signed(in_ap1);
        a20 = $signed(in_ap2);
        a30 = $signed(in_ap3);
        a11 = $signed(in_ap4);
        a21 = $signed(in_ap5);
        a31 = $signed(in_ap6);
        a22 = $signed(in_ap7);
        a32 = $signed(in_ap8);
        a33 = $signed(in_ap9);
    end

    b0 = $signed(in_x0);
    b1 = $signed(in_x1);
    b2 = $signed(in_x2);
    b3 = $signed(in_x3);

    x0_pre = 64'sd0;
    x1_pre = 64'sd0;
    x2_pre = 64'sd0;
    x3_pre = 64'sd0;
    x0_calc = 64'sd0;
    x1_calc = 64'sd0;
    x2_calc = 64'sd0;
    x3_calc = 64'sd0;

    if (!in_trans) begin
        if (in_uplo) begin
            x3_pre  = b3;
            x3_calc = in_diag ? x3_pre : q32_32_div(x3_pre, a33);

            x2_pre  = q32_32_sub(b2, q32_32_mul(x3_calc, a23));
            x2_calc = in_diag ? x2_pre : q32_32_div(x2_pre, a22);

            x1_pre  = q32_32_sub(q32_32_sub(b1, q32_32_mul(x3_calc, a13)), q32_32_mul(x2_calc, a12));
            x1_calc = in_diag ? x1_pre : q32_32_div(x1_pre, a11);

            x0_pre  = q32_32_sub(q32_32_sub(q32_32_sub(b0, q32_32_mul(x3_calc, a03)), q32_32_mul(x2_calc, a02)), q32_32_mul(x1_calc, a01));
            x0_calc = in_diag ? x0_pre : q32_32_div(x0_pre, a00);
        end else begin
            x0_pre  = b0;
            x0_calc = in_diag ? x0_pre : q32_32_div(x0_pre, a00);

            x1_pre  = q32_32_sub(b1, q32_32_mul(x0_calc, a10));
            x1_calc = in_diag ? x1_pre : q32_32_div(x1_pre, a11);

            x2_pre  = q32_32_sub(q32_32_sub(b2, q32_32_mul(x0_calc, a20)), q32_32_mul(x1_calc, a21));
            x2_calc = in_diag ? x2_pre : q32_32_div(x2_pre, a22);

            x3_pre  = q32_32_sub(q32_32_sub(q32_32_sub(b3, q32_32_mul(x0_calc, a30)), q32_32_mul(x1_calc, a31)), q32_32_mul(x2_calc, a32));
            x3_calc = in_diag ? x3_pre : q32_32_div(x3_pre, a33);
        end
    end else begin
        if (in_uplo) begin
            x0_pre  = b0;
            x0_calc = in_diag ? x0_pre : q32_32_div(x0_pre, a00);

            x1_pre  = q32_32_sub(b1, q32_32_mul(a01, x0_calc));
            x1_calc = in_diag ? x1_pre : q32_32_div(x1_pre, a11);

            x2_pre  = q32_32_sub(q32_32_sub(b2, q32_32_mul(a02, x0_calc)), q32_32_mul(a12, x1_calc));
            x2_calc = in_diag ? x2_pre : q32_32_div(x2_pre, a22);

            x3_pre  = q32_32_sub(q32_32_sub(q32_32_sub(b3, q32_32_mul(a03, x0_calc)), q32_32_mul(a13, x1_calc)), q32_32_mul(a23, x2_calc));
            x3_calc = in_diag ? x3_pre : q32_32_div(x3_pre, a33);
        end else begin
            x3_pre  = b3;
            x3_calc = in_diag ? x3_pre : q32_32_div(x3_pre, a33);

            x2_pre  = q32_32_sub(b2, q32_32_mul(a32, x3_calc));
            x2_calc = in_diag ? x2_pre : q32_32_div(x2_pre, a22);

            x1_pre  = q32_32_sub(q32_32_sub(b1, q32_32_mul(a31, x3_calc)), q32_32_mul(a21, x2_calc));
            x1_calc = in_diag ? x1_pre : q32_32_div(x1_pre, a11);

            x0_pre  = q32_32_sub(q32_32_sub(q32_32_sub(b0, q32_32_mul(a30, x3_calc)), q32_32_mul(a20, x2_calc)), q32_32_mul(a10, x1_calc));
            x0_calc = in_diag ? x0_pre : q32_32_div(x0_pre, a00);
        end
    end
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy   <= 1'b0;
        out_x0 <= 64'h0000000000000000;
        out_x1 <= 64'h0000000000000000;
        out_x2 <= 64'h0000000000000000;
        out_x3 <= 64'h0000000000000000;
    end else if (input_accepted) begin
        busy   <= 1'b1;
        out_x0 <= x0_calc[63:0];
        out_x1 <= x1_calc[63:0];
        out_x2 <= x2_calc[63:0];
        out_x3 <= x3_calc[63:0];
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule