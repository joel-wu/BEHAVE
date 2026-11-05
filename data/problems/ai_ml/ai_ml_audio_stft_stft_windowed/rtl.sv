module ai_ml_audio_stft_stft_windowed #(
    parameter int SAMPLE_W = 16,
    parameter int COEFF_W = 16,
    parameter int FRAME_LEN = 8,
    parameter int HOP_LEN = 4
) (
    input  logic                         clk,
    input  logic                         rst,

    input  logic                         in_valid,
    output logic                         in_ready,
    input  logic signed [SAMPLE_W-1:0]   in_sample,

    output logic                         out_valid,
    input  logic                         out_ready,
    output logic signed [SAMPLE_W+COEFF_W-1:0] out_frame_0,
    output logic signed [SAMPLE_W+COEFF_W-1:0] out_frame_1,
    output logic signed [SAMPLE_W+COEFF_W-1:0] out_frame_2,
    output logic signed [SAMPLE_W+COEFF_W-1:0] out_frame_3,
    output logic signed [SAMPLE_W+COEFF_W-1:0] out_frame_4,
    output logic signed [SAMPLE_W+COEFF_W-1:0] out_frame_5,
    output logic signed [SAMPLE_W+COEFF_W-1:0] out_frame_6,
    output logic signed [SAMPLE_W+COEFF_W-1:0] out_frame_7
);

    localparam int OUT_W = SAMPLE_W + COEFF_W;

    logic busy;
    logic [3:0] sample_count;

    logic signed [SAMPLE_W-1:0] hist_0;
    logic signed [SAMPLE_W-1:0] hist_1;
    logic signed [SAMPLE_W-1:0] hist_2;
    logic signed [SAMPLE_W-1:0] hist_3;
    logic signed [SAMPLE_W-1:0] hist_4;
    logic signed [SAMPLE_W-1:0] hist_5;
    logic signed [SAMPLE_W-1:0] hist_6;
    logic signed [SAMPLE_W-1:0] hist_7;

    logic [3:0] new_count;
    logic        emit_now;

    wire input_accepted;
    wire output_consumed;

    assign output_consumed = out_valid & out_ready;
    assign input_accepted  = in_valid & in_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_comb begin
        new_count = (sample_count == 4'd11) ? 4'd8 : sample_count + 4'd1;
        emit_now = (new_count == 4'd8);
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy         <= 1'b0;
            sample_count <= 4'd0;

            hist_0 <= '0;
            hist_1 <= '0;
            hist_2 <= '0;
            hist_3 <= '0;
            hist_4 <= '0;
            hist_5 <= '0;
            hist_6 <= '0;
            hist_7 <= '0;

            out_frame_0 <= '0;
            out_frame_1 <= '0;
            out_frame_2 <= '0;
            out_frame_3 <= '0;
            out_frame_4 <= '0;
            out_frame_5 <= '0;
            out_frame_6 <= '0;
            out_frame_7 <= '0;
        end else begin
            if (input_accepted) begin
                sample_count <= new_count;

                hist_0 <= hist_1;
                hist_1 <= hist_2;
                hist_2 <= hist_3;
                hist_3 <= hist_4;
                hist_4 <= hist_5;
                hist_5 <= hist_6;
                hist_6 <= hist_7;
                hist_7 <= in_sample;

                if (emit_now) begin
                    out_frame_0 <= $signed(hist_1) * $signed(16'sd0);
                    out_frame_1 <= $signed(hist_2) * $signed(16'sd12540);
                    out_frame_2 <= $signed(hist_3) * $signed(16'sd23170);
                    out_frame_3 <= $signed(hist_4) * $signed(16'sd30274);
                    out_frame_4 <= $signed(hist_5) * $signed(16'sd32767);
                    out_frame_5 <= $signed(hist_6) * $signed(16'sd30274);
                    out_frame_6 <= $signed(hist_7) * $signed(16'sd23170);
                    out_frame_7 <= $signed(in_sample) * $signed(16'sd12540);
                    busy        <= 1'b1;
                end else begin
                    busy <= 1'b0;
                end
            end else if (output_consumed) begin
                busy <= 1'b0;
            end
        end
    end

endmodule
