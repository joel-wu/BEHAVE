module compression_flac_entropy_flac_rice_encode (
    input  logic        clk,
    input  logic        rst,
    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_sample,
    input  logic [4:0]  in_parameter,
    output logic        out_valid,
    input  logic        out_ready,
    output logic [63:0] out_codeword,
    output logic [32:0] out_nbits
);
    wire [31:0] folded = {in_sample[30:0], 1'b0} ^ {32{in_sample[31]}};
    wire [31:0] quotient = folded >> in_parameter;
    wire [31:0] suffix = (32'd1 << in_parameter)
                            | (folded & ((32'd1 << in_parameter) - 32'd1));
    assign in_ready = !out_valid || out_ready;
    always_ff @(posedge clk) begin
        if (rst) begin
            out_valid <= 1'b0;
            out_codeword <= 64'd0;
            out_nbits <= 33'd0;
        end else if (in_ready) begin
            out_valid <= in_valid;
            if (in_valid) begin
                out_codeword <= {32'd0, suffix};
                out_nbits <= {1'b0, quotient} + 33'd1 + {28'd0, in_parameter};
            end
        end
    end
endmodule
