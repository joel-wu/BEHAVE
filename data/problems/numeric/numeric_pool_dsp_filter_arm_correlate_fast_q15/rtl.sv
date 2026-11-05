module q15_correlation_fast4 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [10:0]  in_srcA_addr,
    input  logic [10:0]  in_srcB_addr,
    input  logic [10:0]  in_dst_addr,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [2:0]   out_dst_len,

    output logic         data_req_valid,
    output logic         data_req_we,
    output logic [10:0]  data_req_addr,
    output logic [15:0]  data_req_wdata,
    input  logic         data_req_ready,
    input  logic         data_resp_valid,
    input  logic [15:0]  data_resp_rdata
);

    typedef enum logic [2:0] {
        ST_IDLE       = 3'd0,
        ST_READA_REQ  = 3'd1,
        ST_READA_RESP = 3'd2,
        ST_READB_REQ  = 3'd3,
        ST_READB_RESP = 3'd4,
        ST_COMPUTE    = 3'd5,
        ST_WRITE_REQ  = 3'd6,
        ST_OUT        = 3'd7
    } state_t;

    state_t state;

    logic [10:0] srcA_base;
    logic [10:0] srcB_base;
    logic [10:0] dst_base;
    logic [2:0]  idx;

    logic signed [15:0] a0;
    logic signed [15:0] a1;
    logic signed [15:0] a2;
    logic signed [15:0] a3;
    logic signed [15:0] b0;
    logic signed [15:0] b1;
    logic signed [15:0] b2;
    logic signed [15:0] b3;

    logic [15:0] r0_u;
    logic [15:0] r1_u;
    logic [15:0] r2_u;
    logic [15:0] r3_u;
    logic [15:0] r4_u;
    logic [15:0] r5_u;
    logic [15:0] r6_u;

    logic [2:0] pending_dst_len;

    wire input_accepted  = in_valid & in_ready;
    wire output_consumed = out_valid & out_ready;

    function automatic logic signed [31:0] wrap32(input logic signed [63:0] value);
        begin
            wrap32 = value[31:0];
        end
    endfunction

    function automatic logic signed [15:0] sat_q15_from32(input logic signed [31:0] value);
        logic signed [31:0] shifted;
        begin
            shifted = value >>> 15;
            sat_q15_from32 = 16'sd0;
            if (shifted > 32'sd32767) begin
                sat_q15_from32 = 16'sd32767;
            end else if (shifted < -32'sd32768) begin
                sat_q15_from32 = -16'sd32768;
            end else begin
                sat_q15_from32 = shifted[15:0];
            end
        end
    endfunction

    assign in_ready    = (state == ST_IDLE);
    assign out_valid   = (state == ST_OUT);
    assign out_dst_len = pending_dst_len;

    always_comb begin
        data_req_valid = 1'b0;
        data_req_we    = 1'b0;
        data_req_addr  = 11'd0;
        data_req_wdata = 16'd0;

        case (state)
            ST_READA_REQ: begin
                data_req_valid = 1'b1;
                data_req_we    = 1'b0;
                data_req_addr  = srcA_base + {8'd0, idx};
            end

            ST_READB_REQ: begin
                data_req_valid = 1'b1;
                data_req_we    = 1'b0;
                data_req_addr  = srcB_base + {8'd0, idx};
            end

            ST_WRITE_REQ: begin
                data_req_valid = 1'b1;
                data_req_we    = 1'b1;
                data_req_addr  = dst_base + {8'd0, idx};
                case (idx)
                    3'd0: data_req_wdata = r0_u;
                    3'd1: data_req_wdata = r1_u;
                    3'd2: data_req_wdata = r2_u;
                    3'd3: data_req_wdata = r3_u;
                    3'd4: data_req_wdata = r4_u;
                    3'd5: data_req_wdata = r5_u;
                    3'd6: data_req_wdata = r6_u;
                endcase
            end

            ST_IDLE: begin
            end

            ST_READA_RESP: begin
            end

            ST_READB_RESP: begin
            end

            ST_COMPUTE: begin
            end

            ST_OUT: begin
            end
        endcase
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            state           <= ST_IDLE;
            srcA_base       <= 11'd0;
            srcB_base       <= 11'd0;
            dst_base        <= 11'd0;
            idx             <= 3'd0;
            a0              <= 16'sd0;
            a1              <= 16'sd0;
            a2              <= 16'sd0;
            a3              <= 16'sd0;
            b0              <= 16'sd0;
            b1              <= 16'sd0;
            b2              <= 16'sd0;
            b3              <= 16'sd0;
            r0_u            <= 16'd0;
            r1_u            <= 16'd0;
            r2_u            <= 16'd0;
            r3_u            <= 16'd0;
            r4_u            <= 16'd0;
            r5_u            <= 16'd0;
            r6_u            <= 16'd0;
            pending_dst_len <= 3'd0;
        end else begin
            case (state)
                ST_IDLE: begin
                    if (input_accepted) begin
                        srcA_base <= in_srcA_addr;
                        srcB_base <= in_srcB_addr;
                        dst_base  <= in_dst_addr;
                        idx       <= 3'd0;
                        a0        <= 16'sd0;
                        a1        <= 16'sd0;
                        a2        <= 16'sd0;
                        a3        <= 16'sd0;
                        b0        <= 16'sd0;
                        b1        <= 16'sd0;
                        b2        <= 16'sd0;
                        b3        <= 16'sd0;
                        r0_u      <= 16'd0;
                        r1_u      <= 16'd0;
                        r2_u      <= 16'd0;
                        r3_u      <= 16'd0;
                        r4_u      <= 16'd0;
                        r5_u      <= 16'd0;
                        r6_u      <= 16'd0;
                        if ((in_srcA_addr <= 11'd2044) && (in_srcB_addr <= 11'd2044) && (in_dst_addr <= 11'd2041)) begin
                            pending_dst_len <= 3'd0;
                            state           <= ST_READA_REQ;
                        end else begin
                            pending_dst_len <= 3'd0;
                            state           <= ST_OUT;
                        end
                    end
                end

                ST_READA_REQ: begin
                    if (data_req_ready) begin
                        state <= ST_READA_RESP;
                    end
                end

                ST_READA_RESP: begin
                    if (data_resp_valid) begin
                        case (idx)
                            3'd0: a0 <= data_resp_rdata;
                            3'd1: a1 <= data_resp_rdata;
                            3'd2: a2 <= data_resp_rdata;
                            3'd3: a3 <= data_resp_rdata;
                        endcase

                        if (idx == 3'd3) begin
                            idx   <= 3'd0;
                            state <= ST_READB_REQ;
                        end else begin
                            idx   <= idx + 3'd1;
                            state <= ST_READA_REQ;
                        end
                    end
                end

                ST_READB_REQ: begin
                    if (data_req_ready) begin
                        state <= ST_READB_RESP;
                    end
                end

                ST_READB_RESP: begin
                    if (data_resp_valid) begin
                        case (idx)
                            3'd0: b0 <= data_resp_rdata;
                            3'd1: b1 <= data_resp_rdata;
                            3'd2: b2 <= data_resp_rdata;
                            3'd3: b3 <= data_resp_rdata;
                        endcase

                        if (idx == 3'd3) begin
                            state <= ST_COMPUTE;
                        end else begin
                            idx   <= idx + 3'd1;
                            state <= ST_READB_REQ;
                        end
                    end
                end

                ST_COMPUTE: begin
                    r0_u <= sat_q15_from32(wrap32($signed(a0) * $signed(b3)));
                    r1_u <= sat_q15_from32(wrap32(($signed(a0) * $signed(b2)) + ($signed(a1) * $signed(b3))));
                    r2_u <= sat_q15_from32(
                                wrap32(
                                    wrap32(($signed(a0) * $signed(b1)) + ($signed(a1) * $signed(b2))) +
                                    ($signed(a2) * $signed(b3))
                                )
                            );
                    r3_u <= sat_q15_from32(
                                wrap32(
                                    wrap32(
                                        wrap32(($signed(a0) * $signed(b0)) + ($signed(a1) * $signed(b1))) +
                                        ($signed(a2) * $signed(b2))
                                    ) +
                                    ($signed(a3) * $signed(b3))
                                )
                            );
                    r4_u <= sat_q15_from32(
                                wrap32(
                                    wrap32(($signed(a1) * $signed(b0)) + ($signed(a2) * $signed(b1))) +
                                    ($signed(a3) * $signed(b2))
                                )
                            );
                    r5_u <= sat_q15_from32(wrap32(($signed(a2) * $signed(b0)) + ($signed(a3) * $signed(b1))));
                    r6_u <= sat_q15_from32(wrap32($signed(a3) * $signed(b0)));
                    idx  <= 3'd0;
                    state <= ST_WRITE_REQ;
                end

                ST_WRITE_REQ: begin
                    if (data_req_ready) begin
                        if (idx == 3'd6) begin
                            pending_dst_len <= 3'd7;
                            state           <= ST_OUT;
                        end else begin
                            idx   <= idx + 3'd1;
                            state <= ST_WRITE_REQ;
                        end
                    end
                end

                ST_OUT: begin
                    if (output_consumed) begin
                        pending_dst_len <= 3'd0;
                        state           <= ST_IDLE;
                    end
                end
            endcase
        end
    end

endmodule