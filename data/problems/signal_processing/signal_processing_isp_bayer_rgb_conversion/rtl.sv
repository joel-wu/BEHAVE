module signal_processing_isp_bayer_rgb_conversion (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [7:0]  in_y,
    input  logic [7:0]  in_u,
    input  logic [7:0]  in_v,
    input  logic        in_conv_std,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [7:0]  out_r,
    output logic [7:0]  out_g,
    output logic [7:0]  out_b
);

logic busy;

wire input_accepted;
wire output_consumed;

logic signed [8:0]  y_off;
logic signed [8:0]  u_off;
logic signed [8:0]  v_off;
logic signed [16:0] r_mul;
logic signed [16:0] g_mul;
logic signed [16:0] b_mul;
logic signed [10:0] r_shift;
logic signed [10:0] g_shift;
logic signed [10:0] b_shift;
logic [7:0]         next_r;
logic [7:0]         next_g;
logic [7:0]         next_b;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

function automatic logic [7:0] clamp_u8(input logic signed [10:0] value);
    begin
        if (value < 0)
            clamp_u8 = 8'd0;
        else if (value > 11'sd255)
            clamp_u8 = 8'd255;
        else
            clamp_u8 = value[7:0];
    end
endfunction

always_comb begin
    y_off = $signed({1'b0, in_y}) - 9'sd16;
    u_off = $signed({1'b0, in_u}) - 9'sd128;
    v_off = $signed({1'b0, in_v}) - 9'sd128;

    r_mul = 17'sd0;
    g_mul = 17'sd0;
    b_mul = 17'sd0;

    if (in_conv_std == 1'b1) begin
        r_mul = 17'sd74  * y_off + 17'sd114 * v_off;
        g_mul = 17'sd74  * y_off - 17'sd13  * u_off - 17'sd34 * v_off;
        b_mul = 17'sd74  * y_off + 17'sd135 * u_off;
    end else begin
        r_mul = 17'sd64  * y_off + 17'sd87  * v_off;
        g_mul = 17'sd64  * y_off - 17'sd20  * u_off - 17'sd44 * v_off;
        b_mul = 17'sd61  * y_off + 17'sd105 * u_off;
    end

    r_shift = r_mul >>> 6;
    g_shift = g_mul >>> 6;
    b_shift = b_mul >>> 6;

    next_r = clamp_u8(r_shift);
    next_g = clamp_u8(g_shift);
    next_b = clamp_u8(b_shift);
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy  <= 1'b0;
        out_r <= 8'd0;
        out_g <= 8'd0;
        out_b <= 8'd0;
    end else if (input_accepted) begin
        busy  <= 1'b1;
        out_r <= next_r;
        out_g <= next_g;
        out_b <= next_b;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule