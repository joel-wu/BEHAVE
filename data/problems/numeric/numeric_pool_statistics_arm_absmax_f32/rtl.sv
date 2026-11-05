module absmax_f32 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [3:0]  in_base_addr,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_result,
    output logic [3:0]  out_index,

    output logic        src_req_valid,
    output logic [3:0]  src_req_addr,
    input  logic        src_req_ready,
    input  logic        src_resp_valid,
    input  logic [31:0] src_resp_rdata
);

    typedef enum logic [1:0] {
        ST_IDLE     = 2'd0,
        ST_REQ_RESP = 2'd1,
        ST_OUT      = 2'd2
    } state_t;

    state_t      state;
    logic [3:0]  base_addr_reg;
    logic [4:0]  req_count;
    logic [4:0]  resp_count;
    logic [31:0] best_value_reg;
    logic [3:0]  best_index_reg;
    logic        req_pending;

    function automatic logic [31:0] fp32_abs_bits(input logic [31:0] x);
        begin
            fp32_abs_bits = {1'b0, x[30:0]};
        end
    endfunction

    function automatic logic fp32_is_nan(input logic [31:0] x);
        logic exp_all_ones;
        logic frac_nonzero;
        begin
            exp_all_ones = (x[30:23] == 8'hff);
            frac_nonzero = (x[22:0] != 23'h0);
            fp32_is_nan = exp_all_ones && frac_nonzero;
        end
    endfunction

    function automatic logic fp32_cmp_gt(input logic [31:0] a, input logic [31:0] b);
        logic a_nan;
        logic b_nan;
        logic [7:0] a_exp;
        logic [7:0] b_exp;
        logic [22:0] a_frac;
        logic [22:0] b_frac;
        begin
            a_nan  = fp32_is_nan(a);
            b_nan  = fp32_is_nan(b);
            a_exp  = a[30:23];
            b_exp  = b[30:23];
            a_frac = a[22:0];
            b_frac = b[22:0];

            if (a_nan || b_nan) begin
                fp32_cmp_gt = 1'b0;
            end else if ((a[30:0] == 31'd0) && (b[30:0] == 31'd0)) begin
                fp32_cmp_gt = 1'b0;
            end else if (a_exp > b_exp) begin
                fp32_cmp_gt = 1'b1;
            end else if (a_exp < b_exp) begin
                fp32_cmp_gt = 1'b0;
            end else if (a_frac > b_frac) begin
                fp32_cmp_gt = 1'b1;
            end else begin
                fp32_cmp_gt = 1'b0;
            end
        end
    endfunction

    wire output_consumed = out_valid && out_ready;
    wire input_accepted  = in_valid && in_ready;
    wire req_accepted    = src_req_valid && src_req_ready;
    wire [3:0] req_addr_next = base_addr_reg + req_count[3:0];
    wire [31:0] resp_abs = fp32_abs_bits(src_resp_rdata);
    wire take_resp = (resp_count != 5'd0) && fp32_cmp_gt(resp_abs, best_value_reg);

    always_comb begin
        in_ready = (state == ST_IDLE);

        out_valid  = (state == ST_OUT);
        out_result = best_value_reg;
        out_index  = best_index_reg;

        src_req_valid = (state == ST_REQ_RESP) && (req_count < 5'd16) && !req_pending;
        src_req_addr  = req_addr_next;
    end

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            state          <= ST_IDLE;
            base_addr_reg  <= 4'h0;
            req_count      <= 5'd0;
            resp_count     <= 5'd0;
            best_value_reg <= 32'h00000000;
            best_index_reg <= 4'h0;
            req_pending    <= 1'b0;
        end else begin
            case (state)
                ST_IDLE: begin
                    if (input_accepted) begin
                        state          <= ST_REQ_RESP;
                        base_addr_reg  <= in_base_addr;
                        req_count      <= 5'd0;
                        resp_count     <= 5'd0;
                        best_value_reg <= 32'h00000000;
                        best_index_reg <= 4'h0;
                        req_pending    <= 1'b0;
                    end
                end

                ST_REQ_RESP: begin
                    if (req_accepted) begin
                        req_count   <= req_count + 5'd1;
                        req_pending <= 1'b1;
                    end

                    if (src_resp_valid) begin
                        if (resp_count == 5'd0) begin
                            best_value_reg <= resp_abs;
                            best_index_reg <= 4'd0;
                        end else if (take_resp) begin
                            best_value_reg <= resp_abs;
                            best_index_reg <= resp_count[3:0];
                        end

                        resp_count  <= resp_count + 5'd1;
                        req_pending <= 1'b0;

                        if (resp_count == 5'd15) begin
                            state <= ST_OUT;
                        end
                    end
                end

                ST_OUT: begin
                    if (output_consumed) begin
                        state         <= ST_IDLE;
                        base_addr_reg <= 4'h0;
                        req_count     <= 5'd0;
                        resp_count    <= 5'd0;
                        req_pending   <= 1'b0;
                    end
                end
            endcase
        end
    end

endmodule
