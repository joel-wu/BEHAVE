module SHA256ChMaj (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_x,
    input  logic [31:0] in_y,
    input  logic [31:0] in_z,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_ch,
    output logic [31:0] out_maj
);

logic busy;

wire input_accepted;
wire output_consumed;
logic [31:0] ch_calc;
logic [31:0] maj_calc;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = rst ? 1'b1 : (~busy | output_consumed);
assign out_valid = rst ? 1'b0 : busy;

always_comb begin
    ch_calc  = ((in_x & in_y) ^ ((~in_x) & in_z));
    maj_calc = ((in_x & in_y) ^ (in_x & in_z) ^ (in_y & in_z));
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy    <= 1'b0;
        out_ch  <= 32'b0;
        out_maj <= 32'b0;
    end else if (input_accepted) begin
        busy    <= 1'b1;
        out_ch  <= ch_calc;
        out_maj <= maj_calc;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule