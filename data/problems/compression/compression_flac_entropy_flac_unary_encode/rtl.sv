module compression_flac_entropy_flac_unary_encode (
    input  logic       clk,
    input  logic       rst,
    input  logic       in_valid,
    output logic       in_ready,
    input  logic [7:0] in_val,
    input  logic       in_flush,
    output logic       out_valid,
    input  logic       out_ready,
    output logic [7:0] out_byte
);

logic       busy;
logic [7:0] zeros_left;
logic       flush;
logic [7:0] partial_byte;
logic [3:0] used_bits;
logic [3:0] free_bits;
logic [3:0] final_count;
logic [7:0] final_byte;

assign in_ready = !busy && (!out_valid || out_ready);
assign free_bits = 4'd8 - used_bits;
// These final-code values are consumed only when zeros_left < free_bits.
assign final_count = used_bits + {1'b0, zeros_left[2:0]} + 4'd1;
assign final_byte = partial_byte | (8'h80 >> (final_count - 4'd1));

always_ff @(posedge clk) begin
    if (rst) begin
        busy         <= 1'b0;
        zeros_left   <= 8'd0;
        flush        <= 1'b0;
        partial_byte <= 8'd0;
        used_bits    <= 4'd0;
        out_valid    <= 1'b0;
        out_byte     <= 8'd0;
    end else begin
        if (out_valid && out_ready)
            out_valid <= 1'b0;

        if (in_valid && in_ready) begin
            zeros_left <= in_val;
            flush      <= in_flush;
            busy       <= 1'b1;
        end else if (busy && (!out_valid || out_ready)) begin
            if (zeros_left >= {4'd0, free_bits}) begin
                // Finish this byte with zeros; the stop bit is still pending.
                out_byte     <= partial_byte;
                out_valid    <= 1'b1;
                zeros_left   <= zeros_left - {4'd0, free_bits};
                partial_byte <= 8'd0;
                used_bits    <= 4'd0;
            end else begin
                // All remaining zeros and the stop bit fit in this byte.
                busy <= 1'b0;
                if (flush || final_count == 4'd8) begin
                    out_byte     <= final_byte;
                    out_valid    <= 1'b1;
                    partial_byte <= 8'd0;
                    used_bits    <= 4'd0;
                end else begin
                    partial_byte <= final_byte;
                    used_bits    <= final_count;
                end
            end
        end
    end
end

endmodule
