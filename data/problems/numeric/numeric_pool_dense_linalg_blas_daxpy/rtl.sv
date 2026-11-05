module daxpy_element_update (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [7:0]   in_n,
    input  logic [7:0]   in_k,
    input  logic [63:0]  in_da,
    input  logic [7:0]   in_incx,
    input  logic [7:0]   in_incy,

    output logic         out_valid,
    input  logic         out_ready,
    output logic         out_done,

    output logic         dx_req_valid,
    output logic [7:0]   dx_req_addr,
    input  logic         dx_req_ready,
    input  logic         dx_resp_valid,
    input  logic [63:0]  dx_resp_rdata,

    output logic         dy_req_valid,
    output logic         dy_req_we,
    output logic [7:0]   dy_req_addr,
    output logic [63:0]  dy_req_wdata,
    input  logic         dy_req_ready,
    input  logic         dy_resp_valid,
    input  logic [63:0]  dy_resp_rdata
);

    typedef enum logic [2:0] {
        S_IDLE,
        S_DX_REQ,
        S_DX_WAIT,
        S_DY_RD_REQ,
        S_DY_RD_WAIT,
        S_DY_WR_REQ,
        S_OUT
    } state_t;

    state_t state;

    logic [7:0]  n_reg;
    logic [7:0]  k_reg;
    logic [63:0] da_reg;
    logic signed [7:0] incx_reg;
    logic signed [7:0] incy_reg;

    logic [7:0]  dx_addr_reg;
    logic [7:0]  dy_addr_reg;
    logic [63:0] x_bits_reg;
    logic [63:0] sum_bits_reg;

    logic signed [15:0] n_s16;
    logic signed [15:0] k_s16;
    logic signed [15:0] incx_s16;
    logic signed [15:0] incy_s16;
    logic signed [15:0] one_minus_n;
    logic signed [15:0] ix0_calc;
    logic signed [15:0] iy0_calc;
    logic signed [23:0] ix_calc;
    logic signed [23:0] iy_calc;
    logic signed [63:0] da_signed;
    logic signed [63:0] x_signed;
    logic signed [63:0] y_signed;
    logic signed [127:0] product_full;
    logic [63:0] product_bits;
    logic signed [63:0] p_signed;
    logic signed [64:0] sum_full;

    wire input_accepted  = in_valid & in_ready;
    wire output_consumed = out_valid & out_ready;

    always_comb begin
        n_s16 = $signed({1'b0, n_reg});
        k_s16 = $signed({1'b0, k_reg});
        incx_s16 = incx_reg;
        incy_s16 = incy_reg;
        one_minus_n = 16'sd1 - n_s16;

        if (incx_reg >= 0) begin
            ix0_calc = 16'sd0;
        end else begin
            ix0_calc = one_minus_n * incx_s16;
        end

        if (incy_reg >= 0) begin
            iy0_calc = 16'sd0;
        end else begin
            iy0_calc = one_minus_n * incy_s16;
        end

        ix_calc = ix0_calc + (k_s16 * incx_s16);
        iy_calc = iy0_calc + (k_s16 * incy_s16);

        da_signed = da_reg;
        x_signed = x_bits_reg;
        y_signed = dy_resp_rdata;
        product_full = da_signed * x_signed;
        product_bits = product_full[63:0];
        p_signed = product_bits;
        sum_full = y_signed + p_signed;
    end

    assign in_ready = (state == S_IDLE);

    assign out_valid = (state == S_OUT);
    assign out_done  = (state == S_OUT);

    assign dx_req_valid = (state == S_DX_REQ);
    assign dx_req_addr  = dx_addr_reg;

    assign dy_req_valid = (state == S_DY_RD_REQ) || (state == S_DY_WR_REQ);
    assign dy_req_we    = (state == S_DY_WR_REQ);
    assign dy_req_addr  = dy_addr_reg;
    assign dy_req_wdata = sum_bits_reg;

    always_ff @(posedge clk) begin
        logic signed [23:0] dx_addr_calc_tmp;
        logic signed [23:0] dy_addr_calc_tmp;

        if (rst) begin
            state        <= S_IDLE;
            n_reg        <= 8'd0;
            k_reg        <= 8'd0;
            da_reg       <= 64'd0;
            incx_reg     <= 8'sd0;
            incy_reg     <= 8'sd0;
            dx_addr_reg  <= 8'd0;
            dy_addr_reg  <= 8'd0;
            x_bits_reg   <= 64'd0;
            sum_bits_reg <= 64'd0;
        end else begin
            case (state)
                S_IDLE: begin
                    if (input_accepted) begin
                        n_reg    <= in_n;
                        k_reg    <= in_k;
                        da_reg   <= in_da;
                        incx_reg <= $signed(in_incx);
                        incy_reg <= $signed(in_incy);

                        if ((in_n != 8'd0) && (in_k < in_n) && (in_da != 64'd0)) begin
                            if ($signed(in_incx) >= 0) begin
                                dx_addr_calc_tmp = $signed({1'b0, in_k}) * $signed(in_incx);
                            end else begin
                                dx_addr_calc_tmp = (($signed(16'sd1) - $signed({1'b0, in_n})) * $signed(in_incx)) +
                                                   ($signed({1'b0, in_k}) * $signed(in_incx));
                            end

                            if ($signed(in_incy) >= 0) begin
                                dy_addr_calc_tmp = $signed({1'b0, in_k}) * $signed(in_incy);
                            end else begin
                                dy_addr_calc_tmp = (($signed(16'sd1) - $signed({1'b0, in_n})) * $signed(in_incy)) +
                                                   ($signed({1'b0, in_k}) * $signed(in_incy));
                            end

                            dx_addr_reg <= dx_addr_calc_tmp[7:0];
                            dy_addr_reg <= dy_addr_calc_tmp[7:0];
                            state <= S_DX_REQ;
                        end else begin
                            state <= S_OUT;
                        end
                    end
                end

                S_DX_REQ: begin
                    if (dx_req_ready) begin
                        state <= S_DX_WAIT;
                    end
                end

                S_DX_WAIT: begin
                    if (dx_resp_valid) begin
                        x_bits_reg <= dx_resp_rdata;
                        state <= S_DY_RD_REQ;
                    end
                end

                S_DY_RD_REQ: begin
                    if (dy_req_ready) begin
                        state <= S_DY_RD_WAIT;
                    end
                end

                S_DY_RD_WAIT: begin
                    if (dy_resp_valid) begin
                        sum_bits_reg <= sum_full[63:0];
                        state <= S_DY_WR_REQ;
                    end
                end

                S_DY_WR_REQ: begin
                    if (dy_req_ready) begin
                        state <= S_OUT;
                    end
                end

                S_OUT: begin
                    if (output_consumed) begin
                        state <= S_IDLE;
                    end
                end
            endcase
        end
    end

endmodule
