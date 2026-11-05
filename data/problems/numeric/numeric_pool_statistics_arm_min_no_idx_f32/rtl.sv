module vector_min_no_idx_fp32 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [3:0]  in_base_addr,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_min_value,

    output logic        src_req_valid,
    output logic [3:0]  src_req_addr,
    input  logic        src_req_ready,
    input  logic        src_resp_valid,
    input  logic [31:0] src_resp_rdata
);

    typedef enum logic [1:0] {
        ST_IDLE      = 2'd0,
        ST_REQ       = 2'd1,
        ST_WAIT_RESP = 2'd2,
        ST_OUT       = 2'd3
    } state_t;

    state_t       state;
    logic [3:0]   base_addr_reg;
    logic [4:0]   elem_count;
    logic [31:0]  min_value_reg;
    logic [31:0]  out_min_value_reg;

    function automatic logic is_nan(input logic [31:0] x);
        logic [7:0] expf;
        logic [22:0] fracf;
        begin
            expf = x[30:23];
            fracf = x[22:0];
            is_nan = (expf == 8'hFF) && (fracf != 23'h0);
        end
    endfunction

    function automatic logic is_zero(input logic [31:0] x);
        begin
            is_zero = (x[30:0] == 31'd0);
        end
    endfunction

    function automatic logic fp32_gt_ordered(
        input logic [31:0] a,
        input logic [31:0] b
    );
        logic a_nan;
        logic b_nan;
        logic a_sign;
        logic b_sign;
        logic [30:0] a_mag;
        logic [30:0] b_mag;
        begin
            a_nan = is_nan(a);
            b_nan = is_nan(b);
            a_sign = a[31];
            b_sign = b[31];
            a_mag = a[30:0];
            b_mag = b[30:0];

            fp32_gt_ordered = 1'b0;

            if (a_nan || b_nan) begin
                fp32_gt_ordered = 1'b0;
            end else if (is_zero(a) && is_zero(b)) begin
                fp32_gt_ordered = 1'b0;
            end else if (a_sign != b_sign) begin
                if (a_sign == 1'b0) begin
                    fp32_gt_ordered = 1'b1;
                end else begin
                    fp32_gt_ordered = 1'b0;
                end
            end else if (a_sign == 1'b0) begin
                fp32_gt_ordered = (a_mag > b_mag);
            end else begin
                fp32_gt_ordered = (a_mag < b_mag);
            end
        end
    endfunction

    wire input_accepted;
    wire output_consumed;
    wire req_accepted;
    wire last_response;

    assign input_accepted  = in_valid && in_ready;
    assign output_consumed = out_valid && out_ready;
    assign req_accepted    = src_req_valid && src_req_ready;
    assign last_response   = (elem_count == 5'd15);

    always_comb begin
        in_ready = 1'b0;
        out_valid = 1'b0;
        out_min_value = out_min_value_reg;
        src_req_valid = 1'b0;
        src_req_addr = 4'h0;

        unique case (state)
            ST_IDLE: begin
                in_ready = 1'b1;
            end

            ST_REQ: begin
                src_req_valid = 1'b1;
                src_req_addr = base_addr_reg + elem_count[3:0];
            end

            ST_WAIT_RESP: begin
            end

            ST_OUT: begin
                out_valid = 1'b1;
            end
        endcase
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            state             <= ST_IDLE;
            base_addr_reg     <= 4'h0;
            elem_count        <= 5'd0;
            min_value_reg     <= 32'h7F7FFFFF;
            out_min_value_reg <= 32'h00000000;
        end else begin
            unique case (state)
                ST_IDLE: begin
                    if (input_accepted) begin
                        base_addr_reg <= in_base_addr;
                        elem_count <= 5'd0;
                        min_value_reg <= 32'h7F7FFFFF;
                        state <= ST_REQ;
                    end
                end

                ST_REQ: begin
                    if (req_accepted) begin
                        state <= ST_WAIT_RESP;
                    end
                end

                ST_WAIT_RESP: begin
                    if (src_resp_valid) begin
                        if (fp32_gt_ordered(min_value_reg, src_resp_rdata)) begin
                            min_value_reg <= src_resp_rdata;
                            if (last_response) begin
                                out_min_value_reg <= src_resp_rdata;
                                state <= ST_OUT;
                            end else begin
                                elem_count <= elem_count + 5'd1;
                                state <= ST_REQ;
                            end
                        end else begin
                            if (last_response) begin
                                out_min_value_reg <= min_value_reg;
                                state <= ST_OUT;
                            end else begin
                                elem_count <= elem_count + 5'd1;
                                state <= ST_REQ;
                            end
                        end
                    end
                end

                ST_OUT: begin
                    if (output_consumed) begin
                        state <= ST_IDLE;
                    end
                end
            endcase
        end
    end

endmodule
