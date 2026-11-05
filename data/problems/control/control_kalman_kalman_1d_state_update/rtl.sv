module control_kalman_kalman_1d_state_update (
    input  logic               clk,
    input  logic               rst,

    input  logic               in_valid,
    output logic               in_ready,
    input  logic signed [31:0] in_x_prior,
    input  logic signed [31:0] in_p_prior,
    input  logic signed [31:0] in_z,
    input  logic signed [15:0] in_h,
    input  logic signed [31:0] in_r,

    output logic               out_valid,
    input  logic               out_ready,
    output logic signed [31:0] out_x_post,
    output logic signed [31:0] out_p_post,
    output logic signed [31:0] out_y,
    output logic signed [31:0] out_s,
    output logic signed [15:0] out_k
);

logic busy;
wire  output_consumed;
wire  input_accepted;

assign output_consumed = out_valid & out_ready;
assign in_ready        = ~busy | output_consumed;
assign out_valid       = busy;

function automatic logic signed [31:0] sat32(input logic signed [63:0] v);
    begin
        if (v > 64'sd2147483647)
            sat32 = 32'sd2147483647;
        else if (v < -64'sd2147483648)
            sat32 = -32'sd2147483648;
        else
            sat32 = v[31:0];
    end
endfunction

function automatic logic signed [15:0] sat16(input logic signed [31:0] v);
    begin
        if (v > 32'sd32767)
            sat16 = 16'sd32767;
        else if (v < -32'sd32768)
            sat16 = -16'sd32768;
        else
            sat16 = v[15:0];
    end
endfunction

function automatic logic signed [63:0] trunc_div_pow2_toward_zero_64(
    input logic signed [63:0] v,
    input int unsigned        s
);
    logic signed [63:0] neg_v;
    begin
        if (v >= 0)
            trunc_div_pow2_toward_zero_64 = v >>> s;
        else begin
            neg_v = -v;
            trunc_div_pow2_toward_zero_64 = -(neg_v >>> s);
        end
    end
endfunction

function automatic logic signed [63:0] trunc_div_toward_zero_64(
    input logic signed [63:0] n,
    input logic signed [63:0] d
);
    logic sign_neg;
    logic signed [63:0] abs_n;
    logic signed [63:0] abs_d;
    logic signed [63:0] q;
    begin
        if (d == 0) begin
            trunc_div_toward_zero_64 = 64'sd0;
        end else begin
            sign_neg = (n < 0) ^ (d < 0);
            if (n < 0)
                abs_n = -n;
            else
                abs_n = n;
            if (d < 0)
                abs_d = -d;
            else
                abs_d = d;
            q = abs_n / abs_d;
            if (sign_neg)
                trunc_div_toward_zero_64 = -q;
            else
                trunc_div_toward_zero_64 = q;
        end
    end
endfunction

function automatic logic signed [31:0] mul_q31_q15(
    input logic signed [31:0] a_q31,
    input logic signed [15:0] b_q15
);
    logic signed [47:0] prod48;
    logic signed [63:0] prod64;
    logic signed [63:0] q64;
    begin
        prod48 = a_q31 * b_q15;
        prod64 = {{16{prod48[47]}}, prod48};
        q64    = trunc_div_pow2_toward_zero_64(prod64, 15);
        mul_q31_q15 = sat32(q64);
    end
endfunction

function automatic logic signed [15:0] mul_q15_q15(
    input logic signed [15:0] a_q15,
    input logic signed [15:0] b_q15
);
    logic signed [31:0] prod32;
    logic signed [63:0] prod64;
    logic signed [63:0] q64;
    logic signed [31:0] q32;
    begin
        prod32 = a_q15 * b_q15;
        prod64 = {{32{prod32[31]}}, prod32};
        q64    = trunc_div_pow2_toward_zero_64(prod64, 15);
        q32    = q64[31:0];
        mul_q15_q15 = sat16(q32);
    end
endfunction

function automatic logic signed [15:0] div_q31_to_q15(
    input logic signed [31:0] num_q31,
    input logic signed [31:0] den_q31
);
    logic signed [63:0] num_shifted;
    logic signed [63:0] q64;
    logic signed [31:0] q32;
    begin
        if (den_q31 == 0) begin
            if (num_q31 >= 0)
                div_q31_to_q15 = 16'sd32767;
            else
                div_q31_to_q15 = -16'sd32768;
        end else begin
            num_shifted = { {17{num_q31[31]}}, num_q31, 15'b0 };
            q64 = trunc_div_toward_zero_64(num_shifted, {{32{den_q31[31]}}, den_q31});
            if (q64 > 64'sd32767) div_q31_to_q15 = 16'sd32767;
            else if (q64 < -64'sd32768) div_q31_to_q15 = -16'sd32768;
            else div_q31_to_q15 = q64[15:0];
        end
    end
endfunction

logic signed [31:0] hx_q31_c;
logic signed [31:0] y_q31_c;
logic signed [31:0] ph_q31_c;
logic signed [31:0] hph_q31_c;
logic signed [31:0] s_q31_c;
logic signed [15:0] k_q15_c;
logic signed [31:0] ky_q31_c;
logic signed [31:0] x_post_q31_c;
logic signed [15:0] kh_q15_c;
logic signed [31:0] i_minus_kh_q15_c;
logic signed [31:0] i_minus_kh_sq_q15_c;
logic signed [31:0] term1_q31_c;
logic signed [31:0] k_sq_q15_c;
logic signed [31:0] term2_q31_c;
logic signed [31:0] p_post_q31_c;
logic signed [63:0] add64_tmp;

always_comb begin
    hx_q31_c            = mul_q31_q15(in_x_prior, in_h);

    add64_tmp           = {{32{in_z[31]}}, in_z} - {{32{hx_q31_c[31]}}, hx_q31_c};
    y_q31_c             = sat32(add64_tmp);

    ph_q31_c            = mul_q31_q15(in_p_prior, in_h);
    hph_q31_c           = mul_q31_q15(ph_q31_c, in_h);

    add64_tmp           = {{32{hph_q31_c[31]}}, hph_q31_c} + {{32{in_r[31]}}, in_r};
    s_q31_c             = sat32(add64_tmp);

    k_q15_c             = div_q31_to_q15(ph_q31_c, s_q31_c);

    ky_q31_c            = mul_q31_q15(y_q31_c, k_q15_c);

    add64_tmp           = {{32{in_x_prior[31]}}, in_x_prior} + {{32{ky_q31_c[31]}}, ky_q31_c};
    x_post_q31_c        = sat32(add64_tmp);

    kh_q15_c            = mul_q15_q15(k_q15_c, in_h);
    i_minus_kh_q15_c    = 32'sd32768 - {{16{kh_q15_c[15]}}, kh_q15_c};
    add64_tmp = $signed(i_minus_kh_q15_c) * $signed(i_minus_kh_q15_c);
    i_minus_kh_sq_q15_c = add64_tmp >>> 15;
    add64_tmp = $signed(in_p_prior) * $signed(i_minus_kh_sq_q15_c);
    term1_q31_c = sat32(trunc_div_pow2_toward_zero_64(add64_tmp, 15));
    add64_tmp = $signed(k_q15_c) * $signed(k_q15_c);
    k_sq_q15_c = add64_tmp >>> 15;
    add64_tmp = $signed(in_r) * $signed(k_sq_q15_c);
    term2_q31_c = sat32(trunc_div_pow2_toward_zero_64(add64_tmp, 15));

    add64_tmp           = {{32{term1_q31_c[31]}}, term1_q31_c} + {{32{term2_q31_c[31]}}, term2_q31_c};
    p_post_q31_c        = sat32(add64_tmp);
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy       <= 1'b0;
        out_x_post <= 32'sd0;
        out_p_post <= 32'sd0;
        out_y      <= 32'sd0;
        out_s      <= 32'sd0;
        out_k      <= 16'sd0;
    end else if (input_accepted) begin
        busy       <= 1'b1;
        out_x_post <= x_post_q31_c;
        out_p_post <= p_post_q31_c;
        out_y      <= y_q31_c;
        out_s      <= s_q31_c;
        out_k      <= k_q15_c;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

assign input_accepted = in_valid & in_ready;

endmodule
