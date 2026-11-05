module hermitian_matrix_multiply_tile (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic         in_side,
    input  logic         in_uplo,

    input  logic signed [31:0] in_alpha_re,
    input  logic signed [31:0] in_alpha_im,
    input  logic signed [31:0] in_beta_re,
    input  logic signed [31:0] in_beta_im,

    input  logic signed [31:0] in_a00_re,
    input  logic signed [31:0] in_a00_im,
    input  logic signed [31:0] in_a01_re,
    input  logic signed [31:0] in_a01_im,
    input  logic signed [31:0] in_a10_re,
    input  logic signed [31:0] in_a10_im,
    input  logic signed [31:0] in_a11_re,
    input  logic signed [31:0] in_a11_im,

    input  logic signed [31:0] in_b00_re,
    input  logic signed [31:0] in_b00_im,
    input  logic signed [31:0] in_b01_re,
    input  logic signed [31:0] in_b01_im,
    input  logic signed [31:0] in_b10_re,
    input  logic signed [31:0] in_b10_im,
    input  logic signed [31:0] in_b11_re,
    input  logic signed [31:0] in_b11_im,

    input  logic signed [31:0] in_c00_re,
    input  logic signed [31:0] in_c00_im,
    input  logic signed [31:0] in_c01_re,
    input  logic signed [31:0] in_c01_im,
    input  logic signed [31:0] in_c10_re,
    input  logic signed [31:0] in_c10_im,
    input  logic signed [31:0] in_c11_re,
    input  logic signed [31:0] in_c11_im,

    output logic         out_valid,
    input  logic         out_ready,
    output logic signed [31:0] out_c00_re,
    output logic signed [31:0] out_c00_im,
    output logic signed [31:0] out_c01_re,
    output logic signed [31:0] out_c01_im,
    output logic signed [31:0] out_c10_re,
    output logic signed [31:0] out_c10_im,
    output logic signed [31:0] out_c11_re,
    output logic signed [31:0] out_c11_im
);

logic busy;
wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

function automatic signed [63:0] cmul_re(
    input logic signed [31:0] xr,
    input logic signed [31:0] xi,
    input logic signed [31:0] yr,
    input logic signed [31:0] yi
);
    logic signed [63:0] p0, p1;
    begin
        p0 = xr * yr;
        p1 = xi * yi;
        cmul_re = p0 - p1;
    end
endfunction

function automatic signed [63:0] cmul_im(
    input logic signed [31:0] xr,
    input logic signed [31:0] xi,
    input logic signed [31:0] yr,
    input logic signed [31:0] yi
);
    logic signed [63:0] p0, p1;
    begin
        p0 = xr * yi;
        p1 = xi * yr;
        cmul_im = p0 + p1;
    end
endfunction

logic signed [31:0] A00_re, A00_im;
logic signed [31:0] A01_re, A01_im;
logic signed [31:0] A10_re, A10_im;
logic signed [31:0] A11_re, A11_im;

always_comb begin
    A00_re = in_a00_re;
    A00_im = 32'sd0;
    A11_re = in_a11_re;
    A11_im = 32'sd0;

    if (in_uplo) begin
        A01_re = in_a01_re;
        A01_im = in_a01_im;
        A10_re = in_a01_re;
        A10_im = -in_a01_im;
    end else begin
        A10_re = in_a10_re;
        A10_im = in_a10_im;
        A01_re = in_a10_re;
        A01_im = -in_a10_im;
    end
end

logic signed [63:0] p00_re0, p00_im0, p00_re1, p00_im1, p00_re, p00_im;
logic signed [63:0] p01_re0, p01_im0, p01_re1, p01_im1, p01_re, p01_im;
logic signed [63:0] p10_re0, p10_im0, p10_re1, p10_im1, p10_re, p10_im;
logic signed [63:0] p11_re0, p11_im0, p11_re1, p11_im1, p11_re, p11_im;

logic signed [95:0] ap00_re, ap00_im, ap01_re, ap01_im, ap10_re, ap10_im, ap11_re, ap11_im;
logic signed [95:0] bc00_re, bc00_im, bc01_re, bc01_im, bc10_re, bc10_im, bc11_re, bc11_im;
logic signed [95:0] out00_re_w, out00_im_w, out01_re_w, out01_im_w, out10_re_w, out10_im_w, out11_re_w, out11_im_w;

always_comb begin
    if (in_side == 1'b0) begin
        p00_re0 = cmul_re(A00_re, A00_im, in_b00_re, in_b00_im);
        p00_im0 = cmul_im(A00_re, A00_im, in_b00_re, in_b00_im);
        p00_re1 = cmul_re(A01_re, A01_im, in_b10_re, in_b10_im);
        p00_im1 = cmul_im(A01_re, A01_im, in_b10_re, in_b10_im);
        p00_re  = p00_re0 + p00_re1;
        p00_im  = p00_im0 + p00_im1;

        p01_re0 = cmul_re(A00_re, A00_im, in_b01_re, in_b01_im);
        p01_im0 = cmul_im(A00_re, A00_im, in_b01_re, in_b01_im);
        p01_re1 = cmul_re(A01_re, A01_im, in_b11_re, in_b11_im);
        p01_im1 = cmul_im(A01_re, A01_im, in_b11_re, in_b11_im);
        p01_re  = p01_re0 + p01_re1;
        p01_im  = p01_im0 + p01_im1;

        p10_re0 = cmul_re(A10_re, A10_im, in_b00_re, in_b00_im);
        p10_im0 = cmul_im(A10_re, A10_im, in_b00_re, in_b00_im);
        p10_re1 = cmul_re(A11_re, A11_im, in_b10_re, in_b10_im);
        p10_im1 = cmul_im(A11_re, A11_im, in_b10_re, in_b10_im);
        p10_re  = p10_re0 + p10_re1;
        p10_im  = p10_im0 + p10_im1;

        p11_re0 = cmul_re(A10_re, A10_im, in_b01_re, in_b01_im);
        p11_im0 = cmul_im(A10_re, A10_im, in_b01_re, in_b01_im);
        p11_re1 = cmul_re(A11_re, A11_im, in_b11_re, in_b11_im);
        p11_im1 = cmul_im(A11_re, A11_im, in_b11_re, in_b11_im);
        p11_re  = p11_re0 + p11_re1;
        p11_im  = p11_im0 + p11_im1;
    end else begin
        p00_re0 = cmul_re(in_b00_re, in_b00_im, A00_re, A00_im);
        p00_im0 = cmul_im(in_b00_re, in_b00_im, A00_re, A00_im);
        p00_re1 = cmul_re(in_b01_re, in_b01_im, A10_re, A10_im);
        p00_im1 = cmul_im(in_b01_re, in_b01_im, A10_re, A10_im);
        p00_re  = p00_re0 + p00_re1;
        p00_im  = p00_im0 + p00_im1;

        p01_re0 = cmul_re(in_b00_re, in_b00_im, A01_re, A01_im);
        p01_im0 = cmul_im(in_b00_re, in_b00_im, A01_re, A01_im);
        p01_re1 = cmul_re(in_b01_re, in_b01_im, A11_re, A11_im);
        p01_im1 = cmul_im(in_b01_re, in_b01_im, A11_re, A11_im);
        p01_re  = p01_re0 + p01_re1;
        p01_im  = p01_im0 + p01_im1;

        p10_re0 = cmul_re(in_b10_re, in_b10_im, A00_re, A00_im);
        p10_im0 = cmul_im(in_b10_re, in_b10_im, A00_re, A00_im);
        p10_re1 = cmul_re(in_b11_re, in_b11_im, A10_re, A10_im);
        p10_im1 = cmul_im(in_b11_re, in_b11_im, A10_re, A10_im);
        p10_re  = p10_re0 + p10_re1;
        p10_im  = p10_im0 + p10_im1;

        p11_re0 = cmul_re(in_b10_re, in_b10_im, A01_re, A01_im);
        p11_im0 = cmul_im(in_b10_re, in_b10_im, A01_re, A01_im);
        p11_re1 = cmul_re(in_b11_re, in_b11_im, A11_re, A11_im);
        p11_im1 = cmul_im(in_b11_re, in_b11_im, A11_re, A11_im);
        p11_re  = p11_re0 + p11_re1;
        p11_im  = p11_im0 + p11_im1;
    end

    ap00_re = cmul_re(in_alpha_re, in_alpha_im, p00_re[31:0], p00_im[31:0]);
    ap00_im = cmul_im(in_alpha_re, in_alpha_im, p00_re[31:0], p00_im[31:0]);
    ap01_re = cmul_re(in_alpha_re, in_alpha_im, p01_re[31:0], p01_im[31:0]);
    ap01_im = cmul_im(in_alpha_re, in_alpha_im, p01_re[31:0], p01_im[31:0]);
    ap10_re = cmul_re(in_alpha_re, in_alpha_im, p10_re[31:0], p10_im[31:0]);
    ap10_im = cmul_im(in_alpha_re, in_alpha_im, p10_re[31:0], p10_im[31:0]);
    ap11_re = cmul_re(in_alpha_re, in_alpha_im, p11_re[31:0], p11_im[31:0]);
    ap11_im = cmul_im(in_alpha_re, in_alpha_im, p11_re[31:0], p11_im[31:0]);

    bc00_re = cmul_re(in_beta_re, in_beta_im, in_c00_re, in_c00_im);
    bc00_im = cmul_im(in_beta_re, in_beta_im, in_c00_re, in_c00_im);
    bc01_re = cmul_re(in_beta_re, in_beta_im, in_c01_re, in_c01_im);
    bc01_im = cmul_im(in_beta_re, in_beta_im, in_c01_re, in_c01_im);
    bc10_re = cmul_re(in_beta_re, in_beta_im, in_c10_re, in_c10_im);
    bc10_im = cmul_im(in_beta_re, in_beta_im, in_c10_re, in_c10_im);
    bc11_re = cmul_re(in_beta_re, in_beta_im, in_c11_re, in_c11_im);
    bc11_im = cmul_im(in_beta_re, in_beta_im, in_c11_re, in_c11_im);

    out00_re_w = ap00_re + bc00_re;
    out00_im_w = ap00_im + bc00_im;
    out01_re_w = ap01_re + bc01_re;
    out01_im_w = ap01_im + bc01_im;
    out10_re_w = ap10_re + bc10_re;
    out10_im_w = ap10_im + bc10_im;
    out11_re_w = ap11_re + bc11_re;
    out11_im_w = ap11_im + bc11_im;
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy      <= 1'b0;
        out_c00_re <= 32'sd0;
        out_c00_im <= 32'sd0;
        out_c01_re <= 32'sd0;
        out_c01_im <= 32'sd0;
        out_c10_re <= 32'sd0;
        out_c10_im <= 32'sd0;
        out_c11_re <= 32'sd0;
        out_c11_im <= 32'sd0;
    end else if (input_accepted) begin
        out_c00_re <= out00_re_w[31:0];
        out_c00_im <= out00_im_w[31:0];
        out_c01_re <= out01_re_w[31:0];
        out_c01_im <= out01_im_w[31:0];
        out_c10_re <= out10_re_w[31:0];
        out_c10_im <= out10_im_w[31:0];
        out_c11_re <= out11_re_w[31:0];
        out_c11_im <= out11_im_w[31:0];
        busy       <= 1'b1;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule