module signal_processing_comm_audio_cvsd (
    input  logic       clk,
    input  logic       rst,

    input  logic       in_valid,
    output logic       in_ready,
    input  logic       in_mode,
    input  logic [7:0] in_data,

    output logic       out_valid,
    input  logic       out_ready,
    output logic [7:0] out_data
);

    // Q28 fixed-point.
    localparam logic signed [63:0] ONE_Q       = 64'sd268435456;   // 1.0 * 2^28
    localparam logic signed [63:0] NEG_ONE_Q   = -64'sd268435456;
    localparam logic signed [63:0] DELTA_MIN_Q = 64'sd2684354;     // floor(0.01 * 2^28)
    localparam logic signed [63:0] DELTA_MAX_Q = 64'sd268435456;
    localparam logic signed [63:0] ALPHA_Q     = 64'sd241591910;   // int(0.9   * 2^28)
    localparam logic signed [63:0] APLUSB_Q    = 64'sd507343011;   // int(1.89  * 2^28)
    localparam logic signed [63:0] APRODB_Q    = 64'sd239175991;   // int(0.891 * 2^28)

    logic busy;
    logic [7:0] out_data_reg;

    logic [2:0] bitref_reg;
    logic signed [63:0] ref_reg;
    logic signed [63:0] delta_reg;
    logic signed [63:0] prefilt_x1_reg;
    logic signed [63:0] post_x1_reg;
    logic signed [63:0] post_y1_reg;
    logic signed [63:0] post_y2_reg;

    wire input_accepted  = in_valid & in_ready;
    wire output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;
    assign out_data  = out_data_reg;

    function automatic logic signed [63:0] clamp_ref_q28(input logic signed [63:0] v);
        begin
            clamp_ref_q28 = v;
            if (v > ONE_Q) begin
                clamp_ref_q28 = ONE_Q;
            end else if (v < NEG_ONE_Q) begin
                clamp_ref_q28 = NEG_ONE_Q;
            end
        end
    endfunction

    function automatic logic signed [63:0] clamp_delta_q28(input logic signed [63:0] v);
        begin
            clamp_delta_q28 = v;
            if (v > DELTA_MAX_Q) begin
                clamp_delta_q28 = DELTA_MAX_Q;
            end else if (v < DELTA_MIN_Q) begin
                clamp_delta_q28 = DELTA_MIN_Q;
            end
        end
    endfunction

    function automatic logic signed [63:0] qmul_q28(
        input logic signed [63:0] a,
        input logic signed [63:0] b
    );
        logic signed [127:0] prod;
        logic signed [127:0] shifted;
        begin
            prod = a * b;
            shifted = prod >>> 28;
            qmul_q28 = shifted[63:0];
        end
    endfunction

    function automatic logic signed [8:0] signed9_from_u8(input logic [7:0] v);
        begin
            if (v[7]) begin
                signed9_from_u8 = {1'b1, v};
            end else begin
                signed9_from_u8 = {1'b0, v};
            end
        end
    endfunction

    function automatic logic signed [63:0] pcm_u8_to_q28(input logic [7:0] v);
        logic signed [8:0] s9;
        logic signed [63:0] tmp;
        begin
            s9 = signed9_from_u8(v);
            tmp = s9;
            pcm_u8_to_q28 = tmp <<< 21; // x_pcm / 128.0 in Q28
        end
    endfunction

    function automatic logic signed [63:0] div_trunc_toward_zero_3(
        input logic signed [63:0] v
    );
        logic signed [63:0] abs_v;
        logic signed [63:0] q_abs;
        begin
            if (v < 0) begin
                abs_v = -v;
                q_abs = abs_v / 64'sd3;
                div_trunc_toward_zero_3 = -q_abs;
            end else begin
                div_trunc_toward_zero_3 = v / 64'sd3;
            end
        end
    endfunction

    function automatic logic [7:0] q28_to_pcm_u8(input logic signed [63:0] v);
        logic signed [63:0] qint;
        logic signed [63:0] abs_v;
        logic signed [63:0] abs_qint;
        begin
            if (v < 0) begin
                abs_v = -v;
                abs_qint = abs_v >>> 21; // trunc toward zero of y*128
                qint = -abs_qint;
            end else begin
                qint = v >>> 21;
            end

            if (qint > 64'sd127) begin
                q28_to_pcm_u8 = 8'h7f;
            end else if (qint < -64'sd128) begin
                q28_to_pcm_u8 = 8'h80;
            end else begin
                q28_to_pcm_u8 = qint[7:0];
            end
        end
    endfunction

    always_ff @(posedge clk) begin
        if (rst) begin
            busy           <= 1'b0;
            out_data_reg   <= 8'h00;
            bitref_reg     <= 3'b000;
            ref_reg        <= 64'sd0;
            delta_reg      <= DELTA_MIN_Q;
            prefilt_x1_reg <= 64'sd0;
            post_x1_reg    <= 64'sd0;
            post_y1_reg    <= 64'sd0;
            post_y2_reg    <= 64'sd0;
        end else if (input_accepted) begin
            logic        bit_v;
            logic [2:0]  bitref_n;
            logic signed [63:0] x_q;
            logic signed [63:0] y_q;
            logic signed [63:0] delta_tmp;
            logic signed [63:0] delta_n;
            logic signed [63:0] ref_tmp;
            logic signed [63:0] ref_n;
            logic signed [63:0] t1_q;
            logic signed [63:0] t2_q;

            if (in_mode == 1'b0) begin
                x_q = pcm_u8_to_q28(in_data);
                y_q = x_q - qmul_q28(ALPHA_Q, prefilt_x1_reg);

                if (ref_reg > y_q) begin
                    bit_v = 1'b0;
                end else begin
                    bit_v = 1'b1;
                end

                bitref_n = {bitref_reg[1:0], bit_v};

                if ((bitref_n == 3'b000) || (bitref_n == 3'b111)) begin
                    delta_tmp = delta_reg + (delta_reg >>> 1);
                end else begin
                    delta_tmp = div_trunc_toward_zero_3(delta_reg * 64'sd2);
                end
                delta_n = clamp_delta_q28(delta_tmp);

                if (bit_v) begin
                    ref_tmp = ref_reg + delta_n;
                end else begin
                    ref_tmp = ref_reg - delta_n;
                end
                ref_n = clamp_ref_q28(ref_tmp);

                bitref_reg     <= bitref_n;
                ref_reg        <= ref_n;
                delta_reg      <= delta_n;
                prefilt_x1_reg <= x_q;
                out_data_reg   <= {7'b0000000, bit_v};
                busy           <= 1'b1;
            end else begin
                bit_v = in_data[0];
                bitref_n = {bitref_reg[1:0], bit_v};

                if ((bitref_n == 3'b000) || (bitref_n == 3'b111)) begin
                    delta_tmp = delta_reg + (delta_reg >>> 1);
                end else begin
                    delta_tmp = div_trunc_toward_zero_3(delta_reg * 64'sd2);
                end
                delta_n = clamp_delta_q28(delta_tmp);

                if (bit_v) begin
                    ref_tmp = ref_reg + delta_n;
                end else begin
                    ref_tmp = ref_reg - delta_n;
                end
                ref_n = clamp_ref_q28(ref_tmp);

                t1_q = qmul_q28(APLUSB_Q, post_y1_reg);
                t2_q = qmul_q28(APRODB_Q, post_y2_reg);
                y_q = ref_n - post_x1_reg + t1_q - t2_q;

                bitref_reg   <= bitref_n;
                ref_reg      <= ref_n;
                delta_reg    <= delta_n;
                post_x1_reg  <= ref_n;
                post_y2_reg  <= post_y1_reg;
                post_y1_reg  <= y_q;
                out_data_reg <= q28_to_pcm_u8(y_q);
                busy         <= 1'b1;
            end
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
