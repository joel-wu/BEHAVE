module ai_ml_sparse_block_block_sparse_torchao (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [2:0]   in_M,
    input  logic [2:0]   in_K,
    input  logic [2:0]   in_N,
    input  logic [2:0]   in_num_block_rows,
    input  logic [2:0]   in_nnz_blocks,
    input  logic [39:0]  in_crow_indices,
    input  logic [23:0]  in_col_indices,
    input  logic [255:0] in_values,
    input  logic [511:0] in_x,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [511:0] out_y
);

    logic busy;
    logic [511:0] out_y_next;

    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;
    assign in_ready        = ~busy | output_consumed;
    assign out_valid       = busy;

    // Domain check: M, K, N capped at 4; num_block_rows at 2; nnz_blocks at 4.
    // Out-of-domain inputs produce all-zero output.
    wire domain_ok = (in_M <= 3'd4) && (in_K <= 3'd4) && (in_N <= 3'd4)
                  && (in_num_block_rows <= 3'd2) && (in_nnz_blocks <= 3'd4);

    // Reconstruct W as 4x4 (only the active 2*2 block rows).
    logic signed [7:0] W [0:3][0:3];
    integer br_i, p_i, c_i;
    logic [7:0] crow_lo, crow_hi;
    logic [2:0] col_at_p;

    always_comb begin
        // Initialize W
        for (br_i = 0; br_i < 4; br_i = br_i + 1) begin
            for (c_i = 0; c_i < 4; c_i = c_i + 1) begin
                W[br_i][c_i] = 8'sd0;
            end
        end
        // Populate W per block row.
        for (br_i = 0; br_i < 2; br_i = br_i + 1) begin
            if (br_i < in_num_block_rows) begin
                crow_lo = in_crow_indices[8*br_i +: 8];
                crow_hi = in_crow_indices[8*(br_i+1) +: 8];
                for (p_i = 0; p_i < 4; p_i = p_i + 1) begin
                    if (p_i >= crow_lo && p_i < crow_hi && p_i < in_nnz_blocks) begin
                        col_at_p = in_col_indices[3*p_i +: 3];
                        if (col_at_p < 3'd2) begin
                            W[2*br_i + 0][2*col_at_p + 0] = $signed(in_values[32*p_i + 0  +: 8]);
                            W[2*br_i + 0][2*col_at_p + 1] = $signed(in_values[32*p_i + 8  +: 8]);
                            W[2*br_i + 1][2*col_at_p + 0] = $signed(in_values[32*p_i + 16 +: 8]);
                            W[2*br_i + 1][2*col_at_p + 1] = $signed(in_values[32*p_i + 24 +: 8]);
                        end
                    end
                end
            end
        end
    end

    // Compute y[m][n] = sum_k W[m][k] * x[k][n], truncated to int8.
    // m,n in 0..3 (active region capped at 4), zero elsewhere.
    logic signed [31:0] acc;
    logic signed [7:0]  xe;
    integer m_i, n_i, k_i;
    logic [7:0] y_byte;

    always_comb begin
        out_y_next = 512'b0;
        if (domain_ok) begin
            for (m_i = 0; m_i < 4; m_i = m_i + 1) begin
                for (n_i = 0; n_i < 4; n_i = n_i + 1) begin
                    acc = 32'sd0;
                    if (m_i < in_M && n_i < in_N) begin
                        for (k_i = 0; k_i < 4; k_i = k_i + 1) begin
                            if (k_i < in_K) begin
                                xe = $signed(in_x[8*(8*k_i + n_i) +: 8]);
                                acc = acc + 32'(W[m_i][k_i]) * 32'(xe);
                            end
                        end
                    end
                    y_byte = acc[7:0];
                    out_y_next[8*(8*m_i + n_i) +: 8] = y_byte;
                end
            end
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy  <= 1'b0;
            out_y <= 512'b0;
        end else if (input_accepted) begin
            busy  <= 1'b1;
            out_y <= out_y_next;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
