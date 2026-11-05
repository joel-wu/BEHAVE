module compression_lz77_lz4_match_overlap_copy (
    input  logic         clk,
    input  logic         rst,
    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_offset,
    input  logic [5:0]   in_match_length,
    input  logic [255:0] in_history,
    output logic         out_valid,
    input  logic         out_ready,
    output logic [7:0]   out_byte,
    output logic         out_error
);

logic [255:0] history;
logic [5:0] distance;
logic [5:0] remaining;
logic invalid_offset;

assign out_valid = remaining != 0;
assign in_ready = !out_valid || (out_ready && remaining == 6'd1);
assign out_error = out_valid && invalid_offset;
always_comb begin
    out_byte = 8'd0;
    if (out_valid && !invalid_offset)
        out_byte = history[8*(32-int'(distance)) +: 8];
end

always_ff @(posedge clk) begin
    if (rst) begin
        history <= '0;
        distance <= 6'd1;
        remaining <= 6'd0;
        invalid_offset <= 1'b0;
    end else if (in_valid && in_ready) begin
        history <= in_history;
        distance <= in_offset[5:0];
        invalid_offset <= in_offset == 0 || in_offset > 16'd32;
        if (in_match_length == 0)
            remaining <= 6'd0;
        else if (in_offset == 0 || in_offset > 16'd32)
            remaining <= 6'd1;
        else
            remaining <= in_match_length;
    end else if (out_valid && out_ready) begin
        remaining <= remaining - 6'd1;
        history <= {out_byte, history[255:8]};
    end
end
endmodule
