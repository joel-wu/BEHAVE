module direct_map_cache #(
    parameter int CACHE_SIZE=256,
    parameter int DATA_WIDTH=16,
    parameter int TAG_WIDTH=5,
    parameter int OFFSET_WIDTH=3
) (
    input logic clk, rst, enable,
    input logic [$clog2(CACHE_SIZE)-1:0] index,
    input logic [OFFSET_WIDTH-1:0] offset,
    input logic comp, write,
    input logic [TAG_WIDTH-1:0] tag_in,
    input logic [DATA_WIDTH-1:0] data_in,
    input logic valid_in,
    output logic hit, dirty,
    output logic [TAG_WIDTH-1:0] tag_out,
    output logic [DATA_WIDTH-1:0] data_out,
    output logic valid, error
);
    localparam int WORDS = 1 << (OFFSET_WIDTH-1);
    logic [TAG_WIDTH-1:0] tags [0:CACHE_SIZE-1];
    logic valid_bits [0:CACHE_SIZE-1];
    logic dirty_bits [0:CACHE_SIZE-1];
    logic [DATA_WIDTH-1:0] data_mem [0:CACHE_SIZE*WORDS-1];
    wire bad_access = offset[0] || index >= CACHE_SIZE;
    wire match_tag = valid_bits[index] && tags[index] == tag_in;
    wire [$clog2(CACHE_SIZE)+OFFSET_WIDTH-1:0] cell_addr = index*WORDS + (offset >> 1);
    always_comb begin
        hit=0; dirty=0; tag_out=0; data_out=0; valid=0; error=0;
        if (enable) begin
            if (bad_access) error=1;
            else begin
                hit=comp && match_tag;
                dirty=dirty_bits[index]; tag_out=tags[index];
                data_out=data_mem[cell_addr]; valid=valid_bits[index];
            end
        end
    end
    always_ff @(posedge clk) begin
        if (rst) begin
            for (int i=0; i<CACHE_SIZE; i++) begin
                tags[i]<=0; valid_bits[i]<=0; dirty_bits[i]<=0;
            end
            for (int i=0; i<CACHE_SIZE*WORDS; i++) data_mem[i]<=0;
        end else if (enable && !bad_access && write && (!comp || match_tag)) begin
            data_mem[cell_addr]<=data_in;
            if (comp) dirty_bits[index]<=1;
            else begin tags[index]<=tag_in; valid_bits[index]<=valid_in; dirty_bits[index]<=0; end
        end
    end
endmodule
