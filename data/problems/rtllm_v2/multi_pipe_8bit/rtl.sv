module multi_pipe_8bit (
    input logic clk,
    input logic rst_n,
    input logic mul_en_in,
    input logic [7:0] mul_a,
    input logic [7:0] mul_b,
    output logic mul_en_out,
    output logic [15:0] mul_out
);
    logic [7:0] a_reg, b_reg;
    logic [2:0] valid_pipe;
    wire [15:0] partial [0:7];
    logic [15:0] pair_sum [0:3];
    logic [15:0] product;

    for (genvar bit_index = 0; bit_index < 8; bit_index++) begin : partial_products
        assign partial[bit_index] = b_reg[bit_index]
            ? ({8'b0, a_reg} << bit_index) : 16'd0;
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            a_reg <= 0;
            b_reg <= 0;
            valid_pipe <= 0;
            mul_en_out <= 0;
            product <= 0;
            mul_out <= 0;
            for (int j = 0; j < 4; j++) pair_sum[j] <= 0;
        end else begin
            a_reg <= mul_en_in ? mul_a : 8'd0;
            b_reg <= mul_en_in ? mul_b : 8'd0;
            valid_pipe <= {valid_pipe[1:0], mul_en_in};
            mul_en_out <= valid_pipe[2];
            for (int j = 0; j < 4; j++)
                pair_sum[j] <= partial[2*j] + partial[2*j+1];
            product <= pair_sum[0] + pair_sum[1] + pair_sum[2] + pair_sum[3];
            mul_out <= valid_pipe[2] ? product : 16'd0;
        end
    end
endmodule
