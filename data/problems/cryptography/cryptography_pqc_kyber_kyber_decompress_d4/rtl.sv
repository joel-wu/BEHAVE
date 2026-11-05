module cryptography_pqc_kyber_kyber_decompress_d4 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [7:0]  in_byte,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [11:0] out_coeff0,
    output logic [11:0] out_coeff1
);

logic busy;

wire input_accepted;
wire output_consumed;

logic [3:0] t0;
logic [3:0] t1;
logic [15:0] prod0;
logic [15:0] prod1;
logic [15:0] sum0;
logic [15:0] sum1;
logic [11:0] coeff0_calc;
logic [11:0] coeff1_calc;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

always_comb begin
    t0 = in_byte[3:0];
    t1 = in_byte[7:4];

    prod0 = t0 * 16'd3329;
    prod1 = t1 * 16'd3329;

    sum0 = prod0 + 16'd8;
    sum1 = prod1 + 16'd8;

    coeff0_calc = sum0[15:4];
    coeff1_calc = sum1[15:4];
end

always_ff @(posedge clk or posedge rst) begin
    if (rst) begin
        busy       <= 1'b0;
        out_coeff0 <= 12'h000;
        out_coeff1 <= 12'h000;
    end else if (input_accepted) begin
        busy       <= 1'b1;
        out_coeff0 <= coeff0_calc;
        out_coeff1 <= coeff1_calc;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule