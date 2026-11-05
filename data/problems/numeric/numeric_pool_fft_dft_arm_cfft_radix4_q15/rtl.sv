module radix4_q15_butterfly_kernel (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic         in_ifft,
    input  logic [15:0]  in_xa_re,
    input  logic [15:0]  in_xa_im,
    input  logic [15:0]  in_xb_re,
    input  logic [15:0]  in_xb_im,
    input  logic [15:0]  in_xc_re,
    input  logic [15:0]  in_xc_im,
    input  logic [15:0]  in_xd_re,
    input  logic [15:0]  in_xd_im,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_x0_re,
    output logic [15:0]  out_x0_im,
    output logic [15:0]  out_x1_re,
    output logic [15:0]  out_x1_im,
    output logic [15:0]  out_x2_re,
    output logic [15:0]  out_x2_im,
    output logic [15:0]  out_x3_re,
    output logic [15:0]  out_x3_im
);

logic busy;

wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

function automatic logic signed [16:0] sat16(input logic signed [16:0] value);
    if (value > 17'sd32767) sat16 = 17'sd32767;
    else if (value < -17'sd32768) sat16 = -17'sd32768;
    else sat16 = value;
endfunction

always_ff @(posedge clk) begin
    logic signed [15:0] xa;
    logic signed [15:0] ya;
    logic signed [15:0] xb;
    logic signed [15:0] yb;
    logic signed [15:0] xc;
    logic signed [15:0] yc;
    logic signed [15:0] xd;
    logic signed [15:0] yd;

    logic signed [16:0] r0_re;
    logic signed [16:0] r0_im;
    logic signed [16:0] s0_re;
    logic signed [16:0] s0_im;
    logic signed [16:0] t0_re;
    logic signed [16:0] t0_im;
    logic signed [16:0] u0_re;
    logic signed [16:0] u0_im;

    logic signed [16:0] r0_re_s;
    logic signed [16:0] r0_im_s;
    logic signed [16:0] s0_re_s;
    logic signed [16:0] s0_im_s;
    logic signed [16:0] t0_re_s;
    logic signed [16:0] t0_im_s;
    logic signed [16:0] u0_re_s;
    logic signed [16:0] u0_im_s;

    logic signed [17:0] x0_re_v;
    logic signed [17:0] x0_im_v;
    logic signed [17:0] x1_re_v;
    logic signed [17:0] x1_im_v;
    logic signed [17:0] x2_re_v;
    logic signed [17:0] x2_im_v;
    logic signed [17:0] x3_re_v;
    logic signed [17:0] x3_im_v;

    if (rst) begin
        busy      <= 1'b0;
        out_x0_re <= 16'h0000;
        out_x0_im <= 16'h0000;
        out_x1_re <= 16'h0000;
        out_x1_im <= 16'h0000;
        out_x2_re <= 16'h0000;
        out_x2_im <= 16'h0000;
        out_x3_re <= 16'h0000;
        out_x3_im <= 16'h0000;
    end else if (input_accepted) begin
        xa = $signed(in_xa_re);
        ya = $signed(in_xa_im);
        xb = $signed(in_xb_re);
        yb = $signed(in_xb_im);
        xc = $signed(in_xc_re);
        yc = $signed(in_xc_im);
        xd = $signed(in_xd_re);
        yd = $signed(in_xd_im);

        r0_re = sat16(17'(xa) + 17'(xc));
        r0_im = sat16(17'(ya) + 17'(yc));
        s0_re = sat16(17'(xa) - 17'(xc));
        s0_im = sat16(17'(ya) - 17'(yc));
        t0_re = sat16(17'(xb) + 17'(xd));
        t0_im = sat16(17'(yb) + 17'(yd));
        u0_re = sat16(17'(xb) - 17'(xd));
        u0_im = sat16(17'(yb) - 17'(yd));

        r0_re_s = r0_re >>> 1;
        r0_im_s = r0_im >>> 1;
        s0_re_s = s0_re >>> 1;
        s0_im_s = s0_im >>> 1;
        t0_re_s = t0_re >>> 1;
        t0_im_s = t0_im >>> 1;
        u0_re_s = u0_re >>> 1;
        u0_im_s = u0_im >>> 1;

        x0_re_v = r0_re_s + t0_re_s;
        x0_im_v = r0_im_s + t0_im_s;
        x2_re_v = r0_re_s - t0_re_s;
        x2_im_v = r0_im_s - t0_im_s;

        if (in_ifft) begin
            x1_re_v = s0_re_s - u0_im_s;
            x1_im_v = s0_im_s + u0_re_s;
            x3_re_v = s0_re_s + u0_im_s;
            x3_im_v = s0_im_s - u0_re_s;
        end else begin
            x1_re_v = s0_re_s + u0_im_s;
            x1_im_v = s0_im_s - u0_re_s;
            x3_re_v = s0_re_s - u0_im_s;
            x3_im_v = s0_im_s + u0_re_s;
        end

        out_x0_re <= x0_re_v[15:0];
        out_x0_im <= x0_im_v[15:0];
        out_x1_re <= x1_re_v[15:0];
        out_x1_im <= x1_im_v[15:0];
        out_x2_re <= x2_re_v[15:0];
        out_x2_im <= x2_im_v[15:0];
        out_x3_re <= x3_re_v[15:0];
        out_x3_im <= x3_im_v[15:0];
        busy      <= 1'b1;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule
