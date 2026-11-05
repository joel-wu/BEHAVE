module signal_processing_comm_modem_modem_ook (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic        in_op,
    input  logic        in_sym,
    input  logic [31:0] in_x_re,
    input  logic [31:0] in_x_im,

    output logic        out_valid,
    input  logic        out_ready,
    output logic        out_sym,
    output logic [31:0] out_y_re,
    output logic [31:0] out_y_im
);

    localparam logic [31:0] SQRT2   = 32'h3FB504F3;
    localparam logic [31:0] SQRT1_2 = 32'h3F3504F3;
    localparam logic [31:0] ZERO    = 32'h00000000;

    logic busy;

    logic [31:0] r_re;
    logic [31:0] r_im;
    logic [31:0] x_hat_re;
    logic [31:0] x_hat_im;

    wire input_accepted;
    wire output_consumed;

    logic cmp_gt_threshold;
    logic in_x_re_is_nan;
    logic in_x_re_is_zero;
    logic in_x_re_sign;
    logic [30:0] in_x_re_mag;
    logic [30:0] threshold_mag;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_comb begin
        in_x_re_is_nan  = (in_x_re[30:23] == 8'hFF) && (in_x_re[22:0] != 23'h0);
        in_x_re_is_zero = (in_x_re[30:0] == 31'h0);
        in_x_re_sign    = in_x_re[31];
        in_x_re_mag     = in_x_re[30:0];
        threshold_mag   = SQRT1_2[30:0];

        if (in_x_re_is_nan) begin
            cmp_gt_threshold = 1'b0;
        end else if (in_x_re_is_zero) begin
            cmp_gt_threshold = 1'b0;
        end else if (in_x_re_sign) begin
            cmp_gt_threshold = 1'b0;
        end else begin
            cmp_gt_threshold = (in_x_re_mag > threshold_mag);
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy     <= 1'b0;
            out_sym  <= 1'b0;
            out_y_re <= ZERO;
            out_y_im <= ZERO;

            r_re     <= ZERO;
            r_im     <= ZERO;
            x_hat_re <= ZERO;
            x_hat_im <= ZERO;
        end else if (input_accepted) begin
            busy <= 1'b1;

            if (in_op == 1'b0) begin
                out_sym  <= in_sym;
                out_y_re <= (in_sym == 1'b0) ? SQRT2 : ZERO;
                out_y_im <= ZERO;
            end else begin
                out_sym  <= cmp_gt_threshold ? 1'b0 : 1'b1;
                out_y_re <= cmp_gt_threshold ? SQRT2 : ZERO;
                out_y_im <= ZERO;

                r_re     <= in_x_re;
                r_im     <= in_x_im;
                x_hat_re <= cmp_gt_threshold ? SQRT2 : ZERO;
                x_hat_im <= ZERO;
            end
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule