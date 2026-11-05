module ai_ml_ssm_rwkv_rwkv6_channel_mix #(
    parameter int WIDTH = 32
) (
    input  logic             clk,
    input  logic             rst,

    input  logic             in_valid,
    output logic             in_ready,
    input  logic [WIDTH-1:0] in_x,
    input  logic [WIDTH-1:0] in_mu,

    output logic             out_valid,
    input  logic             out_ready,
    output logic [WIDTH-1:0] out_y
);

    logic busy;
    logic signed [WIDTH-1:0] prev_x;
    logic signed [WIDTH-1:0] out_y_reg;

    logic signed [WIDTH-1:0] x_s;
    logic signed [WIDTH-1:0] mu_s;
    logic signed [WIDTH:0] delta_s;
    logic signed [(2*WIDTH)-1:0] prod_s;
    logic signed [(2*WIDTH)-1:0] sum_s;
    logic signed [WIDTH-1:0] y_s;

    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;
    assign out_y     = out_y_reg;

    always_comb begin
        x_s     = $signed(in_x);
        mu_s    = $signed(in_mu);
        delta_s = {prev_x[WIDTH-1], prev_x} - {x_s[WIDTH-1], x_s};
        prod_s  = delta_s * mu_s;
        sum_s   = $signed(x_s) + (prod_s / 64'sd65536);
        y_s     = sum_s[WIDTH-1:0];
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy      <= 1'b0;
            prev_x    <= '0;
            out_y_reg <= '0;
        end else if (input_accepted) begin
            out_y_reg <= y_s;
            prev_x    <= x_s;
            busy      <= 1'b1;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
