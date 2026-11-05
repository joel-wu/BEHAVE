module compression_lz77_lz77_match_length_extend (
    input  logic         clk,
    input  logic         rst,
    input  logic         in_valid,
    output logic         in_ready,
    input  logic [511:0] in_str_bytes,
    input  logic [511:0] in_match_bytes,
    input  logic [6:0]   in_start_len,
    input  logic [6:0]   in_max_len,
    output logic         out_valid,
    input  logic         out_ready,
    output logic [6:0]   out_len,
    output logic         out_error
);

logic [6:0] length_next;
logic invalid_bounds;
logic matching;
assign in_ready = !out_valid || out_ready;

always_comb begin
    invalid_bounds = (in_start_len > in_max_len) || (in_max_len > 7'd64);
    length_next = invalid_bounds ? 7'd0 : in_start_len;
    matching = !invalid_bounds;
    for (int i = 0; i < 64; i++) begin
        if (matching && i >= int'(in_start_len) && i < int'(in_max_len)) begin
            if (in_str_bytes[8*i +: 8] != in_match_bytes[8*i +: 8])
                matching = 1'b0;
            else
                length_next = length_next + 7'd1;
        end
    end
end

always_ff @(posedge clk) begin
    if (rst) begin
        out_valid <= 1'b0;
        out_len <= 7'd0;
        out_error <= 1'b0;
    end else if (in_ready) begin
        out_valid <= in_valid;
        if (in_valid) begin
            out_len <= length_next;
            out_error <= invalid_bounds;
        end
    end
end
endmodule
