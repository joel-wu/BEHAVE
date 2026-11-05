module data_systems_distance_inner_product_int8_d64 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [511:0] in_vec_a,
    input  logic [511:0] in_vec_b,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [20:0]  out_distance
);

logic busy;
wire  output_consumed;
wire  input_accepted;

logic signed [20:0] inner_product;
logic signed [20:0] distance_calc;

integer i;
logic signed [7:0] a_i;
logic signed [7:0] b_i;
logic signed [15:0] product_i;

assign output_consumed = out_valid & out_ready;
assign input_accepted  = in_valid & in_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

always_comb begin
    inner_product = 21'sd0;
    for (i = 0; i < 64; i = i + 1) begin
        a_i = $signed(in_vec_a[i*8 +: 8]);
        b_i = $signed(in_vec_b[i*8 +: 8]);
        product_i = a_i * b_i;
        inner_product = inner_product + product_i;
    end
    distance_calc = 21'sd1 - inner_product;
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy         <= 1'b0;
        out_distance <= 21'd0;
    end else if (input_accepted) begin
        out_distance <= distance_calc;
        busy         <= 1'b1;
    end else if (output_consumed) begin
        busy         <= 1'b0;
    end
end

endmodule
