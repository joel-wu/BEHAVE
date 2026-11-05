module radix2_q15_butterfly8 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic        in_ifft,
    input  logic [15:0] in_x0_re,
    input  logic [15:0] in_x0_im,
    input  logic [15:0] in_x1_re,
    input  logic [15:0] in_x1_im,
    input  logic [15:0] in_x2_re,
    input  logic [15:0] in_x2_im,
    input  logic [15:0] in_x3_re,
    input  logic [15:0] in_x3_im,
    input  logic [15:0] in_x4_re,
    input  logic [15:0] in_x4_im,
    input  logic [15:0] in_x5_re,
    input  logic [15:0] in_x5_im,
    input  logic [15:0] in_x6_re,
    input  logic [15:0] in_x6_im,
    input  logic [15:0] in_x7_re,
    input  logic [15:0] in_x7_im,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [15:0] out_x0_re,
    output logic [15:0] out_x0_im,
    output logic [15:0] out_x1_re,
    output logic [15:0] out_x1_im,
    output logic [15:0] out_x2_re,
    output logic [15:0] out_x2_im,
    output logic [15:0] out_x3_re,
    output logic [15:0] out_x3_im,
    output logic [15:0] out_x4_re,
    output logic [15:0] out_x4_im,
    output logic [15:0] out_x5_re,
    output logic [15:0] out_x5_im,
    output logic [15:0] out_x6_re,
    output logic [15:0] out_x6_im,
    output logic [15:0] out_x7_re,
    output logic [15:0] out_x7_im
);

logic busy;
wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

function automatic logic signed [15:0] wrap16(input logic signed [31:0] v);
    wrap16 = v[15:0];
endfunction

function automatic logic signed [15:0] sat16_addsub(
    input logic signed [15:0] a,
    input logic signed [15:0] b,
    input logic               sub
);
    logic signed [15:0] b_eff;
    logic signed [15:0] sum16;
    begin
        b_eff = sub ? -b : b;
        sum16 = a + b_eff;
        if ((a[15] == b_eff[15]) && (sum16[15] != a[15])) begin
            sat16_addsub = a[15] ? -16'sd32768 : 16'sd32767;
        end else begin
            sat16_addsub = sum16;
        end
    end
endfunction

function automatic logic signed [15:0] ashr1_16(input logic signed [15:0] v);
    ashr1_16 = v >>> 1;
endfunction

function automatic logic signed [15:0] mul16_hi(
    input logic signed [15:0] a,
    input logic signed [15:0] b
);
    logic signed [31:0] prod;
    logic signed [31:0] sh;
    begin
        prod = a * b;
        sh   = prod >>> 16;
        mul16_hi = sh[15:0];
    end
endfunction

function automatic logic signed [15:0] low_re_func(
    input logic        ifft,
    input logic signed [15:0] xt,
    input logic signed [15:0] yt,
    input logic signed [15:0] cos,
    input logic signed [15:0] sin
);
    logic signed [15:0] m0;
    logic signed [15:0] m1;
    logic signed [31:0] tmp;
    begin
        m0 = mul16_hi(xt, cos);
        m1 = mul16_hi(yt, sin);
        if (ifft) begin
            tmp = m0 - m1;
        end else begin
            tmp = m0 + m1;
        end
        low_re_func = wrap16(tmp);
    end
endfunction

function automatic logic signed [15:0] low_im_func(
    input logic        ifft,
    input logic signed [15:0] xt,
    input logic signed [15:0] yt,
    input logic signed [15:0] cos,
    input logic signed [15:0] sin
);
    logic signed [15:0] m2;
    logic signed [15:0] m3;
    logic signed [31:0] tmp;
    begin
        m2 = mul16_hi(yt, cos);
        m3 = mul16_hi(xt, sin);
        if (ifft) begin
            tmp = m2 + m3;
        end else begin
            tmp = m2 - m3;
        end
        low_im_func = wrap16(tmp);
    end
endfunction

