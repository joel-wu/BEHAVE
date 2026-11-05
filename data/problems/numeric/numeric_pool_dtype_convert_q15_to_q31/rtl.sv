module q15_to_q31_converter (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_sample,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_sample
);

logic busy;
wire  input_accepted;
wire  output_consumed;
logic signed [15:0] in_sample_s;
logic signed [31:0] converted_sample;

assign output_consumed = out_valid & out_ready;
assign in_ready        = ~busy | output_consumed;
assign out_valid       = busy;

assign input_accepted  = in_valid & in_ready;

always_comb begin
    in_sample_s      = in_sample;
    converted_sample = in_sample_s <<< 16;
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy       <= 1'b0;
        out_sample <= 32'h00000000;
    end else if (input_accepted) begin
        busy       <= 1'b1;
        out_sample <= converted_sample;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule