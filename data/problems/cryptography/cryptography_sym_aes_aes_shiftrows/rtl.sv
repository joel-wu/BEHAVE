module cryptography_sym_aes_aes_shiftrows (
    input  logic         clk,
    input  logic         rst,
    input  logic         in_valid,
    output logic         in_ready,
    input  logic [127:0] in_state,
    output logic         out_valid,
    input  logic         out_ready,
    output logic [127:0] out_state
);

logic busy;
logic [127:0] out_state_next;

wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

always_comb begin
    out_state_next = 128'h00000000000000000000000000000000;
    out_state_next[127:120] = in_state[127:120];
    out_state_next[119:112] = in_state[87:80];
    out_state_next[111:104] = in_state[47:40];
    out_state_next[103:96]  = in_state[7:0];
    out_state_next[95:88]   = in_state[95:88];
    out_state_next[87:80]   = in_state[55:48];
    out_state_next[79:72]   = in_state[15:8];
    out_state_next[71:64]   = in_state[103:96];
    out_state_next[63:56]   = in_state[63:56];
    out_state_next[55:48]   = in_state[23:16];
    out_state_next[47:40]   = in_state[111:104];
    out_state_next[39:32]   = in_state[71:64];
    out_state_next[31:24]   = in_state[31:24];
    out_state_next[23:16]   = in_state[119:112];
    out_state_next[15:8]    = in_state[79:72];
    out_state_next[7:0]     = in_state[39:32];
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy      <= 1'b0;
        out_state <= 128'h00000000000000000000000000000000;
    end else if (input_accepted) begin
        busy      <= 1'b1;
        out_state <= out_state_next;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule