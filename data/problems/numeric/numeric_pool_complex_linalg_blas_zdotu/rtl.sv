module complex_dot_product_streaming (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_x_re,
    input  logic [31:0] in_x_im,
    input  logic [31:0] in_y_re,
    input  logic [31:0] in_y_im,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [63:0] out_z_re,
    output logic [63:0] out_z_im
);

logic [2:0]  beat_index;
logic [63:0] sum_re;
logic [63:0] sum_im;
logic        busy;

wire input_accepted;
wire output_consumed;
wire signed [31:0] x_re_s;
wire signed [31:0] x_im_s;
wire signed [31:0] y_re_s;
wire signed [31:0] y_im_s;
wire signed [63:0] mul_xr_yr;
wire signed [63:0] mul_xi_yi;
wire signed [63:0] mul_xr_yi;
wire signed [63:0] mul_xi_yr;
wire signed [64:0] product_re_wide;
wire signed [64:0] product_im_wide;
wire signed [63:0] sum_re_signed;
wire signed [63:0] sum_im_signed;
wire signed [64:0] new_sum_re_wide;
wire signed [64:0] new_sum_im_wide;
wire [63:0] new_sum_re_mod;
wire [63:0] new_sum_im_mod;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

assign x_re_s = in_x_re;
assign x_im_s = in_x_im;
assign y_re_s = in_y_re;
assign y_im_s = in_y_im;

assign mul_xr_yr = x_re_s * y_re_s;
assign mul_xi_yi = x_im_s * y_im_s;
assign mul_xr_yi = x_re_s * y_im_s;
assign mul_xi_yr = x_im_s * y_re_s;

assign product_re_wide = $signed({mul_xr_yr[63], mul_xr_yr}) - $signed({mul_xi_yi[63], mul_xi_yi});
assign product_im_wide = $signed({mul_xr_yi[63], mul_xr_yi}) + $signed({mul_xi_yr[63], mul_xi_yr});

assign sum_re_signed   = sum_re;
assign sum_im_signed   = sum_im;

assign new_sum_re_wide = $signed({sum_re_signed[63], sum_re_signed}) + product_re_wide;
assign new_sum_im_wide = $signed({sum_im_signed[63], sum_im_signed}) + product_im_wide;

assign new_sum_re_mod  = new_sum_re_wide[63:0];
assign new_sum_im_mod  = new_sum_im_wide[63:0];

always_ff @(posedge clk) begin
    if (rst) begin
        beat_index <= 3'd0;
        sum_re     <= 64'h0000000000000000;
        sum_im     <= 64'h0000000000000000;
        out_z_re   <= 64'h0000000000000000;
        out_z_im   <= 64'h0000000000000000;
        busy       <= 1'b0;
    end else begin
        if (input_accepted) begin
            if (beat_index == 3'd7) begin
                out_z_re   <= new_sum_re_mod;
                out_z_im   <= new_sum_im_mod;
                beat_index <= 3'd0;
                sum_re     <= 64'h0000000000000000;
                sum_im     <= 64'h0000000000000000;
                busy       <= 1'b1;
            end else begin
                beat_index <= beat_index + 3'd1;
                sum_re     <= new_sum_re_mod;
                sum_im     <= new_sum_im_mod;
                busy       <= 1'b0;
            end
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end
end

endmodule