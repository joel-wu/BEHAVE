module ai_ml_moe_dispatch_moe_block_sparse_dispatch #(
    parameter int NUM_TOKENS = 8,
    parameter int NUM_EXPERTS = 4,
    parameter int TOP_K = 2,
    parameter int FFN_HIDDEN_SIZE = 256
) (
    input logic clk, rst, in_valid,
    output logic in_ready,
    input logic [31:0] in_top_experts,
    output logic out_valid,
    input logic out_ready,
    output logic [63:0] out_indices,
    output logic [31:0] out_bin_ids,
    output logic [19:0] out_bins,
    output logic [39:0] out_padded_bins,
    output logic [19:0] out_tokens_per_expert,
    output logic [2:0] out_block_rows,
    output logic [19:0] out_offsets,
    output logic [23:0] out_column_indices,
    output logic [15:0] out_row_indices,
    output logic [15:0] out_column_indices_t,
    output logic [35:0] out_offsets_t,
    output logic [23:0] out_block_offsets_t
);
    // Only this finite shape is part of the public task.
    initial begin
        if (NUM_TOKENS != 8 || NUM_EXPERTS != 4 || TOP_K != 2 ||
            FFN_HIDDEN_SIZE != 256) $fatal(1, "unsupported dispatch shape");
    end
    logic [63:0] next_indices;
    logic [31:0] next_bin_ids;
    logic [19:0] next_cumulative_bins;
    logic [39:0] next_padded_bins;
    logic [19:0] next_counts;
    logic [2:0] next_block_rows;
    logic [19:0] next_offsets;
    logic [23:0] next_columns;
    logic [15:0] next_rows;
    logic [15:0] next_columns_t;
    logic [35:0] next_offsets_t;
    logic [23:0] next_permutation_t;
    integer experts [0:15];
    integer counts [0:3];
    integer rank, total, row_count, col, entry, cursor;
    integer columns [0:7];
    integer rows [0:7];

    always_comb begin
        next_indices = '0;
        next_bin_ids = '0;
        next_cumulative_bins = '0;
        next_padded_bins = '0;
        next_counts = '0;
        next_block_rows = '0;
        next_offsets = '0;
        next_columns = '0;
        next_rows = '0;
        next_columns_t = '0;
        next_offsets_t = '0;
        next_permutation_t = '0;
        rank = 0;
        total = 0;
        row_count = 0;
        col = 0;
        entry = 0;
        cursor = 0;
        for (int e=0; e<4; e++) counts[e] = 0;
        for (int i=0; i<16; i++) begin
            experts[i] = int'(in_top_experts[2*i +: 2]);
            counts[experts[i]] = counts[experts[i]]+1;
        end
        // Rank by (expert, original position), independent of a sorting network.
        for (int i=0; i<16; i++) begin
            rank = 0;
            for (int j=0; j<16; j++)
                if ((experts[j] < experts[i]) ||
                    ((experts[j] == experts[i]) && (j < i))) rank = rank+1;
            next_indices[4*rank +: 4] = 4'(i);
            next_bin_ids[2*rank +: 2] = 2'(experts[i]);
        end
        for (int k=0; k<8; k++) begin
            columns[k] = 0;
            rows[k] = 0;
        end
        for (int e=0; e<4; e++) begin
            total = total+counts[e];
            next_counts[5*e +: 5] = 5'(counts[e]);
            next_cumulative_bins[5*e +: 5] = 5'(total);
            if (counts[e] != 0) begin
                columns[2*row_count] = 2*e;
                columns[2*row_count+1] = 2*e+1;
                rows[2*row_count] = row_count;
                rows[2*row_count+1] = row_count;
                row_count = row_count+1;
            end
            next_padded_bins[10*e +: 10] = 10'(128*row_count);
        end
        next_block_rows = 3'(row_count);
        for (int r=0; r<5; r++)
            next_offsets[4*r +: 4] = 4'(2*((r < row_count) ? r : row_count));
        for (int k=0; k<8; k++) begin
            next_columns[3*k +: 3] = 3'(columns[k]);
            next_rows[2*k +: 2] = 2'(rows[k]);
        end
        cursor = 0;
        for (int c=0; c<8; c++) begin
            for (int k=0; k<8; k++) begin
                if ((k < 2*row_count) && (columns[k] == c)) begin
                    next_columns_t[2*cursor +: 2] = 2'(rows[k]);
                    next_permutation_t[3*cursor +: 3] = 3'(k);
                    cursor = cursor+1;
                end
            end
            next_offsets_t[4*(c+1) +: 4] = 4'(cursor);
        end
    end
    assign in_ready = !out_valid || out_ready;
    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            out_valid <= 0;
            out_indices <= '0;
            out_bin_ids <= '0;
            out_bins <= '0;
            out_padded_bins <= '0;
            out_tokens_per_expert <= '0;
            out_block_rows <= '0;
            out_offsets <= '0;
            out_column_indices <= '0;
            out_row_indices <= '0;
            out_column_indices_t <= '0;
            out_offsets_t <= '0;
            out_block_offsets_t <= '0;
        end else if (in_ready) begin
            out_valid <= in_valid;
            if (in_valid) begin
                out_indices <= next_indices;
                out_bin_ids <= next_bin_ids;
                out_bins <= next_cumulative_bins;
                out_padded_bins <= next_padded_bins;
                out_tokens_per_expert <= next_counts;
                out_block_rows <= next_block_rows;
                out_offsets <= next_offsets;
                out_column_indices <= next_columns;
                out_row_indices <= next_rows;
                out_column_indices_t <= next_columns_t;
                out_offsets_t <= next_offsets_t;
                out_block_offsets_t <= next_permutation_t;
            end
        end
    end
endmodule
