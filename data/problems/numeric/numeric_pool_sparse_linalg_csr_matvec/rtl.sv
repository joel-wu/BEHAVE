// 4-lane sparse-matrix x dense-vector kernel in CSR form.
// Each transaction reads:
//   row_start = row_ptr[r], row_end = row_ptr[r+1]   (9-bit each)
//   b_pack    = b_vec[r]                              (64-bit, 4×16)
// Then iterates k in [row_start, row_end), each step:
//   c       = col_idx[k]    (8-bit)
//   a       = a_val[k]      (signed 16-bit)
//   x_pack  = x_vec[c]      (64-bit, 4×16 signed)
//   sumL   += a * xL        (signed 32-bit wraparound) for L = 0..3
// Finally:
//   out_yL  = unsigned16(alpha * signed16(sumL) + beta * bL)
//
// All arithmetic is two's-complement wraparound per spec; no
// saturation.

module csr_matvec4_lane (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [7:0]   in_row,
    input  logic [15:0]  in_alpha,
    input  logic [15:0]  in_beta,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_y0,
    output logic [15:0]  out_y1,
    output logic [15:0]  out_y2,
    output logic [15:0]  out_y3,

    output logic         row_ptr_req_valid,
    output logic [8:0]   row_ptr_req_addr,
    input  logic         row_ptr_req_ready,
    input  logic         row_ptr_resp_valid,
    input  logic [8:0]   row_ptr_resp_rdata,

    output logic         col_idx_req_valid,
    output logic [9:0]   col_idx_req_addr,
    input  logic         col_idx_req_ready,
    input  logic         col_idx_resp_valid,
    input  logic [7:0]   col_idx_resp_rdata,

    output logic         a_val_req_valid,
    output logic [9:0]   a_val_req_addr,
    input  logic         a_val_req_ready,
    input  logic         a_val_resp_valid,
    input  logic [15:0]  a_val_resp_rdata,

    output logic         x_vec_req_valid,
    output logic [9:0]   x_vec_req_addr,
    input  logic         x_vec_req_ready,
    input  logic         x_vec_resp_valid,
    input  logic [63:0]  x_vec_resp_rdata,

    output logic         b_vec_req_valid,
    output logic [9:0]   b_vec_req_addr,
    input  logic         b_vec_req_ready,
    input  logic         b_vec_resp_valid,
    input  logic [63:0]  b_vec_resp_rdata
);

    localparam int unsigned NNZ_PER_ROW = 2;

    typedef enum logic [3:0] {
        ST_IDLE,
        ST_REQ_RP,
        ST_WAIT_RP,
        ST_REQ_B,
        ST_WAIT_B,
        ST_LOOP_CHECK,
        ST_REQ_COL,
        ST_WAIT_COL,
        ST_REQ_A,
        ST_WAIT_A,
        ST_REQ_X,
        ST_WAIT_X,
        ST_OUT
    } state_t;

    state_t      state;
    logic        busy;

    logic [7:0]  r_reg;
    logic [15:0] alpha_reg;
    logic [15:0] beta_reg;

    logic [8:0]  row_start_reg;
    logic [9:0]  k_reg;          // 10-bit because row_start + 1 may overflow 9 bits
    logic [1:0]  iter_cnt;       // counts entries already processed (0..NNZ_PER_ROW)

    logic [63:0] b_pack_reg;
    logic [7:0]  c_reg;
    logic [15:0] a_reg;

    logic [31:0] sum0_reg, sum1_reg, sum2_reg, sum3_reg;

    logic [15:0] out_y0_reg, out_y1_reg, out_y2_reg, out_y3_reg;

    wire input_accepted  = in_valid  & in_ready;
    wire output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy && (state == ST_OUT);
    assign out_y0    = out_y0_reg;
    assign out_y1    = out_y1_reg;
    assign out_y2    = out_y2_reg;
    assign out_y3    = out_y3_reg;

    // Memory request muxing: state-driven so only one channel asserts
    // req_valid at any time.  Single row_ptr access (row_ptr[r] only —
    // row_ptr[r+1] is intentionally not consulted because NNZ_PER_ROW
    // is fixed at 2).
    assign row_ptr_req_valid = (state == ST_REQ_RP);
    assign row_ptr_req_addr  = {1'b0, r_reg};

    assign b_vec_req_valid   = (state == ST_REQ_B);
    assign b_vec_req_addr    = {2'b00, r_reg};

    assign col_idx_req_valid = (state == ST_REQ_COL);
    assign col_idx_req_addr  = k_reg;

    assign a_val_req_valid   = (state == ST_REQ_A);
    assign a_val_req_addr    = k_reg;

    assign x_vec_req_valid   = (state == ST_REQ_X);
    assign x_vec_req_addr    = {2'b00, c_reg};

    // Final-stage combinational compute (used in transition into ST_OUT
    // from ST_LOOP_CHECK when the iteration count is exhausted).
    logic signed [15:0] alpha_s, beta_s;
    logic signed [15:0] sum0_low, sum1_low, sum2_low, sum3_low;
    logic signed [15:0] b0_s, b1_s, b2_s, b3_s;
    logic signed [31:0] alpha_mul0, alpha_mul1, alpha_mul2, alpha_mul3;
    logic signed [31:0] beta_mul0,  beta_mul1,  beta_mul2,  beta_mul3;
    logic signed [31:0] final0, final1, final2, final3;

    always_comb begin
        alpha_s   = $signed(alpha_reg);
        beta_s    = $signed(beta_reg);
        sum0_low  = $signed(sum0_reg[15:0]);
        sum1_low  = $signed(sum1_reg[15:0]);
        sum2_low  = $signed(sum2_reg[15:0]);
        sum3_low  = $signed(sum3_reg[15:0]);
        b0_s      = $signed(b_pack_reg[15:0]);
        b1_s      = $signed(b_pack_reg[31:16]);
        b2_s      = $signed(b_pack_reg[47:32]);
        b3_s      = $signed(b_pack_reg[63:48]);
        alpha_mul0 = alpha_s * sum0_low;
        alpha_mul1 = alpha_s * sum1_low;
        alpha_mul2 = alpha_s * sum2_low;
        alpha_mul3 = alpha_s * sum3_low;
        beta_mul0  = beta_s  * b0_s;
        beta_mul1  = beta_s  * b1_s;
        beta_mul2  = beta_s  * b2_s;
        beta_mul3  = beta_s  * b3_s;
        final0     = alpha_mul0 + beta_mul0;
        final1     = alpha_mul1 + beta_mul1;
        final2     = alpha_mul2 + beta_mul2;
        final3     = alpha_mul3 + beta_mul3;
    end

    // Per-iteration MAC compute (used at ST_WAIT_X latch using the
    // just-arrived x_vec_resp_rdata).
    logic signed [15:0] a_s;
    logic signed [15:0] x0_s, x1_s, x2_s, x3_s;
    logic signed [31:0] prod0, prod1, prod2, prod3;
    logic signed [31:0] sum0_next, sum1_next, sum2_next, sum3_next;

    always_comb begin
        a_s   = $signed(a_reg);
        x0_s  = $signed(x_vec_resp_rdata[15:0]);
        x1_s  = $signed(x_vec_resp_rdata[31:16]);
        x2_s  = $signed(x_vec_resp_rdata[47:32]);
        x3_s  = $signed(x_vec_resp_rdata[63:48]);
        prod0 = a_s * x0_s;
        prod1 = a_s * x1_s;
        prod2 = a_s * x2_s;
        prod3 = a_s * x3_s;
        sum0_next = $signed(sum0_reg) + prod0;
        sum1_next = $signed(sum1_reg) + prod1;
        sum2_next = $signed(sum2_reg) + prod2;
        sum3_next = $signed(sum3_reg) + prod3;
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            state         <= ST_IDLE;
            busy          <= 1'b0;
            r_reg         <= 8'd0;
            alpha_reg     <= 16'd0;
            beta_reg      <= 16'd0;
            row_start_reg <= 9'd0;
            k_reg         <= 10'd0;
            iter_cnt      <= 2'd0;
            b_pack_reg    <= 64'd0;
            c_reg         <= 8'd0;
            a_reg         <= 16'd0;
            sum0_reg      <= 32'd0;
            sum1_reg      <= 32'd0;
            sum2_reg      <= 32'd0;
            sum3_reg      <= 32'd0;
            out_y0_reg    <= 16'd0;
            out_y1_reg    <= 16'd0;
            out_y2_reg    <= 16'd0;
            out_y3_reg    <= 16'd0;
        end else begin
            if (input_accepted) begin
                busy          <= 1'b1;
                r_reg         <= in_row;
                alpha_reg     <= in_alpha;
                beta_reg      <= in_beta;
                sum0_reg      <= 32'd0;
                sum1_reg      <= 32'd0;
                sum2_reg      <= 32'd0;
                sum3_reg      <= 32'd0;
                iter_cnt      <= 2'd0;
                state         <= ST_REQ_RP;
            end else if (output_consumed) begin
                busy          <= 1'b0;
                state         <= ST_IDLE;
            end else begin
                case (state)
                    ST_REQ_RP: begin
                        if (row_ptr_req_ready) state <= ST_WAIT_RP;
                    end
                    ST_WAIT_RP: begin
                        if (row_ptr_resp_valid) begin
                            row_start_reg <= row_ptr_resp_rdata;
                            state         <= ST_REQ_B;
                        end
                    end
                    ST_REQ_B: begin
                        if (b_vec_req_ready) state <= ST_WAIT_B;
                    end
                    ST_WAIT_B: begin
                        if (b_vec_resp_valid) begin
                            b_pack_reg <= b_vec_resp_rdata;
                            // first entry: k = row_start (zero-extended
                            // to 10 bits — the col_idx / a_val arrays
                            // are 1024-deep so the address space is 10b)
                            k_reg      <= {1'b0, row_start_reg};
                            state      <= ST_LOOP_CHECK;
                        end
                    end
                    ST_LOOP_CHECK: begin
                        // iter_cnt counts entries already processed.
                        // After exactly NNZ_PER_ROW = 2 of them,
                        // finalise and emit.
                        if (iter_cnt == 2'(NNZ_PER_ROW)) begin
                            out_y0_reg <= final0[15:0];
                            out_y1_reg <= final1[15:0];
                            out_y2_reg <= final2[15:0];
                            out_y3_reg <= final3[15:0];
                            state      <= ST_OUT;
                        end else begin
                            state <= ST_REQ_COL;
                        end
                    end
                    ST_REQ_COL: begin
                        if (col_idx_req_ready) state <= ST_WAIT_COL;
                    end
                    ST_WAIT_COL: begin
                        if (col_idx_resp_valid) begin
                            c_reg <= col_idx_resp_rdata;
                            state <= ST_REQ_A;
                        end
                    end
                    ST_REQ_A: begin
                        if (a_val_req_ready) state <= ST_WAIT_A;
                    end
                    ST_WAIT_A: begin
                        if (a_val_resp_valid) begin
                            a_reg <= a_val_resp_rdata;
                            state <= ST_REQ_X;
                        end
                    end
                    ST_REQ_X: begin
                        if (x_vec_req_ready) state <= ST_WAIT_X;
                    end
                    ST_WAIT_X: begin
                        if (x_vec_resp_valid) begin
                            // Accumulate (32-bit wraparound) using the
                            // just-arrived x_vec_resp_rdata.
                            sum0_reg <= sum0_next;
                            sum1_reg <= sum1_next;
                            sum2_reg <= sum2_next;
                            sum3_reg <= sum3_next;
                            // advance to the second entry (10-bit add
                            // wraps as required by the spec)
                            k_reg    <= k_reg + 10'd1;
                            iter_cnt <= iter_cnt + 2'd1;
                            state    <= ST_LOOP_CHECK;
                        end
                    end
                    ST_OUT: begin
                        // hold output until consumed
                    end
                    default: state <= ST_IDLE;
                endcase
            end
        end
    end

endmodule
