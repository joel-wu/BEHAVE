module compression_huffman_huffman_lut_decode_8b (
    input  logic        clk,
    input  logic        rst,
    input  logic        in_valid,
    output logic        in_ready,
    input  logic        in_write,
    input  logic [10:0] in_dt_log,
    input  logic [10:0] in_lookup_bits,
    input  logic [7:0]  in_symbol,
    input  logic [5:0]  in_nb_bits,
    output logic        out_valid,
    input  logic        out_ready,
    output logic [7:0]  out_symbol,
    output logic [5:0]  out_nb_bits,
    output logic        out_error
);

logic [7:0] symbols [0:2047];
logic [5:0] lengths [0:2047];
logic [10:0] index;
logic bad_entry;
assign in_ready = !out_valid || out_ready;
always_comb begin
    index = 11'd0;
    if (in_dt_log >= 1 && in_dt_log <= 11)
        index = in_lookup_bits & ((11'd1 << in_dt_log) - 11'd1);
    bad_entry = in_dt_log < 1 || in_dt_log > 11
             || lengths[index] == 0 || {5'd0, lengths[index]} > in_dt_log;
end

always_ff @(posedge clk) begin
    if (rst) begin
        for (int i = 0; i < 2048; i++) begin
            symbols[i] <= 8'd0;
            lengths[i] <= 6'd0;
        end
        out_valid <= 1'b0;
        out_symbol <= 8'd0;
        out_nb_bits <= 6'd0;
        out_error <= 1'b0;
    end else if (in_ready) begin
        out_valid <= 1'b0;
        if (in_valid) begin
            if (in_write) begin
                symbols[in_lookup_bits] <= in_symbol;
                lengths[in_lookup_bits] <= in_nb_bits;
            end else begin
                out_valid <= 1'b1;
                out_error <= bad_entry;
                out_symbol <= bad_entry ? 8'd0 : symbols[index];
                out_nb_bits <= bad_entry ? 6'd0 : lengths[index];
            end
        end
    end
end
endmodule