always_ff @(posedge clk) begin
    logic signed [15:0] x0r, x0i, x1r, x1i, x2r, x2i, x3r, x3i;
    logic signed [15:0] x4r, x4i, x5r, x5i, x6r, x6i, x7r, x7i;

    logic signed [15:0] s0r, s0i, s1r, s1i, s2r, s2i, s3r, s3i;
    logic signed [15:0] s4r, s4i, s5r, s5i, s6r, s6i, s7r, s7i;

    logic signed [15:0] y0r, y0i, y1r, y1i, y2r, y2i, y3r, y3i;
    logic signed [15:0] y4r, y4i, y5r, y5i, y6r, y6i, y7r, y7i;

    logic signed [15:0] z0r, z0i, z1r, z1i, z2r, z2i, z3r, z3i;
    logic signed [15:0] z4r, z4i, z5r, z5i, z6r, z6i, z7r, z7i;

    logic signed [15:0] w0r, w0i, w1r, w1i, w2r, w2i, w3r, w3i;
    logic signed [15:0] w4r, w4i, w5r, w5i, w6r, w6i, w7r, w7i;

    logic signed [15:0] xt, yt;

    if (rst) begin
        busy <= 1'b0;
        out_x0_re <= 16'd0;
        out_x0_im <= 16'd0;
        out_x1_re <= 16'd0;
        out_x1_im <= 16'd0;
        out_x2_re <= 16'd0;
        out_x2_im <= 16'd0;
        out_x3_re <= 16'd0;
        out_x3_im <= 16'd0;
        out_x4_re <= 16'd0;
        out_x4_im <= 16'd0;
        out_x5_re <= 16'd0;
        out_x5_im <= 16'd0;
        out_x6_re <= 16'd0;
        out_x6_im <= 16'd0;
        out_x7_re <= 16'd0;
        out_x7_im <= 16'd0;
    end else if (input_accepted) begin
        x0r = $signed(in_x0_re);
        x0i = $signed(in_x0_im);
        x1r = $signed(in_x1_re);
        x1i = $signed(in_x1_im);
        x2r = $signed(in_x2_re);
        x2i = $signed(in_x2_im);
        x3r = $signed(in_x3_re);
        x3i = $signed(in_x3_im);
        x4r = $signed(in_x4_re);
        x4i = $signed(in_x4_im);
        x5r = $signed(in_x5_re);
        x5i = $signed(in_x5_im);
        x6r = $signed(in_x6_re);
        x6i = $signed(in_x6_im);
        x7r = $signed(in_x7_re);
        x7i = $signed(in_x7_im);

        s0r = ashr1_16(x0r); s0i = ashr1_16(x0i);
        s1r = ashr1_16(x1r); s1i = ashr1_16(x1i);
        s2r = ashr1_16(x2r); s2i = ashr1_16(x2i);
        s3r = ashr1_16(x3r); s3i = ashr1_16(x3i);
        s4r = ashr1_16(x4r); s4i = ashr1_16(x4i);
        s5r = ashr1_16(x5r); s5i = ashr1_16(x5i);
        s6r = ashr1_16(x6r); s6i = ashr1_16(x6i);
        s7r = ashr1_16(x7r); s7i = ashr1_16(x7i);

        y0r = wrap16((s0r + s4r) >>> 1);
        y0i = wrap16((s0i + s4i) >>> 1);
        xt = wrap16(s0r - s4r);
        yt = wrap16(s0i - s4i);
        y4r = low_re_func(in_ifft, xt, yt, 16'sd32767, 16'sd0);
        y4i = low_im_func(in_ifft, xt, yt, 16'sd32767, 16'sd0);

        y1r = wrap16((s1r + s5r) >>> 1);
        y1i = wrap16((s1i + s5i) >>> 1);
        xt = wrap16(s1r - s5r);
        yt = wrap16(s1i - s5i);
        y5r = low_re_func(in_ifft, xt, yt, 16'sd23169, 16'sd23169);
        y5i = low_im_func(in_ifft, xt, yt, 16'sd23169, 16'sd23169);

        y2r = wrap16((s2r + s6r) >>> 1);
        y2i = wrap16((s2i + s6i) >>> 1);
        xt = wrap16(s2r - s6r);
        yt = wrap16(s2i - s6i);
        y6r = low_re_func(in_ifft, xt, yt, 16'sd0, 16'sd32767);
        y6i = low_im_func(in_ifft, xt, yt, 16'sd0, 16'sd32767);

        y3r = wrap16((s3r + s7r) >>> 1);
        y3i = wrap16((s3i + s7i) >>> 1);
        xt = wrap16(s3r - s7r);
        yt = wrap16(s3i - s7i);
        y7r = low_re_func(in_ifft, xt, yt, -16'sd23169, 16'sd23169);
        y7i = low_im_func(in_ifft, xt, yt, -16'sd23169, 16'sd23169);

        z0r = wrap16((y0r + y2r) >>> 1);
        z0i = wrap16((y0i + y2i) >>> 1);
        xt = wrap16(y0r - y2r);
        yt = wrap16(y0i - y2i);
        z2r = low_re_func(in_ifft, xt, yt, 16'sd32767, 16'sd0);
        z2i = low_im_func(in_ifft, xt, yt, 16'sd32767, 16'sd0);

        z4r = wrap16((y4r + y6r) >>> 1);
        z4i = wrap16((y4i + y6i) >>> 1);
        xt = wrap16(y4r - y6r);
        yt = wrap16(y4i - y6i);
        z6r = low_re_func(in_ifft, xt, yt, 16'sd32767, 16'sd0);
        z6i = low_im_func(in_ifft, xt, yt, 16'sd32767, 16'sd0);

        z1r = wrap16((y1r + y3r) >>> 1);
        z1i = wrap16((y1i + y3i) >>> 1);
        xt = wrap16(y1r - y3r);
        yt = wrap16(y1i - y3i);
        z3r = low_re_func(in_ifft, xt, yt, 16'sd0, 16'sd32767);
        z3i = low_im_func(in_ifft, xt, yt, 16'sd0, 16'sd32767);

        z5r = wrap16((y5r + y7r) >>> 1);
        z5i = wrap16((y5i + y7i) >>> 1);
        xt = wrap16(y5r - y7r);
        yt = wrap16(y5i - y7i);
        z7r = low_re_func(in_ifft, xt, yt, 16'sd0, 16'sd32767);
        z7i = low_im_func(in_ifft, xt, yt, 16'sd0, 16'sd32767);

        w0r = sat16_addsub(z0r, z1r, 1'b0);
        w0i = sat16_addsub(z0i, z1i, 1'b0);
        w1r = sat16_addsub(z0r, z1r, 1'b1);
        w1i = sat16_addsub(z0i, z1i, 1'b1);

        w2r = sat16_addsub(z2r, z3r, 1'b0);
        w2i = sat16_addsub(z2i, z3i, 1'b0);
        w3r = sat16_addsub(z2r, z3r, 1'b1);
        w3i = sat16_addsub(z2i, z3i, 1'b1);

        w4r = sat16_addsub(z4r, z5r, 1'b0);
        w4i = sat16_addsub(z4i, z5i, 1'b0);
        w5r = sat16_addsub(z4r, z5r, 1'b1);
        w5i = sat16_addsub(z4i, z5i, 1'b1);

        w6r = sat16_addsub(z6r, z7r, 1'b0);
        w6i = sat16_addsub(z6i, z7i, 1'b0);
        w7r = sat16_addsub(z6r, z7r, 1'b1);
        w7i = sat16_addsub(z6i, z7i, 1'b1);

        out_x0_re <= w0r[15:0];
        out_x0_im <= w0i[15:0];
        out_x1_re <= w4r[15:0];
        out_x1_im <= w4i[15:0];
        out_x2_re <= w2r[15:0];
        out_x2_im <= w2i[15:0];
        out_x3_re <= w6r[15:0];
        out_x3_im <= w6i[15:0];
        out_x4_re <= w1r[15:0];
        out_x4_im <= w1i[15:0];
        out_x5_re <= w5r[15:0];
        out_x5_im <= w5i[15:0];
        out_x6_re <= w3r[15:0];
        out_x6_im <= w3i[15:0];
        out_x7_re <= w7r[15:0];
        out_x7_im <= w7i[15:0];

        busy <= 1'b1;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule