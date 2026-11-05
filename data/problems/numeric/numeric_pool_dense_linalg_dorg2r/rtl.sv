// One reverse-order step of the unblocked QR orthogonal-factor construction
// (DORG2R). For reflector index i it applies H(i) = I - tau*v*v^T from the left
// to the trailing submatrix, scales the reflector tail of column i by -tau,
// sets the diagonal to 1 - tau, and clears the upper part of column i. The 8x8
// matrix and the tau vector live in external memories accessed over req/resp
// ports; all arithmetic is signed Q16.16 with mul_q16(x,y) = (x*y) >>> 16.
//
// Column i is only written in steps 4/5/6, never in step 3, so every memory
// read returns the pre-update ("old_a") value the reference model snapshots —
// no local matrix copy is needed.
module householder_q_column_step (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [3:0]   in_m,
    input  logic [3:0]   in_n,
    input  logic [2:0]   in_i,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_done,

    output logic         a_req_valid,
    output logic         a_req_we,
    output logic [5:0]   a_req_addr,
    output logic [31:0]  a_req_wdata,
    input  logic         a_req_ready,
    input  logic         a_resp_valid,
    input  logic [31:0]  a_resp_rdata,

    output logic         tau_req_valid,
    output logic [2:0]   tau_req_addr,
    input  logic         tau_req_ready,
    input  logic         tau_resp_valid,
    input  logic [31:0]  tau_resp_rdata
);

    typedef enum logic [4:0] {
        S_IDLE,
        S_TAU_REQ, S_TAU_RESP,
        S_DOT_VREQ, S_DOT_VRESP, S_DOT_AREQ, S_DOT_ARESP,
        S_UPD_VREQ, S_UPD_VRESP, S_UPD_AREQ, S_UPD_ARESP, S_UPD_WR,
        S_S4_RREQ, S_S4_RRESP, S_S4_WR,
        S_S5_WR,
        S_S6_WR,
        S_DONE
    } state_t;

    state_t state;

    logic [3:0] m_reg, n_reg;
    logic [2:0] i_reg;
    logic       valid_reg;
    logic [2:0] r_cnt, c_cnt;

    logic signed [31:0] tau_i;
    logic signed [31:0] dot;
    logic signed [31:0] w_val;
    logic signed [31:0] v_tmp;     // current A[r][i]
    logic signed [31:0] a_tmp;     // current A[r][c]

    // Q16.16 multiply: (x*y) >>> 16, truncated to 32 bits.
    function automatic logic signed [31:0] mul_q16(input logic signed [31:0] x,
                                                   input logic signed [31:0] y);
        logic signed [63:0] p;
        begin
            p = $signed(x) * $signed(y);
            mul_q16 = p[47:16];
        end
    endfunction

    // Row-major 8-wide address r*8 + c (c is 3 bits) == {r, c}.
    function automatic logic [5:0] aidx(input logic [2:0] r, input logic [2:0] c);
        aidx = {r, c};
    endfunction

    wire input_accepted  = in_valid & in_ready;
    wire output_consumed = out_valid & out_ready;
    wire is_valid_tx = (in_m >= 3'd1) && (in_n >= 3'd1) &&
                       (in_m <= 4'd8) && (in_n <= in_m) && (in_i < in_n);

    assign in_ready  = (state == S_IDLE);
    assign out_valid = (state == S_DONE);
    assign out_done  = (state == S_DONE) ? 32'd1 : 32'd0;

    // ── memory request drive (combinational, held until the handshake) ──
    always_comb begin
        a_req_valid   = 1'b0;
        a_req_we      = 1'b0;
        a_req_addr    = 6'd0;
        a_req_wdata   = 32'd0;
        tau_req_valid = 1'b0;
        tau_req_addr  = 3'd0;
        // NOTE: a read asserts req_valid ONLY in its REQ state (one request),
        // never in the RESP state — otherwise the memory server would queue a
        // second read of the same address and later serve that stale response
        // when the FSM has moved on to the next address.
        unique case (state)
            S_TAU_REQ: begin
                tau_req_valid = 1'b1;
                tau_req_addr  = i_reg;
            end
            S_DOT_VREQ: begin
                a_req_valid = 1'b1;
                a_req_addr  = aidx(r_cnt, i_reg);
            end
            S_DOT_AREQ: begin
                a_req_valid = 1'b1;
                a_req_addr  = aidx(r_cnt, c_cnt);
            end
            S_UPD_VREQ: begin
                a_req_valid = 1'b1;
                a_req_addr  = aidx(r_cnt, i_reg);
            end
            S_UPD_AREQ: begin
                a_req_valid = 1'b1;
                a_req_addr  = aidx(r_cnt, c_cnt);
            end
            S_UPD_WR: begin
                a_req_valid = 1'b1;
                a_req_we    = 1'b1;
                a_req_addr  = aidx(r_cnt, c_cnt);
                a_req_wdata = a_tmp - mul_q16(v_tmp, w_val);
            end
            S_S4_RREQ: begin
                a_req_valid = 1'b1;
                a_req_addr  = aidx(r_cnt, i_reg);
            end
            S_S4_WR: begin
                a_req_valid = 1'b1;
                a_req_we    = 1'b1;
                a_req_addr  = aidx(r_cnt, i_reg);
                a_req_wdata = mul_q16(v_tmp, -tau_i);
            end
            S_S5_WR: begin
                a_req_valid = 1'b1;
                a_req_we    = 1'b1;
                a_req_addr  = aidx(i_reg, i_reg);
                a_req_wdata = 32'h0001_0000 - tau_i;
            end
            S_S6_WR: begin
                a_req_valid = 1'b1;
                a_req_we    = 1'b1;
                a_req_addr  = aidx(r_cnt, i_reg);
                a_req_wdata = 32'd0;
            end
            default: ;
        endcase
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            state <= S_IDLE;
        end else begin
            unique case (state)
                S_IDLE: if (input_accepted) begin
                    m_reg     <= in_m;
                    n_reg     <= in_n;
                    i_reg     <= in_i;
                    valid_reg <= is_valid_tx;
                    state     <= is_valid_tx ? S_TAU_REQ : S_DONE;
                end

                // step 1: tau_i = tau[i]
                S_TAU_REQ:  if (tau_req_ready) state <= S_TAU_RESP;
                S_TAU_RESP: if (tau_resp_valid) begin
                    tau_i <= $signed(tau_resp_rdata);
                    c_cnt <= i_reg + 3'd1;          // step 3 starts at c = i+1
                    if (({1'b0, i_reg} + 4'd1) < n_reg) begin
                        dot   <= 32'sd0;
                        r_cnt <= i_reg;
                        state <= S_DOT_VREQ;
                    end else begin
                        r_cnt <= i_reg + 3'd1;      // step 4 starts at r = i+1
                        state <= (({1'b0, i_reg} + 4'd1) < m_reg) ? S_S4_RREQ : S_S5_WR;
                    end
                end

                // step 3 dot: dot += mul_q16(A[r][i], A[r][c])
                S_DOT_VREQ:  if (a_req_ready) state <= S_DOT_VRESP;
                S_DOT_VRESP: if (a_resp_valid) begin
                    v_tmp <= (r_cnt == i_reg) ? 32'sd65536 : $signed(a_resp_rdata);
                    state <= S_DOT_AREQ;
                end
                S_DOT_AREQ:  if (a_req_ready) state <= S_DOT_ARESP;
                S_DOT_ARESP: if (a_resp_valid) begin
                    dot <= dot + mul_q16(v_tmp, $signed(a_resp_rdata));
                    if ({1'b0, r_cnt} + 4'd1 < m_reg) begin
                        r_cnt <= r_cnt + 3'd1;
                        state <= S_DOT_VREQ;
                    end else begin
                        // dot complete -> w = mul_q16(tau_i, dot); begin update
                        w_val <= mul_q16(tau_i,
                                         dot + mul_q16(v_tmp, $signed(a_resp_rdata)));
                        r_cnt <= i_reg;
                        state <= S_UPD_VREQ;
                    end
                end

                // step 3 update: A[r][c] = A[r][c] - mul_q16(A[r][i], w)
                S_UPD_VREQ:  if (a_req_ready) state <= S_UPD_VRESP;
                S_UPD_VRESP: if (a_resp_valid) begin
                    v_tmp <= (r_cnt == i_reg) ? 32'sd65536 : $signed(a_resp_rdata);
                    state <= S_UPD_AREQ;
                end
                S_UPD_AREQ:  if (a_req_ready) state <= S_UPD_ARESP;
                S_UPD_ARESP: if (a_resp_valid) begin
                    a_tmp <= $signed(a_resp_rdata);
                    state <= S_UPD_WR;
                end
                S_UPD_WR: if (a_req_ready) begin
                    if ({1'b0, r_cnt} + 4'd1 < m_reg) begin
                        r_cnt <= r_cnt + 3'd1;
                        state <= S_UPD_VREQ;
                    end else if ({1'b0, c_cnt} + 4'd1 < n_reg) begin
                        c_cnt <= c_cnt + 3'd1;
                        dot   <= 32'sd0;
                        r_cnt <= i_reg;
                        state <= S_DOT_VREQ;
                    end else begin
                        r_cnt <= i_reg + 3'd1;
                        state <= (({1'b0, i_reg} + 4'd1) < m_reg) ? S_S4_RREQ : S_S5_WR;
                    end
                end

                // step 4: A[r][i] = mul_q16(A[r][i], -tau_i) for r = i+1..m-1
                S_S4_RREQ:  if (a_req_ready) state <= S_S4_RRESP;
                S_S4_RRESP: if (a_resp_valid) begin
                    v_tmp <= $signed(a_resp_rdata);
                    state <= S_S4_WR;
                end
                S_S4_WR: if (a_req_ready) begin
                    if ({1'b0, r_cnt} + 4'd1 < m_reg) begin
                        r_cnt <= r_cnt + 3'd1;
                        state <= S_S4_RREQ;
                    end else begin
                        state <= S_S5_WR;
                    end
                end

                // step 5: A[i][i] = 1.0 - tau_i
                S_S5_WR: if (a_req_ready) begin
                    if (i_reg != 3'd0) begin
                        r_cnt <= 3'd0;
                        state <= S_S6_WR;
                    end else begin
                        state <= S_DONE;
                    end
                end

                // step 6: A[r][i] = 0 for r = 0..i-1
                S_S6_WR: if (a_req_ready) begin
                    if ({1'b0, r_cnt} + 4'd1 < i_reg) begin
                        r_cnt <= r_cnt + 3'd1;
                        state <= S_S6_WR;
                    end else begin
                        state <= S_DONE;
                    end
                end

                S_DONE: if (output_consumed) state <= S_IDLE;

                default: state <= S_IDLE;
            endcase
        end
    end

endmodule
