module onehot_to_bin (
    input  logic        clk,
    input  logic        rst,
    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_onehot,
    output logic        out_valid,
    input  logic        out_ready,
    output logic [3:0]  out_bin
);

logic busy;

wire input_accepted;
wire output_consumed;
logic [3:0] next_out_bin;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

always_comb begin
    next_out_bin = 4'b0000;
    next_out_bin[0] = in_onehot[1]  | in_onehot[3]  | in_onehot[5]  | in_onehot[7] |
                      in_onehot[9]  | in_onehot[11] | in_onehot[13] | in_onehot[15];
    next_out_bin[1] = in_onehot[2]  | in_onehot[3]  | in_onehot[6]  | in_onehot[7] |
                      in_onehot[10] | in_onehot[11] | in_onehot[14] | in_onehot[15];
    next_out_bin[2] = in_onehot[4]  | in_onehot[5]  | in_onehot[6]  | in_onehot[7] |
                      in_onehot[12] | in_onehot[13] | in_onehot[14] | in_onehot[15];
    next_out_bin[3] = in_onehot[8]  | in_onehot[9]  | in_onehot[10] | in_onehot[11] |
                      in_onehot[12] | in_onehot[13] | in_onehot[14] | in_onehot[15];
end

always_ff @(posedge clk or posedge rst) begin
    if (rst) begin
        busy    <= 1'b0;
        out_bin <= 4'h0;
    end else if (input_accepted) begin
        busy    <= 1'b1;
        out_bin <= next_out_bin;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule