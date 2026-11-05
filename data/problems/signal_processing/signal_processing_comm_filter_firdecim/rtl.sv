module signal_processing_comm_filter_firdecim #(
    parameter int DATA_W = 16,
    parameter int COEF_W = 16,
    parameter int SCALE_W = 16,
    parameter int ACC_W = 48,
    parameter int H_LEN = 8,
    parameter int M = 2,
    parameter logic signed [COEF_W*H_LEN-1:0] COEFFS =
        {COEF_W'(0), COEF_W'(1), COEF_W'(2), COEF_W'(3),
         COEF_W'(4), COEF_W'(3), COEF_W'(2), COEF_W'(1)},
    parameter logic signed [SCALE_W-1:0] SCALE = 'd1
) (
    input  logic                         clk,
    input  logic                         rst,

    input  logic                         in_valid,
    output logic                         in_ready,
    input  logic signed [DATA_W-1:0]     in_x,

    output logic                         out_valid,
    input  logic                         out_ready,
    output logic signed [DATA_W-1:0]     out_y
);

    localparam int PROD_W   = DATA_W + COEF_W;
    localparam int SUM_W    = ACC_W;
    localparam int SCALED_W = ACC_W + SCALE_W;
    localparam int PHASE_W  = (M <= 1) ? 1 : $clog2(M);

    logic signed [DATA_W-1:0] window [0:H_LEN-1];
    logic [PHASE_W-1:0] phase;
    logic busy;

    logic signed [SUM_W-1:0] fir_sum_comb;
    logic signed [SCALED_W-1:0] scaled_sum_comb;
    logic signed [ACC_W-1:0] wrapped_acc_comb;
    logic signed [DATA_W-1:0] out_y_next;

    wire input_accepted;
    wire output_consumed;
    wire produce_output;

    integer comb_idx;
    integer seq_idx;
    logic signed [SUM_W-1:0] sum_tmp;
    logic signed [PROD_W-1:0] prod_tmp;
    logic signed [COEF_W-1:0] coef_sel;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;
    assign produce_output  = (phase == PHASE_W'(0));

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_comb begin
        sum_tmp = '0;
        prod_tmp = '0;
        coef_sel = '0;

        for (comb_idx = 0; comb_idx < H_LEN; comb_idx = comb_idx + 1) begin
            coef_sel = COEFFS[comb_idx*COEF_W +: COEF_W];
            if (comb_idx == 0) begin
                prod_tmp = $signed(in_x) * $signed(coef_sel);
            end else begin
                prod_tmp = $signed(window[comb_idx-1]) * $signed(coef_sel);
            end
            sum_tmp = sum_tmp + SUM_W'(prod_tmp);
        end

        fir_sum_comb = sum_tmp;
        scaled_sum_comb = $signed(fir_sum_comb) * $signed(SCALE);
        wrapped_acc_comb = scaled_sum_comb[ACC_W-1:0];
        out_y_next = wrapped_acc_comb[DATA_W-1:0];
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            for (seq_idx = 0; seq_idx < H_LEN; seq_idx = seq_idx + 1) begin
                window[seq_idx] <= '0;
            end
            phase <= '0;
            busy  <= 1'b0;
            out_y <= '0;
        end else begin
            if (input_accepted) begin
                window[0] <= in_x;
                for (seq_idx = 1; seq_idx < H_LEN; seq_idx = seq_idx + 1) begin
                    window[seq_idx] <= window[seq_idx-1];
                end

                if (phase == M - 1) begin
                    phase <= '0;
                end else begin
                    phase <= phase + PHASE_W'(1);
                end

                if (produce_output) begin
                    out_y <= out_y_next;
                    busy  <= 1'b1;
                end else begin
                    busy  <= 1'b0;
                end
            end else if (output_consumed) begin
                busy <= 1'b0;
            end
        end
    end

endmodule
