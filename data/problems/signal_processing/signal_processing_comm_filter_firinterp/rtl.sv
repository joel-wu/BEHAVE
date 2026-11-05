module firinterp #(
    parameter int unsigned M = 2,
    parameter int unsigned COEF_WIDTH = 16,
    parameter int unsigned DATA_WIDTH = 16,
    parameter int unsigned COEF_LEN = 7,
    parameter logic signed [COEF_WIDTH*COEF_LEN-1:0] COEF =
        {COEF_WIDTH'(1), COEF_WIDTH'(2), COEF_WIDTH'(3), COEF_WIDTH'(4),
         COEF_WIDTH'(3), COEF_WIDTH'(2), COEF_WIDTH'(1)}
) (
    input  logic                             clk,
    input  logic                             rst,

    input  logic                             in_valid,
    output logic                             in_ready,
    input  logic signed [DATA_WIDTH-1:0]     in_x,

    output logic                             out_valid,
    input  logic                             out_ready,
    output logic signed [DATA_WIDTH-1:0]     out_y
);

    localparam int unsigned H_SUB_LEN = (COEF_LEN + M - 1) / M;
    localparam int unsigned PHASE_W   = (M <= 1) ? 1 : $clog2(M);
    localparam int unsigned ACC_WIDTH = DATA_WIDTH + COEF_WIDTH + ((H_SUB_LEN <= 1) ? 1 : $clog2(H_SUB_LEN));

    logic signed [DATA_WIDTH-1:0] history [0:H_SUB_LEN-1];
    logic signed [DATA_WIDTH-1:0] out_buf [0:M-1];

    logic               busy;
    logic [PHASE_W-1:0] phase_idx;

    wire input_accepted;
    wire output_consumed;
    wire frame_complete;

    integer p, k;

    logic signed [ACC_WIDTH-1:0] acc_tmp;
    logic signed [ACC_WIDTH-1:0] prod_tmp;
    logic signed [COEF_WIDTH-1:0] coef_tmp;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;
    assign frame_complete  = output_consumed & (phase_idx == PHASE_W'(M-1));

    assign out_valid = busy;
    assign in_ready  = (~busy) | frame_complete;
    assign out_y     = out_buf[phase_idx];

    always_ff @(posedge clk) begin
        if (rst) begin
            busy      <= 1'b0;
            phase_idx <= '0;
            for (k = 0; k < H_SUB_LEN; k = k + 1) begin
                history[k] <= '0;
            end
            for (p = 0; p < M; p = p + 1) begin
                out_buf[p] <= '0;
            end
        end else if (input_accepted) begin
            for (p = 0; p < M; p = p + 1) begin
                acc_tmp = '0;

                for (k = 0; k < H_SUB_LEN; k = k + 1) begin
                    coef_tmp = '0;
                    if (p + k*M < COEF_LEN)
                        coef_tmp = COEF[(p + k*M)*COEF_WIDTH +: COEF_WIDTH];
                    if (k == 0)
                        prod_tmp = $signed(in_x) * $signed(coef_tmp);
                    else
                        prod_tmp = $signed(history[k-1]) * $signed(coef_tmp);
                    acc_tmp = acc_tmp + prod_tmp;
                end

                out_buf[p] <= acc_tmp[DATA_WIDTH-1:0];
            end

            history[0] <= in_x;
            for (k = 1; k < H_SUB_LEN; k = k + 1) begin
                history[k] <= history[k-1];
            end
            busy      <= 1'b1;
            phase_idx <= '0;
        end else if (output_consumed) begin
            if (phase_idx == PHASE_W'(M-1)) begin
                busy      <= 1'b0;
                phase_idx <= '0;
            end else begin
                busy      <= 1'b1;
                phase_idx <= phase_idx + PHASE_W'(1);
            end
        end
    end

endmodule
