module matrix_multiply_f32_cell (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [7:0]   in_num_rows_a,
    input  logic [7:0]   in_num_cols_a,
    input  logic [7:0]   in_num_rows_b,
    input  logic [7:0]   in_num_cols_b,
    input  logic [7:0]   in_row_idx,
    input  logic [7:0]   in_col_idx,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_status,
    output logic [31:0]  out_value,

    output logic         a_mem_req_valid,
    output logic [15:0]  a_mem_req_addr,
    input  logic         a_mem_req_ready,
    input  logic         a_mem_resp_valid,
    input  logic [31:0]  a_mem_resp_rdata,

    output logic         b_mem_req_valid,
    output logic [15:0]  b_mem_req_addr,
    input  logic         b_mem_req_ready,
    input  logic         b_mem_resp_valid,
    input  logic [31:0]  b_mem_resp_rdata
);

function automatic logic [63:0] fp_shift_jam(input logic [63:0] x, input integer n);
    if (n <= 0) fp_shift_jam = x;
    else if (n >= 64) fp_shift_jam = {63'b0, |x};
    else fp_shift_jam = (x >> n) | {63'b0, |(x << (64-n))};
endfunction

function automatic logic [31:0] fp_round_pack(
    input logic sign_bit, input integer exponent, input logic [63:0] sig
);
    integer e;
    logic [63:0] shifted, rounded;
    begin
        e = exponent;
        shifted = sig;
        if (e < 1) begin
            shifted = fp_shift_jam(shifted, 1-e);
            e = 1;
        end
        rounded = (shifted >> 3) + {63'b0, (shifted[2] &&
                  (shifted[1] || shifted[0] || shifted[3]))};
        if (rounded[24]) begin
            rounded = rounded >> 1;
            e = e+1;
        end
        if (e >= 255) fp_round_pack = {sign_bit, 8'hff, 23'b0};
        else if (rounded < 64'h800000)
            fp_round_pack = {sign_bit, 8'b0, rounded[22:0]};
        else fp_round_pack = {sign_bit, e[7:0], rounded[22:0]};
    end
endfunction

function automatic logic [31:0] fp32_add_bits(input logic [31:0] a, b);
    integer ea, eb, e, i;
    logic sa, sb, sign_bit;
    logic [63:0] ma, mb, hi, lo, sum;
    begin
        sa = a[31]; sb = b[31];
        ea = a[30:23] == 0 ? 1 : int'(a[30:23]);
        eb = b[30:23] == 0 ? 1 : int'(b[30:23]);
        ma = {40'b0, (a[30:23] != 0), a[22:0]};
        mb = {40'b0, (b[30:23] != 0), b[22:0]};
        if ((a[30:0] > 31'h7f800000) || (b[30:0] > 31'h7f800000))
            fp32_add_bits = 32'h7fc00000;
        else if ((a[30:0] == 31'h7f800000) && (b[30:0] == 31'h7f800000) && (sa != sb))
            fp32_add_bits = 32'h7fc00000;
        else if (a[30:0] == 31'h7f800000) fp32_add_bits = a;
        else if (b[30:0] == 31'h7f800000) fp32_add_bits = b;
        else if ((a[30:0] == 0) && (b[30:0] == 0))
            fp32_add_bits = {sa & sb, 31'b0};
        else begin
            if ((ea > eb) || ((ea == eb) && (ma >= mb))) begin
                hi = ma << 3; lo = fp_shift_jam(mb << 3, ea-eb);
                sign_bit = sa; e = ea;
            end else begin
                hi = mb << 3; lo = fp_shift_jam(ma << 3, eb-ea);
                sign_bit = sb; e = eb;
            end
            sum = sa == sb ? hi+lo : hi-lo;
            if (sum == 0) fp32_add_bits = 32'b0;
            else begin
                if (sum[27]) begin
                    sum = fp_shift_jam(sum, 1); e = e+1;
                end
                for (i=0; i<26; i=i+1) begin
                    if (!sum[26] && e>1) begin sum=sum<<1; e=e-1; end
                end
                fp32_add_bits = fp_round_pack(sign_bit, e, sum);
            end
        end
    end
endfunction

function automatic logic [31:0] fp32_sub_bits(input logic [31:0] a, b);
    fp32_sub_bits = fp32_add_bits(a, b ^ 32'h80000000);
endfunction

function automatic logic [31:0] fp32_mul_bits(input logic [31:0] a, b);
    integer ea, eb, e, i;
    logic sign_bit;
    logic [63:0] ma, mb, product, sig;
    begin
        sign_bit = a[31] ^ b[31];
        ea = a[30:23] == 0 ? 1 : int'(a[30:23]);
        eb = b[30:23] == 0 ? 1 : int'(b[30:23]);
        ma = {40'b0, (a[30:23] != 0), a[22:0]};
        mb = {40'b0, (b[30:23] != 0), b[22:0]};
        if ((a[30:0] > 31'h7f800000) || (b[30:0] > 31'h7f800000) ||
            ((a[30:0] == 31'h7f800000) && (b[30:0] == 0)) ||
            ((b[30:0] == 31'h7f800000) && (a[30:0] == 0)))
            fp32_mul_bits = 32'h7fc00000;
        else if ((a[30:0] == 31'h7f800000) || (b[30:0] == 31'h7f800000))
            fp32_mul_bits = {sign_bit, 8'hff, 23'b0};
        else if ((a[30:0] == 0) || (b[30:0] == 0))
            fp32_mul_bits = {sign_bit, 31'b0};
        else begin
            for (i=0; i<23; i=i+1) begin
                if (!ma[23]) begin ma=ma<<1; ea=ea-1; end
                if (!mb[23]) begin mb=mb<<1; eb=eb-1; end
            end
            product = ma*mb;
            e = ea+eb-127;
            if (product[47]) begin sig=fp_shift_jam(product,21); e=e+1; end
            else sig=fp_shift_jam(product,20);
            fp32_mul_bits = fp_round_pack(sign_bit, e, sig);
        end
    end
endfunction
    localparam logic [31:0] FP32_CANON_NAN=32'h7fc00000;

    typedef enum logic [2:0] {
        ST_IDLE        = 3'd0,
        ST_INVALID_OUT = 3'd1,
        ST_REQ_A       = 3'd2,
        ST_WAIT_A      = 3'd3,
        ST_REQ_B       = 3'd4,
        ST_WAIT_B      = 3'd5,
        ST_ACCUM       = 3'd6,
        ST_DONE        = 3'd7
    } state_t;

    state_t state;

    logic [7:0]  num_cols_a_r;
    logic [7:0]  num_cols_b_r;
    logic [7:0]  row_idx_r;
    logic [7:0]  col_idx_r;
    logic [7:0]  k_r;
    logic [31:0] a_val_r;
    logic [31:0] b_val_r;
    logic [31:0] acc_r;

    logic [15:0] a_addr_w;
    logic [15:0] b_addr_w;
    logic [23:0] a_addr_calc;
    logic [23:0] b_addr_calc;

    logic output_consumed;
    logic input_accepted;
    logic valid_dims;
    logic valid_coords;
    logic valid_all;

    assign valid_dims   = (in_num_cols_a == in_num_rows_b);
    assign valid_coords = (in_row_idx < in_num_rows_a) && (in_col_idx < in_num_cols_b);
    assign valid_all    = valid_dims && valid_coords;

    assign out_valid = (state == ST_INVALID_OUT) || (state == ST_DONE);
    assign output_consumed = out_valid && out_ready;
    assign in_ready = (state == ST_IDLE) || output_consumed;
    assign input_accepted = in_valid && in_ready;

    assign a_addr_calc = ({16'd0, row_idx_r} * {16'd0, num_cols_a_r}) + {16'd0, k_r};
    assign b_addr_calc = ({16'd0, k_r} * {16'd0, num_cols_b_r}) + {16'd0, col_idx_r};
    assign a_addr_w = a_addr_calc[15:0];
    assign b_addr_w = b_addr_calc[15:0];

    always_comb begin
        a_mem_req_valid = 1'b0;
        a_mem_req_addr  = 16'h0000;
        b_mem_req_valid = 1'b0;
        b_mem_req_addr  = 16'h0000;

        if (state == ST_REQ_A) begin
            a_mem_req_valid = 1'b1;
            a_mem_req_addr  = a_addr_w;
        end
        if (state == ST_REQ_B) begin
            b_mem_req_valid = 1'b1;
            b_mem_req_addr  = b_addr_w;
        end
    end

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            state        <= ST_IDLE;
            num_cols_a_r <= 8'd0;
            num_cols_b_r <= 8'd0;
            row_idx_r    <= 8'd0;
            col_idx_r    <= 8'd0;
            k_r          <= 8'd0;
            a_val_r      <= 32'd0;
            b_val_r      <= 32'd0;
            acc_r        <= 32'd0;
            out_status   <= 32'd0;
            out_value    <= 32'd0;
        end else begin
            case (state)
                ST_IDLE: begin
                    if (input_accepted) begin
                        num_cols_a_r <= in_num_cols_a;
                        num_cols_b_r <= in_num_cols_b;
                        row_idx_r    <= in_row_idx;
                        col_idx_r    <= in_col_idx;
                        k_r          <= 8'd0;
                        a_val_r      <= 32'd0;
                        b_val_r      <= 32'd0;
                        acc_r        <= 32'd0;

                        if (valid_all) begin
                            out_status <= 32'h00000000;
                            if (in_num_cols_a == 8'd0) begin
                                out_value <= 32'h00000000;
                                state     <= ST_DONE;
                            end else begin
                                out_value <= 32'h00000000;
                                state     <= ST_REQ_A;
                            end
                        end else begin
                            out_status <= 32'h00000001;
                            out_value  <= 32'h00000000;
                            state      <= ST_INVALID_OUT;
                        end
                    end
                end

                ST_INVALID_OUT: begin
                    if (output_consumed) begin
                        if (in_valid) begin
                            num_cols_a_r <= in_num_cols_a;
                            num_cols_b_r <= in_num_cols_b;
                            row_idx_r    <= in_row_idx;
                            col_idx_r    <= in_col_idx;
                            k_r          <= 8'd0;
                            a_val_r      <= 32'd0;
                            b_val_r      <= 32'd0;
                            acc_r        <= 32'd0;

                            if (valid_all) begin
                                out_status <= 32'h00000000;
                                if (in_num_cols_a == 8'd0) begin
                                    out_value <= 32'h00000000;
                                    state     <= ST_DONE;
                                end else begin
                                    out_value <= 32'h00000000;
                                    state     <= ST_REQ_A;
                                end
                            end else begin
                                out_status <= 32'h00000001;
                                out_value  <= 32'h00000000;
                                state      <= ST_INVALID_OUT;
                            end
                        end else begin
                            state <= ST_IDLE;
                        end
                    end
                end

                ST_REQ_A: begin
                    if (a_mem_req_ready) begin
                        state <= ST_WAIT_A;
                    end
                end

                ST_WAIT_A: begin
                    if (a_mem_resp_valid) begin
                        a_val_r <= a_mem_resp_rdata;
                        state   <= ST_REQ_B;
                    end
                end

                ST_REQ_B: begin
                    if (b_mem_req_ready) begin
                        state <= ST_WAIT_B;
                    end
                end

                ST_WAIT_B: begin
                    if (b_mem_resp_valid) begin
                        b_val_r <= b_mem_resp_rdata;
                        state   <= ST_ACCUM;
                    end
                end

                ST_ACCUM: begin
                    acc_r <= k_r == 0 ? fp32_mul_bits(a_val_r,b_val_r) : fp32_add_bits(acc_r,fp32_mul_bits(a_val_r,b_val_r));
                    if (k_r == num_cols_a_r-1) begin
                        out_value <= k_r == 0 ? fp32_mul_bits(a_val_r,b_val_r) : fp32_add_bits(acc_r,fp32_mul_bits(a_val_r,b_val_r));
                        state <= ST_DONE;
                    end else begin
                        k_r <= k_r+1;
                        state <= ST_REQ_A;
                    end
                end

                ST_DONE: begin
                    if (output_consumed) begin
                        if (in_valid) begin
                            num_cols_a_r <= in_num_cols_a;
                            num_cols_b_r <= in_num_cols_b;
                            row_idx_r    <= in_row_idx;
                            col_idx_r    <= in_col_idx;
                            k_r          <= 8'd0;
                            a_val_r      <= 32'd0;
                            b_val_r      <= 32'd0;
                            acc_r        <= 32'd0;

                            if (valid_all) begin
                                out_status <= 32'h00000000;
                                if (in_num_cols_a == 8'd0) begin
                                    out_value <= 32'h00000000;
                                    state     <= ST_DONE;
                                end else begin
                                    out_value <= 32'h00000000;
                                    state     <= ST_REQ_A;
                                end
                            end else begin
                                out_status <= 32'h00000001;
                                out_value  <= 32'h00000000;
                                state      <= ST_INVALID_OUT;
                            end
                        end else begin
                            state <= ST_IDLE;
                        end
                    end
                end
            endcase
        end
    end

endmodule
