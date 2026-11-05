module control_closed_loop_lowpass_iir_first_order_q15 (
    input  logic               clk,
    input  logic               rst,

    input  logic               in_valid,
    output logic               in_ready,
    input  logic signed [15:0] in_x,
    input  logic signed [15:0] in_alpha,

    output logic               out_valid,
    input  logic               out_ready,
    output logic signed [15:0] out_y
);

logic signed [15:0] y_prev;
logic signed [15:0] out_y_reg;
logic               busy;

logic signed [17:0] one_minus_alpha;
logic signed [32:0] term_prev_q30;
logic signed [32:0] term_x_q30;
logic signed [33:0] sum_q30;
logic signed [33:0] y_raw;

wire input_accepted;
wire output_consumed;

function automatic logic signed [15:0] sat16(input logic signed [33:0] value);
    if (value > 34'sd32767)
        sat16 = 16'sd32767;
    else if (value < -34'sd32768)
        sat16 = -16'sd32768;
    else
        sat16 = value[15:0];
endfunction

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;
assign out_y     = out_y_reg;

always_comb begin
    one_minus_alpha = 18'sd32768 - $signed(in_alpha);
    term_prev_q30   = $signed(y_prev) * $signed(in_alpha);
    term_x_q30      = $signed(in_x) * one_minus_alpha;
    sum_q30         = term_prev_q30 + term_x_q30;
    y_raw           = sum_q30 >>> 15;
end

always_ff @(posedge clk) begin
    if (rst) begin
        y_prev    <= 16'sh0000;
        out_y_reg <= 16'sh0000;
        busy      <= 1'b0;
    end else if (input_accepted) begin
        out_y_reg <= sat16(y_raw);
        y_prev    <= sat16(y_raw);
        busy      <= 1'b1;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule
