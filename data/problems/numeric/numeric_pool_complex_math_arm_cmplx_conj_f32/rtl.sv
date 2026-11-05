module complex_conjugate_f32 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_real,
    input  logic [31:0] in_imag,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_real,
    output logic [31:0] out_imag
);

logic busy;

wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

always_ff @(posedge clk) begin
    if (rst) begin
        busy     <= 1'b0;
        out_real <= 32'h00000000;
        out_imag <= 32'h00000000;
    end else if (input_accepted) begin
        busy     <= 1'b1;
        out_real <= in_real;
        out_imag <= in_imag ^ 32'h80000000;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule