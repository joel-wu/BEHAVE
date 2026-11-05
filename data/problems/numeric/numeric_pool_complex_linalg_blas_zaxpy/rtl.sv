module complex_axpy_step (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [63:0]  in_alpha_re,
    input  logic [63:0]  in_alpha_im,
    input  logic [63:0]  in_x_re,
    input  logic [63:0]  in_x_im,
    input  logic [63:0]  in_y_re,
    input  logic [63:0]  in_y_im,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [63:0]  out_y_re,
    output logic [63:0]  out_y_im
);

logic busy;

wire input_accepted;
wire output_consumed;

logic signed [63:0] alpha_re_s;
logic signed [63:0] alpha_im_s;
logic signed [63:0] x_re_s;
logic signed [63:0] x_im_s;
logic signed [63:0] y_re_s;
logic signed [63:0] y_im_s;

logic signed [63:0] abs_alpha_re;
logic signed [63:0] abs_alpha_im;
logic [64:0] cabs1_alpha;

logic signed [127:0] mul_ar_xr;
logic signed [127:0] mul_ai_xi;
logic signed [127:0] mul_ar_xi;
logic signed [127:0] mul_ai_xr;
logic signed [128:0] prod_re_wide;
logic signed [128:0] prod_im_wide;
logic signed [129:0] sum_re_wide;
logic signed [129:0] sum_im_wide;

logic [63:0] next_out_y_re;
logic [63:0] next_out_y_im;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

always_comb begin
    alpha_re_s = $signed(in_alpha_re);
    alpha_im_s = $signed(in_alpha_im);
    x_re_s     = $signed(in_x_re);
    x_im_s     = $signed(in_x_im);
    y_re_s     = $signed(in_y_re);
    y_im_s     = $signed(in_y_im);

    if (alpha_re_s >= 0)
        abs_alpha_re = alpha_re_s;
    else
        abs_alpha_re = -alpha_re_s;

    if (alpha_im_s >= 0)
        abs_alpha_im = alpha_im_s;
    else
        abs_alpha_im = -alpha_im_s;

    cabs1_alpha = {1'b0, abs_alpha_re} + {1'b0, abs_alpha_im};

    mul_ar_xr = alpha_re_s * x_re_s;
    mul_ai_xi = alpha_im_s * x_im_s;
    mul_ar_xi = alpha_re_s * x_im_s;
    mul_ai_xr = alpha_im_s * x_re_s;

    prod_re_wide = $signed({mul_ar_xr[127], mul_ar_xr}) - $signed({mul_ai_xi[127], mul_ai_xi});
    prod_im_wide = $signed({mul_ar_xi[127], mul_ar_xi}) + $signed({mul_ai_xr[127], mul_ai_xr});

    sum_re_wide = $signed({{65{y_re_s[63]}}, y_re_s}) + $signed({prod_re_wide[128], prod_re_wide});
    sum_im_wide = $signed({{65{y_im_s[63]}}, y_im_s}) + $signed({prod_im_wide[128], prod_im_wide});

    if (cabs1_alpha == 65'd0) begin
        next_out_y_re = in_y_re;
        next_out_y_im = in_y_im;
    end else begin
        next_out_y_re = sum_re_wide[63:0];
        next_out_y_im = sum_im_wide[63:0];
    end
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy     <= 1'b0;
        out_y_re <= 64'h0000000000000000;
        out_y_im <= 64'h0000000000000000;
    end else if (input_accepted) begin
        busy     <= 1'b1;
        out_y_re <= next_out_y_re;
        out_y_im <= next_out_y_im;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule