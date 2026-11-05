module complex_householder_reflector3 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_alpha_re,
    input  logic [15:0] in_alpha_im,
    input  logic [15:0] in_x0_re,
    input  logic [15:0] in_x0_im,
    input  logic [15:0] in_x1_re,
    input  logic [15:0] in_x1_im,
    input  logic [15:0] in_x2_re,
    input  logic [15:0] in_x2_im,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [15:0] out_alpha_re,
    output logic [15:0] out_alpha_im,
    output logic [15:0] out_tau_re,
    output logic [15:0] out_tau_im,
    output logic [15:0] out_x0_re,
    output logic [15:0] out_x0_im,
    output logic [15:0] out_x1_re,
    output logic [15:0] out_x1_im,
    output logic [15:0] out_x2_re,
    output logic [15:0] out_x2_im
);

logic busy;
wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;
assign in_ready        = ~busy | output_consumed;
assign out_valid       = busy;

function automatic logic signed [15:0] sat16(input logic signed [63:0] value);
    begin
        if (value > 64'sd32767) begin
            sat16 = 16'sd32767;
        end else if (value < -64'sd32768) begin
            sat16 = -16'sd32768;
        end else begin
            sat16 = value[15:0];
        end
    end
endfunction

function automatic logic signed [63:0] trunc0_s64(
    input logic signed [63:0] num,
    input logic signed [63:0] den
);
    logic neg;
    logic signed [63:0] anum;
    logic signed [63:0] aden;
    logic signed [63:0] q;
    begin
        trunc0_s64 = 64'sd0;
        if (den == 64'sd0) begin
            trunc0_s64 = 64'sd0;
        end else begin
            neg  = (num < 0) ^ (den < 0);
            anum = (num < 0) ? -num : num;
            aden = (den < 0) ? -den : den;
            q    = anum / aden;
            trunc0_s64 = neg ? -q : q;
        end
    end
endfunction

function automatic logic [31:0] isqrt_u64(input logic [63:0] n);
    logic [31:0] lo;
    logic [31:0] hi;
    logic [31:0] mid;
    logic [63:0] mid_sq;
    integer i;
    begin
        lo = 32'd0;
        hi = 32'hFFFF_FFFF;
        for (i = 0; i < 64; i = i + 1) begin
            mid = (lo + hi) >> 1;
            mid_sq = mid * mid;
            if (mid_sq <= n) begin
                lo = mid;
            end else begin
                hi = mid;
            end
        end
        isqrt_u64 = lo;
    end
endfunction

logic signed [15:0] alphar_s;
logic signed [15:0] alphai_s;
logic signed [15:0] x0r_s;
logic signed [15:0] x0i_s;
logic signed [15:0] x1r_s;
logic signed [15:0] x1i_s;
logic signed [15:0] x2r_s;
logic signed [15:0] x2i_s;

logic signed [63:0] xnorm_sq_c;
logic               identity_c;
logic [63:0]        beta_arg_u_c;
logic [31:0]        beta_mag_u_c;
logic signed [63:0] beta_s_c;
logic signed [63:0] tau_re_s_c;
logic signed [63:0] tau_im_s_c;
logic signed [63:0] alpha_minus_beta_c;
logic signed [63:0] den_s_c;
logic signed [63:0] scale_re_s_c;
logic signed [63:0] scale_im_s_c;
logic signed [63:0] v0_re_s_c;
logic signed [63:0] v0_im_s_c;
logic signed [63:0] v1_re_s_c;
logic signed [63:0] v1_im_s_c;
logic signed [63:0] v2_re_s_c;
logic signed [63:0] v2_im_s_c;

always_comb begin
    alphar_s = $signed(in_alpha_re);
    alphai_s = $signed(in_alpha_im);
    x0r_s    = $signed(in_x0_re);
    x0i_s    = $signed(in_x0_im);
    x1r_s    = $signed(in_x1_re);
    x1i_s    = $signed(in_x1_im);
    x2r_s    = $signed(in_x2_re);
    x2i_s    = $signed(in_x2_im);

    xnorm_sq_c =
        (x0r_s * x0r_s) + (x0i_s * x0i_s) +
        (x1r_s * x1r_s) + (x1i_s * x1i_s) +
        (x2r_s * x2r_s) + (x2i_s * x2i_s);

    identity_c = (xnorm_sq_c == 64'sd0) && (alphai_s == 16'sd0);

    beta_arg_u_c = (alphar_s * alphar_s) + (alphai_s * alphai_s) + xnorm_sq_c;
    beta_mag_u_c = isqrt_u64(beta_arg_u_c);

    if (alphar_s >= 16'sd0) begin
        beta_s_c = -$signed({32'd0, beta_mag_u_c});
    end else begin
        beta_s_c =  $signed({32'd0, beta_mag_u_c});
    end

    tau_re_s_c = trunc0_s64((beta_s_c - alphar_s) * 64'sd8192, beta_s_c);
    tau_im_s_c = trunc0_s64(-alphai_s * 64'sd8192, beta_s_c);

    alpha_minus_beta_c = alphar_s - beta_s_c;
    den_s_c = (alpha_minus_beta_c * alpha_minus_beta_c) + (alphai_s * alphai_s);

    v0_re_s_c = trunc0_s64(((x0r_s * alpha_minus_beta_c) + (x0i_s * alphai_s)) * 64'sd8192, den_s_c);
    v0_im_s_c = trunc0_s64(((x0i_s * alpha_minus_beta_c) - (x0r_s * alphai_s)) * 64'sd8192, den_s_c);
    v1_re_s_c = trunc0_s64(((x1r_s * alpha_minus_beta_c) + (x1i_s * alphai_s)) * 64'sd8192, den_s_c);
    v1_im_s_c = trunc0_s64(((x1i_s * alpha_minus_beta_c) - (x1r_s * alphai_s)) * 64'sd8192, den_s_c);
    v2_re_s_c = trunc0_s64(((x2r_s * alpha_minus_beta_c) + (x2i_s * alphai_s)) * 64'sd8192, den_s_c);
    v2_im_s_c = trunc0_s64(((x2i_s * alpha_minus_beta_c) - (x2r_s * alphai_s)) * 64'sd8192, den_s_c);

end

always_ff @(posedge clk) begin
    if (rst) begin
        busy         <= 1'b0;
        out_alpha_re <= 16'h0000;
        out_alpha_im <= 16'h0000;
        out_tau_re   <= 16'h0000;
        out_tau_im   <= 16'h0000;
        out_x0_re    <= 16'h0000;
        out_x0_im    <= 16'h0000;
        out_x1_re    <= 16'h0000;
        out_x1_im    <= 16'h0000;
        out_x2_re    <= 16'h0000;
        out_x2_im    <= 16'h0000;
    end else begin
        if (input_accepted) begin
            busy <= 1'b1;
            if (identity_c) begin
                out_alpha_re <= in_alpha_re;
                out_alpha_im <= in_alpha_im;
                out_tau_re   <= 16'h0000;
                out_tau_im   <= 16'h0000;
                out_x0_re    <= in_x0_re;
                out_x0_im    <= in_x0_im;
                out_x1_re    <= in_x1_re;
                out_x1_im    <= in_x1_im;
                out_x2_re    <= in_x2_re;
                out_x2_im    <= in_x2_im;
            end else begin
                out_alpha_re <= sat16(beta_s_c);
                out_alpha_im <= 16'h0000;
                out_tau_re   <= sat16(tau_re_s_c);
                out_tau_im   <= sat16(tau_im_s_c);
                out_x0_re    <= sat16(v0_re_s_c);
                out_x0_im    <= sat16(v0_im_s_c);
                out_x1_re    <= sat16(v1_re_s_c);
                out_x1_im    <= sat16(v1_im_s_c);
                out_x2_re    <= sat16(v2_re_s_c);
                out_x2_im    <= sat16(v2_im_s_c);
            end
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end
end

endmodule
