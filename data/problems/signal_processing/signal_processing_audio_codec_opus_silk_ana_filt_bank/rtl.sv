module signal_processing_audio_codec_opus_silk_ana_filt_bank (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_s0,
    input  logic [15:0]  in_s1,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_outL,
    output logic [15:0]  out_outH
);

    localparam logic signed [15:0] A_FB1_20 = 16'sd10788;
    localparam logic signed [15:0] A_FB1_21 = -16'sd24290;

    logic busy;
    logic signed [31:0] state_s0;
    logic signed [31:0] state_s1;
    logic signed [15:0] out_l_reg;
    logic signed [15:0] out_h_reg;

    logic signed [15:0] s0_signed;
    logic signed [15:0] s1_signed;
    logic signed [31:0] in32_0;
    logic signed [31:0] in32_1;
    logic signed [31:0] y0;
    logic signed [31:0] y1;
    logic signed [47:0] prod0;
    logic signed [47:0] prod1;
    logic signed [31:0] x0_mul_shift;
    logic signed [31:0] x1;
    logic signed [31:0] x0;
    logic signed [31:0] out_1;
    logic signed [31:0] out_2;
    logic signed [31:0] new_s0;
    logic signed [31:0] new_s1;
    logic signed [31:0] sum_l;
    logic signed [31:0] sum_h;
    logic signed [31:0] low_tmp;
    logic signed [31:0] high_tmp;
    logic signed [15:0] next_out_l;
    logic signed [15:0] next_out_h;

    wire input_accepted  = in_valid & in_ready;
    wire output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;
    assign out_outL  = out_l_reg;
    assign out_outH  = out_h_reg;

    function automatic logic signed [31:0] rshift_round_11_func(input logic signed [31:0] v);
        begin
            rshift_round_11_func = ((v >>> 10) + 32'sd1) >>> 1;
        end
    endfunction

    function automatic logic signed [15:0] sat16(input logic signed [31:0] value);
        begin
            sat16 = 16'sd0;
            if (value > 32'sd32767) begin
                sat16 = 16'sd32767;
            end else if (value < -32'sd32768) begin
                sat16 = -16'sd32768;
            end else begin
                sat16 = value[15:0];
            end
        end
    endfunction

    always_comb begin
        s0_signed    = in_s0;
        s1_signed    = in_s1;
        in32_0       = s0_signed <<< 10;
        in32_1       = s1_signed <<< 10;
        y0           = in32_0 - state_s0;
        y1           = in32_1 - state_s1;
        prod0        = y0 * A_FB1_21;
        prod1        = y1 * A_FB1_20;
        x0_mul_shift = prod0 >>> 16;
        x1           = prod1 >>> 16;
        x0           = y0 + x0_mul_shift;
        out_1        = state_s0 + x0;
        out_2        = state_s1 + x1;
        new_s0       = in32_0 + x0;
        new_s1       = in32_1 + x1;
        sum_l        = out_1 + out_2;
        sum_h        = out_2 - out_1;
        low_tmp      = rshift_round_11_func(sum_l);
        high_tmp     = rshift_round_11_func(sum_h);
        next_out_l   = sat16(low_tmp);
        next_out_h   = sat16(high_tmp);
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy     <= 1'b0;
            state_s0 <= 32'sd0;
            state_s1 <= 32'sd0;
            out_l_reg <= 16'sd0;
            out_h_reg <= 16'sd0;
        end else if (input_accepted) begin
            state_s0 <= new_s0;
            state_s1 <= new_s1;
            out_l_reg <= next_out_l;
            out_h_reg <= next_out_h;
            busy     <= 1'b1;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
