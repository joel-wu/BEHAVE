module hamming_window_f32_generator (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic        in_start,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [3:0]  out_index,
    output logic [31:0] out_coeff
);

logic        busy;
logic [3:0]  index_reg;

wire input_accepted;
wire output_consumed;
wire frame_complete;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;
assign frame_complete  = output_consumed & (index_reg == 4'd15);

assign out_valid = busy;
assign in_ready  = ~busy | frame_complete;
assign out_index = busy ? index_reg : 4'h0;

function automatic logic [31:0] coeff_lut(input logic [3:0] idx);
    begin
        coeff_lut = 32'h00000000;
        unique case (idx)
            4'd0:  coeff_lut = 32'h3DA3D70A;
            4'd1:  coeff_lut = 32'h3DEB8D34;
            4'd2:  coeff_lut = 32'h3E5BE269;
            4'd3:  coeff_lut = 32'h3EBA59B4;
            4'd4:  coeff_lut = 32'h3F0A3D71;
            4'd5:  coeff_lut = 32'h3F374E07;
            4'd6:  coeff_lut = 32'h3F5D8247;
            4'd7:  coeff_lut = 32'h3F77093B;
            4'd8:  coeff_lut = 32'h3F800000;
            4'd9:  coeff_lut = 32'h3F77093B;
            4'd10: coeff_lut = 32'h3F5D8247;
            4'd11: coeff_lut = 32'h3F374E07;
            4'd12: coeff_lut = 32'h3F0A3D71;
            4'd13: coeff_lut = 32'h3EBA59B4;
            4'd14: coeff_lut = 32'h3E5BE269;
            4'd15: coeff_lut = 32'h3DEB8D34;
        endcase
    end
endfunction

assign out_coeff = busy ? coeff_lut(index_reg) : 32'h00000000;

always_ff @(posedge clk or posedge rst) begin
    if (rst) begin
        busy     <= 1'b0;
        index_reg <= 4'h0;
    end else if (input_accepted) begin
        busy     <= 1'b1;
        index_reg <= 4'h0;
    end else if (output_consumed) begin
        if (index_reg == 4'd15) begin
            busy     <= 1'b0;
            index_reg <= 4'h0;
        end else begin
            busy     <= 1'b1;
            index_reg <= index_reg + 4'd1;
        end
    end
end

endmodule
