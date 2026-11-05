module ai_ml_tx_kv_cache_kv_cache_paged_rr #(
    parameter int NUM_BLOCKS    = 2,
    parameter int NUM_KV_HEADS  = 2,
    parameter int HEAD_SIZE     = 4,
    parameter int BLOCK_SIZE    = 2,
    parameter int ELEM_WIDTH    = 16,
    parameter int SLOT_WIDTH    = 32
) (
    input  logic clk,
    input  logic rst,

    input  logic in_valid,
    output logic in_ready,
    input  logic [SLOT_WIDTH-1:0] in_slot_mapping,
    input  logic [NUM_KV_HEADS*HEAD_SIZE*ELEM_WIDTH-1:0] in_key,
    input  logic [NUM_KV_HEADS*HEAD_SIZE*ELEM_WIDTH-1:0] in_value,

    input  logic [0:0] in_read_block,
    input  logic [0:0] in_read_head,
    input  logic [1:0] in_read_elem,
    input  logic [0:0] in_read_slot,

    output logic out_valid,
    input  logic out_ready,
    output logic out_ok,
    output logic [ELEM_WIDTH-1:0] out_key_elem,
    output logic [ELEM_WIDTH-1:0] out_value_elem
);

    localparam int BLOCK_IDX_W = (NUM_BLOCKS > 1) ? $clog2(NUM_BLOCKS) : 1;
    localparam int SLOT_IDX_W  = (BLOCK_SIZE > 1) ? $clog2(BLOCK_SIZE) : 1;

    logic [ELEM_WIDTH-1:0] key_cache   [0:NUM_BLOCKS-1][0:NUM_KV_HEADS-1][0:HEAD_SIZE-1][0:BLOCK_SIZE-1];
    logic [ELEM_WIDTH-1:0] value_cache [0:NUM_BLOCKS-1][0:NUM_KV_HEADS-1][0:HEAD_SIZE-1][0:BLOCK_SIZE-1];

    logic busy;
    logic out_ok_reg;
    logic [ELEM_WIDTH-1:0] out_key_reg;
    logic [ELEM_WIDTH-1:0] out_value_reg;

    wire input_accepted   = in_valid & in_ready;
    wire output_consumed  = out_valid & out_ready;

    wire [SLOT_WIDTH-1:0]   block_index_full = in_slot_mapping / BLOCK_SIZE;
    wire [SLOT_WIDTH-1:0]   slot_index_full  = in_slot_mapping - (block_index_full * BLOCK_SIZE);
    wire                    write_in_range   = (block_index_full < NUM_BLOCKS);
    wire [BLOCK_IDX_W-1:0]  block_index_narrow = block_index_full[BLOCK_IDX_W-1:0];
    wire [SLOT_IDX_W-1:0]   slot_index_narrow  = slot_index_full[SLOT_IDX_W-1:0];

    // Combinational read with write-bypass: if this transaction writes the
    // same cell being read, the read sees the post-write value.
    logic [ELEM_WIDTH-1:0] read_key_eff;
    logic [ELEM_WIDTH-1:0] read_value_eff;
    logic                  read_targets_write;

    always_comb begin : read_eff_blk
        // Local var (declared inside the named block) so it can't infer a latch.
        int unsigned write_idx;
        write_idx = 32'(in_read_head) * 32'(HEAD_SIZE) + 32'(in_read_elem);

        // Bypass: if the in-flight write hits the same (block, slot) as the read
        // addr, return the new in_key / in_value element instead of the cached one.
        read_targets_write = write_in_range
                           && (block_index_narrow == in_read_block)
                           && (slot_index_narrow  == in_read_slot);

        if (read_targets_write) begin
            read_key_eff   = in_key  [write_idx*ELEM_WIDTH +: ELEM_WIDTH];
            read_value_eff = in_value[write_idx*ELEM_WIDTH +: ELEM_WIDTH];
        end else begin
            read_key_eff   = key_cache  [in_read_block][in_read_head][in_read_elem][in_read_slot];
            read_value_eff = value_cache[in_read_block][in_read_head][in_read_elem][in_read_slot];
        end
    end

    assign in_ready        = ~busy | output_consumed;
    assign out_valid       = busy;
    assign out_ok          = out_ok_reg;
    assign out_key_elem    = out_key_reg;
    assign out_value_elem  = out_value_reg;

    integer b, h, e, s, idx;

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            busy          <= 1'b0;
            out_ok_reg    <= 1'b0;
            out_key_reg   <= '0;
            out_value_reg <= '0;
            for (b = 0; b < NUM_BLOCKS; b = b + 1) begin
                for (h = 0; h < NUM_KV_HEADS; h = h + 1) begin
                    for (e = 0; e < HEAD_SIZE; e = e + 1) begin
                        for (s = 0; s < BLOCK_SIZE; s = s + 1) begin
                            key_cache[b][h][e][s]   <= '0;
                            value_cache[b][h][e][s] <= '0;
                        end
                    end
                end
            end
        end else if (input_accepted) begin
            out_ok_reg    <= write_in_range;
            out_key_reg   <= read_key_eff;
            out_value_reg <= read_value_eff;
            busy          <= 1'b1;

            if (write_in_range) begin
                for (h = 0; h < NUM_KV_HEADS; h = h + 1) begin
                    for (e = 0; e < HEAD_SIZE; e = e + 1) begin
                        idx = h * HEAD_SIZE + e;
                        key_cache[block_index_narrow][h][e][slot_index_narrow]
                            <= in_key[idx*ELEM_WIDTH +: ELEM_WIDTH];
                        value_cache[block_index_narrow][h][e][slot_index_narrow]
                            <= in_value[idx*ELEM_WIDTH +: ELEM_WIDTH];
                    end
                end
            end
        end else if (output_consumed) begin
            busy          <= 1'b0;
            out_ok_reg    <= 1'b0;
            out_key_reg   <= '0;
            out_value_reg <= '0;
        end
    end

endmodule
