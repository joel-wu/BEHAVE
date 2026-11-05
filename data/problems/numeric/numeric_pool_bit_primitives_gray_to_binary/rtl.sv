module gray_to_binary_8 (
    input  logic       clk,
    input  logic       rst,
    input  logic       in_valid,
    output logic       in_ready,
    input  logic [7:0] in_a,
    output logic       out_valid,
    input  logic       out_ready,
    output logic [7:0] out_z
);

logic       busy;
logic [7:0] out_z_next;

wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

always_comb begin
    out_z_next[7] = in_a[7];
    out_z_next[6] = in_a[7] ^ in_a[6];
    out_z_next[5] = in_a[7] ^ in_a[6] ^ in_a[5];
    out_z_next[4] = in_a[7] ^ in_a[6] ^ in_a[5] ^ in_a[4];
    out_z_next[3] = in_a[7] ^ in_a[6] ^ in_a[5] ^ in_a[4] ^ in_a[3];
    out_z_next[2] = in_a[7] ^ in_a[6] ^ in_a[5] ^ in_a[4] ^ in_a[3] ^ in_a[2];
    out_z_next[1] = in_a[7] ^ in_a[6] ^ in_a[5] ^ in_a[4] ^ in_a[3] ^ in_a[2] ^ in_a[1];
    out_z_next[0] = in_a[7] ^ in_a[6] ^ in_a[5] ^ in_a[4] ^ in_a[3] ^ in_a[2] ^ in_a[1] ^ in_a[0];
end

always_ff @(posedge clk or posedge rst) begin
    if (rst) begin
        busy  <= 1'b0;
        out_z <= 8'b00000000;
    end else begin
        if (input_accepted) begin
            busy  <= 1'b1;
            out_z <= out_z_next;
        end else if (output_consumed) begin
            busy  <= 1'b0;
            out_z <= 8'b00000000;
        end else if (!busy) begin
            out_z <= 8'b00000000;
        end
    end
end

endmodule