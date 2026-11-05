module bloom_filter_multi_hash_insert_k3 (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic [31:0] in_key,
    input logic in_insert,
    output logic out_valid,
    input logic out_ready,
    output logic [9:0] out_idx0,
    output logic [9:0] out_idx1,
    output logic [9:0] out_idx2,
    output logic out_present,
    output logic [1023:0] out_bits
);
    logic [1023:0] bits, mask;
    logic [31:0] a, b;
    logic [9:0] idx0, idx1, idx2;
    function automatic [31:0] mix32(input logic [31:0] word);
        logic [31:0] x;
        begin
            x = word ^ (word >> 16);
            x = x * 32'h85ebca6b;
            x = x ^ (x >> 13);
            x = x * 32'hc2b2ae35;
            mix32 = x ^ (x >> 16);
        end
    endfunction
    assign in_ready = !rst && (!out_valid || out_ready);
    always_comb begin
        a = mix32(in_key);
        b = mix32(in_key ^ 32'h27d4eb2f);
        idx0 = a[9:0];
        idx1 = a + b;
        idx2 = a + (b << 1);
        mask = (1024'd1 << idx0) | (1024'd1 << idx1) | (1024'd1 << idx2);
    end
    always_ff @(posedge clk) begin
        if (rst) begin
            bits <= 0;
            out_valid <= 0;
            out_idx0 <= 0; out_idx1 <= 0; out_idx2 <= 0;
            out_present <= 0; out_bits <= 0;
        end else begin
            if (out_valid && out_ready) out_valid <= 0;
            if (in_valid && in_ready) begin
                out_valid <= 1;
                out_idx0 <= idx0; out_idx1 <= idx1; out_idx2 <= idx2;
                out_present <= ((bits & mask) == mask);
                if (in_insert) begin
                    bits <= bits | mask;
                    out_bits <= bits | mask;
                end else out_bits <= bits;
            end
        end
    end
endmodule
