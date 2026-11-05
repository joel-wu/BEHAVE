module blackman_harris92db_window (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [3:0]  in_index,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_value
);

logic busy;
wire  output_consumed;
wire  input_accepted;

function automatic logic [31:0] rom_lookup(input logic [3:0] idx);
    begin
        rom_lookup = 32'h00000000;
        unique case (idx)
            4'h0: rom_lookup = 32'h387BA882;
            4'h1: rom_lookup = 32'h3B487C4D;
            4'h2: rom_lookup = 32'h3CB20F5B;
            4'h3: rom_lookup = 32'h3DA988C2;
            4'h4: rom_lookup = 32'h3E5EB075;
            4'h5: rom_lookup = 32'h3EDEADC6;
            4'h6: rom_lookup = 32'h3F321D9A;
            4'h7: rom_lookup = 32'h3F6A0BB1;
            4'h8: rom_lookup = 32'h3F800000;
            4'h9: rom_lookup = 32'h3F6A0BB1;
            4'hA: rom_lookup = 32'h3F321D9A;
            4'hB: rom_lookup = 32'h3EDEADC6;
            4'hC: rom_lookup = 32'h3E5EB075;
            4'hD: rom_lookup = 32'h3DA988C2;
            4'hE: rom_lookup = 32'h3CB20F5B;
            4'hF: rom_lookup = 32'h3B487C4D;
        endcase
    end
endfunction

assign out_valid = busy;
assign output_consumed = out_valid & out_ready;
assign in_ready = ~busy | output_consumed;
assign input_accepted = in_valid & in_ready;

always_ff @(posedge clk) begin
    if (rst) begin
        busy      <= 1'b0;
        out_value <= 32'h00000000;
    end else if (input_accepted) begin
        out_value <= rom_lookup(in_index);
        busy      <= 1'b1;
    end else if (output_consumed) begin
        busy      <= 1'b0;
    end
end

endmodule
