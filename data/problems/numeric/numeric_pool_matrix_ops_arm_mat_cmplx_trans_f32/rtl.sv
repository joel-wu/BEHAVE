module complex_matrix_transpose_tile (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [31:0]  in_a00_real,
    input  logic [31:0]  in_a00_imag,
    input  logic [31:0]  in_a01_real,
    input  logic [31:0]  in_a01_imag,
    input  logic [31:0]  in_a10_real,
    input  logic [31:0]  in_a10_imag,
    input  logic [31:0]  in_a11_real,
    input  logic [31:0]  in_a11_imag,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_b00_real,
    output logic [31:0]  out_b00_imag,
    output logic [31:0]  out_b01_real,
    output logic [31:0]  out_b01_imag,
    output logic [31:0]  out_b10_real,
    output logic [31:0]  out_b10_imag,
    output logic [31:0]  out_b11_real,
    output logic [31:0]  out_b11_imag
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
        busy         <= 1'b0;
        out_b00_real <= 32'b0;
        out_b00_imag <= 32'b0;
        out_b01_real <= 32'b0;
        out_b01_imag <= 32'b0;
        out_b10_real <= 32'b0;
        out_b10_imag <= 32'b0;
        out_b11_real <= 32'b0;
        out_b11_imag <= 32'b0;
    end else if (input_accepted) begin
        busy         <= 1'b1;
        out_b00_real <= in_a00_real;
        out_b00_imag <= in_a00_imag;
        out_b01_real <= in_a10_real;
        out_b01_imag <= in_a10_imag;
        out_b10_real <= in_a01_real;
        out_b10_imag <= in_a01_imag;
        out_b11_real <= in_a11_real;
        out_b11_imag <= in_a11_imag;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule