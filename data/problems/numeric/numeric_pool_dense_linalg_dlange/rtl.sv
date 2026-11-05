module matrix_norm_dlange (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [1:0]  in_norm,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [63:0] out_value,

    output logic        a_req_valid,
    output logic [3:0]  a_req_addr,
    input  logic        a_req_ready,
    input  logic        a_resp_valid,
    input  logic [31:0] a_resp_rdata
);

    typedef enum logic [2:0] {
        ST_IDLE  = 3'd0,
        ST_REQ   = 3'd1,
        ST_RESP  = 3'd2,
        ST_ISQRT = 3'd3,
        ST_OUT   = 3'd4
    } state_t;

    state_t state;

    logic [1:0] norm_reg;
    logic [3:0] addr_reg;
    logic [4:0] count_reg;

    logic [31:0] abs32;
    logic [33:0] abs34;
    logic [31:0] maxabs_next;

    logic [33:0] colsum0_reg, colsum1_reg, colsum2_reg, colsum3_reg;
    logic [33:0] rowsum0_reg, rowsum1_reg, rowsum2_reg, rowsum3_reg;
    logic [33:0] colsum0_next, colsum1_next, colsum2_next, colsum3_next;
    logic [33:0] rowsum0_next, rowsum1_next, rowsum2_next, rowsum3_next;
    logic [33:0] maxcol01;
    logic [33:0] maxcol23;
    logic [33:0] maxcol_all;
    logic [33:0] maxrow01;
    logic [33:0] maxrow23;
    logic [33:0] maxrow_all;

    logic [66:0] sumsq_reg;
    logic signed [32:0] x_signed;
    logic [65:0] square66;
    logic [66:0] sumsq_next;

    logic [33:0] root_reg;
    logic [67:0] rem_reg;
    logic [5:0]  isqrt_idx_reg;
    logic [1:0]  pair_bits;
    logic [67:0] rem_shifted;
    logic [67:0] cand_val;
    logic        take_bit;
    logic [67:0] rem_next;
    logic [33:0] root_next;

    logic [63:0] out_value_reg;

    logic [1:0] row_idx;
    logic [1:0] col_idx;

    assign in_ready   = (state == ST_IDLE);
    assign out_valid  = (state == ST_OUT);
    assign out_value  = out_value_reg;

    assign a_req_valid = (state == ST_REQ);
    assign a_req_addr  = addr_reg;

    assign row_idx = addr_reg[1:0];
    assign col_idx = addr_reg[3:2];

    always_comb begin
        if (a_resp_rdata[31]) begin
            abs32 = (~a_resp_rdata) + 32'd1;
        end else begin
            abs32 = a_resp_rdata;
        end
    end

    assign abs34 = {2'b00, abs32};

    always_comb begin
        if (a_resp_rdata[31]) begin
            x_signed = -$signed({1'b0, abs32});
        end else begin
            x_signed = $signed({1'b0, a_resp_rdata});
        end
    end

    assign square66 = x_signed * x_signed;
    assign sumsq_next = sumsq_reg + square66;
    assign maxabs_next = (maxabs_reg < abs32) ? abs32 : maxabs_reg;

    always_comb begin
        colsum0_next = colsum0_reg;
        colsum1_next = colsum1_reg;
        colsum2_next = colsum2_reg;
        colsum3_next = colsum3_reg;

        case (col_idx)
            2'd0: colsum0_next = colsum0_reg + abs34;
            2'd1: colsum1_next = colsum1_reg + abs34;
            2'd2: colsum2_next = colsum2_reg + abs34;
            2'd3: colsum3_next = colsum3_reg + abs34;
        endcase
    end

    always_comb begin
        rowsum0_next = rowsum0_reg;
        rowsum1_next = rowsum1_reg;
        rowsum2_next = rowsum2_reg;
        rowsum3_next = rowsum3_reg;

        case (row_idx)
            2'd0: rowsum0_next = rowsum0_reg + abs34;
            2'd1: rowsum1_next = rowsum1_reg + abs34;
            2'd2: rowsum2_next = rowsum2_reg + abs34;
            2'd3: rowsum3_next = rowsum3_reg + abs34;
        endcase
    end

    always_comb begin
        if (colsum0_next < colsum1_next) begin
            maxcol01 = colsum1_next;
        end else begin
            maxcol01 = colsum0_next;
        end

        if (colsum2_next < colsum3_next) begin
            maxcol23 = colsum3_next;
        end else begin
            maxcol23 = colsum2_next;
        end

        if (maxcol01 < maxcol23) begin
            maxcol_all = maxcol23;
        end else begin
            maxcol_all = maxcol01;
        end

        if (rowsum0_next < rowsum1_next) begin
            maxrow01 = rowsum1_next;
        end else begin
            maxrow01 = rowsum0_next;
        end

        if (rowsum2_next < rowsum3_next) begin
            maxrow23 = rowsum3_next;
        end else begin
            maxrow23 = rowsum2_next;
        end

        if (maxrow01 < maxrow23) begin
            maxrow_all = maxrow23;
        end else begin
            maxrow_all = maxrow01;
        end
    end

    assign pair_bits   = 2'(sumsq_reg >> (isqrt_idx_reg * 2));
    assign rem_shifted = (rem_reg << 2) | {66'd0, pair_bits};
    assign cand_val    = {32'd0, root_reg, 2'b01};
    assign take_bit    = (rem_shifted >= cand_val);
    assign rem_next    = take_bit ? (rem_shifted - cand_val) : rem_shifted;
    assign root_next   = take_bit ? ((root_reg << 1) | 34'd1) : (root_reg << 1);

    logic [31:0] maxabs_reg;
    always_ff @(posedge clk) begin
        if (rst) begin
            state         <= ST_IDLE;
            norm_reg      <= 2'd0;
            addr_reg      <= 4'd0;
            count_reg     <= 5'd0;
            maxabs_reg    <= 32'd0;
            colsum0_reg   <= 34'd0;
            colsum1_reg   <= 34'd0;
            colsum2_reg   <= 34'd0;
            colsum3_reg   <= 34'd0;
            rowsum0_reg   <= 34'd0;
            rowsum1_reg   <= 34'd0;
            rowsum2_reg   <= 34'd0;
            rowsum3_reg   <= 34'd0;
            sumsq_reg     <= 67'd0;
            root_reg      <= 34'd0;
            rem_reg       <= 68'd0;
            isqrt_idx_reg <= 6'd0;
            out_value_reg <= 64'd0;
        end else begin
            case (state)
                ST_IDLE: begin
                    if (in_valid && in_ready) begin
                        norm_reg      <= in_norm;
                        addr_reg      <= 4'd0;
                        count_reg     <= 5'd0;
                        maxabs_reg    <= 32'd0;
                        colsum0_reg   <= 34'd0;
                        colsum1_reg   <= 34'd0;
                        colsum2_reg   <= 34'd0;
                        colsum3_reg   <= 34'd0;
                        rowsum0_reg   <= 34'd0;
                        rowsum1_reg   <= 34'd0;
                        rowsum2_reg   <= 34'd0;
                        rowsum3_reg   <= 34'd0;
                        sumsq_reg     <= 67'd0;
                        root_reg      <= 34'd0;
                        rem_reg       <= 68'd0;
                        isqrt_idx_reg <= 6'd33;
                        out_value_reg <= 64'd0;
                        state         <= ST_REQ;
                    end
                end

                ST_REQ: begin
                    if (a_req_valid && a_req_ready) begin
                        state <= ST_RESP;
                    end
                end

                ST_RESP: begin
                    if (a_resp_valid) begin
                        maxabs_reg  <= maxabs_next;
                        colsum0_reg <= colsum0_next;
                        colsum1_reg <= colsum1_next;
                        colsum2_reg <= colsum2_next;
                        colsum3_reg <= colsum3_next;
                        rowsum0_reg <= rowsum0_next;
                        rowsum1_reg <= rowsum1_next;
                        rowsum2_reg <= rowsum2_next;
                        rowsum3_reg <= rowsum3_next;
                        sumsq_reg   <= sumsq_next;

                        if (count_reg == 5'd15) begin
                            if (norm_reg == 2'b11) begin
                                root_reg      <= 34'd0;
                                rem_reg       <= 68'd0;
                                isqrt_idx_reg <= 6'd33;
                                sumsq_reg     <= sumsq_next;
                                state         <= ST_ISQRT;
                            end else begin
                                case (norm_reg)
                                    2'b00: out_value_reg <= {32'd0, maxabs_next};
                                    2'b01: out_value_reg <= {30'd0, maxcol_all};
                                    2'b10: out_value_reg <= {30'd0, maxrow_all};
                                    2'b11: out_value_reg <= {30'd0, root_reg};
                                endcase
                                state <= ST_OUT;
                            end
                        end else begin
                            count_reg <= count_reg + 5'd1;
                            addr_reg  <= addr_reg + 4'd1;
                            state     <= ST_REQ;
                        end
                    end
                end

                ST_ISQRT: begin
                    rem_reg  <= rem_next;
                    root_reg <= root_next;
                    if (isqrt_idx_reg == 6'd0) begin
                        out_value_reg <= {30'd0, root_next};
                        state         <= ST_OUT;
                    end else begin
                        isqrt_idx_reg <= isqrt_idx_reg - 6'd1;
                    end
                end

                ST_OUT: begin
                    if (out_valid && out_ready) begin
                        state <= ST_IDLE;
                    end
                end
            endcase
        end
    end

endmodule
