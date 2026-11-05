module signal_processing_comm_loop_eqlms #(
    parameter int W = 32,
    parameter int H_LEN = 8
) (
    input  logic                 clk,
    input  logic                 rst,

    input  logic                 in_valid,
    output logic                 in_ready,
    input  logic signed [W-1:0]  in_x,
    input  logic signed [W-1:0]  in_d,

    output logic                 out_valid,
    input  logic                 out_ready,
    output logic signed [W-1:0]  out_y,
    output logic                 out_updated
);

    localparam int COUNT_W = (H_LEN <= 1) ? 1 : $clog2(H_LEN + 1);
    localparam int X2_W    = 2 * W;
    localparam int SUM_W   = X2_W + ((H_LEN <= 1) ? 1 : $clog2(H_LEN + 1));
    localparam int PROD_W  = 2 * W;
    localparam int YACC_W  = PROD_W + ((H_LEN <= 1) ? 1 : $clog2(H_LEN));
    localparam int ALPHA_W = W + 1;
    localparam int NUM_W   = ALPHA_W + W;

    logic busy;
    logic signed [W-1:0]  w0      [0:H_LEN-1];
    logic signed [W-1:0]  buffer  [0:H_LEN-1];
    logic        [X2_W-1:0] x2_hist [0:H_LEN-1];
    logic        [SUM_W-1:0] x2_sum;
    logic        [COUNT_W-1:0] count;
    logic buf_full;

    logic signed [W-1:0]  new_buffer_c    [0:H_LEN-1];
    logic        [X2_W-1:0] new_x2_hist_c [0:H_LEN-1];
    logic signed [W-1:0]  new_w0_c        [0:H_LEN-1];

    logic        [X2_W-1:0] x2_c;
    logic        [X2_W-1:0] x2_oldest_c;
    logic        [SUM_W-1:0] x2_c_ext;
    logic        [SUM_W-1:0] x2_oldest_ext_c;
    logic        [SUM_W-1:0] new_x2_sum_c;
    logic        [COUNT_W-1:0] new_count_c;
    logic        [COUNT_W-1:0] h_len_count_c;
    logic                     do_update_c;
    logic                     new_buf_full_c;
    logic signed [YACC_W-1:0] y_full_c;
    logic signed [W-1:0]      out_y_c;
    logic signed [ALPHA_W-1:0] alpha_c;
    logic        [SUM_W:0]    den_c;
    logic signed [NUM_W-1:0]  numerator_c;
    logic signed [NUM_W-1:0]  delta_c;
    logic signed [NUM_W-1:0]  w0_ext_c;
    logic signed [NUM_W-1:0]  wsum_c;

    integer idx;
    integer j;

    wire input_accepted  = in_valid & in_ready;
    wire output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    function automatic logic signed [W-1:0] trunc_yacc_to_w(input logic signed [YACC_W-1:0] value);
        trunc_yacc_to_w = value[W-1:0];
    endfunction

    function automatic logic signed [W-1:0] trunc_numw_to_w(input logic signed [NUM_W-1:0] value);
        trunc_numw_to_w = value[W-1:0];
    endfunction

    function automatic logic signed [W-1:0] init_coeff(input int tap_idx);
        init_coeff = '0;
        if (tap_idx == (H_LEN / 2))
            init_coeff = {{(W-1){1'b0}}, 1'b1};
        else
            init_coeff = '0;
    endfunction

    function automatic logic signed [NUM_W-1:0] trunc_div_toward_zero_sv(
        input logic signed [NUM_W-1:0] num,
        input logic [SUM_W:0] den
    );
        logic signed [NUM_W-1:0] num_abs;
        logic [SUM_W:0] den_abs;
        logic [NUM_W-1:0] q_abs;
        begin
            trunc_div_toward_zero_sv = '0;
            num_abs = '0;
            den_abs = '0;
            q_abs   = '0;
            if (den == '0) begin
                trunc_div_toward_zero_sv = '0;
            end else begin
                if (num < 0)
                    num_abs = -num;
                else
                    num_abs = num;

                den_abs = den;
                q_abs   = num_abs / den_abs;

                if (num < 0)
                    trunc_div_toward_zero_sv = -$signed(q_abs);
                else
                    trunc_div_toward_zero_sv = $signed(q_abs);
            end
        end
    endfunction

    always_comb begin
        x2_c            = in_x * in_x;
        x2_oldest_c     = x2_hist[H_LEN-1];
        x2_c_ext        = {{(SUM_W-X2_W){1'b0}}, x2_c};
        x2_oldest_ext_c = {{(SUM_W-X2_W){1'b0}}, x2_oldest_c};
        new_x2_sum_c    = x2_sum + x2_c_ext - x2_oldest_ext_c;
        new_count_c     = count < COUNT_W'(H_LEN) ? count + COUNT_W'(1) : count;
        h_len_count_c   = COUNT_W'(H_LEN);

        for (j = 0; j < H_LEN; j = j + 1) begin
            new_buffer_c[j]  = '0;
            new_x2_hist_c[j] = '0;
            new_w0_c[j]      = w0[j];
        end

        new_buffer_c[0]  = in_x;
        new_x2_hist_c[0] = x2_c;
        for (j = 1; j < H_LEN; j = j + 1) begin
            new_buffer_c[j]  = buffer[j-1];
            new_x2_hist_c[j] = x2_hist[j-1];
        end

        y_full_c = '0;
        for (j = 0; j < H_LEN; j = j + 1) begin
            y_full_c = y_full_c + (w0[j] * new_buffer_c[j]);
        end
        out_y_c = trunc_yacc_to_w(y_full_c);

        do_update_c    = 1'b0;
        new_buf_full_c = buf_full;
        if (buf_full == 1'b0) begin
            if (new_count_c >= h_len_count_c) begin
                new_buf_full_c = 1'b1;
                do_update_c    = 1'b1;
            end
        end else begin
            do_update_c = 1'b1;
        end

        alpha_c     = in_d - out_y_c;
        den_c       = {new_x2_sum_c, 1'b0};
        numerator_c = '0;
        delta_c     = '0;
        w0_ext_c    = '0;
        wsum_c      = '0;

        for (j = 0; j < H_LEN; j = j + 1) begin
            new_w0_c[j] = w0[j];
            if (do_update_c) begin
                if (new_x2_sum_c == '0) begin
                    new_w0_c[j] = w0[j];
                end else begin
                    numerator_c = alpha_c * new_buffer_c[j];
                    delta_c     = trunc_div_toward_zero_sv(numerator_c, den_c);
                    w0_ext_c    = {{(NUM_W-W){w0[j][W-1]}}, w0[j]};
                    wsum_c      = w0_ext_c + delta_c;
                    new_w0_c[j] = trunc_numw_to_w(wsum_c);
                end
            end
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy        <= 1'b0;
            out_y       <= '0;
            out_updated <= 1'b0;
            x2_sum      <= '0;
            count       <= '0;
            buf_full    <= 1'b0;
            for (idx = 0; idx < H_LEN; idx = idx + 1) begin
                w0[idx]      <= init_coeff(idx);
                buffer[idx]  <= '0;
                x2_hist[idx] <= '0;
            end
        end else if (input_accepted) begin
            out_y       <= out_y_c;
            out_updated <= do_update_c;
            busy        <= 1'b1;
            x2_sum      <= new_x2_sum_c;
            count       <= new_count_c;
            buf_full    <= new_buf_full_c;
            for (idx = 0; idx < H_LEN; idx = idx + 1) begin
                buffer[idx]  <= new_buffer_c[idx];
                x2_hist[idx] <= new_x2_hist_c[idx];
                w0[idx]      <= new_w0_c[idx];
            end
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
