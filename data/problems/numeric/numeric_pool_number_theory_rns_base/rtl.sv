module rns_exact_base_convert_q_to_t (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_x0,
    input  logic [15:0] in_x1,
    input  logic [15:0] in_x2,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [15:0] out_y
);

logic busy;

wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

logic [15:0] x0_mod;
logic [15:0] x1_mod;
logic [15:0] x2_mod;

logic [15:0] a0;
logic [15:0] a1;
logic [15:0] a2;

logic [15:0] s_num;
logic [15:0] v;
logic [15:0] sum_mod_t;
logic [15:0] corr;
logic [15:0] y_calc;

always_comb begin
    x0_mod = in_x0 % 16'd7;
    x1_mod = in_x1 % 16'd11;
    x2_mod = in_x2 % 16'd13;

    a0 = (x0_mod * 16'd5) % 16'd7;
    a1 = (x1_mod * 16'd4) % 16'd11;
    a2 = (x2_mod * 16'd12) % 16'd13;

    s_num = a0 * 16'd143 + a1 * 16'd91 + a2 * 16'd77;
    v = (s_num * 16'd2 + 16'd1001) / 16'd2002;

    sum_mod_t = (a0 * 16'd7 + a1 * 16'd6 + a2 * 16'd9) % 16'd17;
    corr = (v * 16'd15) % 16'd17;

    if (sum_mod_t >= corr) begin
        y_calc = sum_mod_t - corr;
    end else begin
        y_calc = sum_mod_t + 16'd17 - corr;
    end
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy  <= 1'b0;
        out_y <= 16'd0;
    end else if (input_accepted) begin
        out_y <= y_calc;
        busy  <= 1'b1;
    end else if (output_consumed) begin
        busy  <= 1'b0;
    end
end

endmodule