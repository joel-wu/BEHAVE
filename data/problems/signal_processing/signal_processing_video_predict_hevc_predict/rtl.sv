module signal_processing_video_predict_hevc_predict (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic         in_bidir,
    input  logic         in_src0,
    input  logic         in_src1,
    input  logic signed [15:0] in_p0,
    input  logic signed [15:0] in_p1,
    input  logic signed [7:0]  in_w0,
    input  logic signed [7:0]  in_w1,
    input  logic signed [8:0]  in_offset0,
    input  logic signed [8:0]  in_offset1,
    input  logic        [3:0]  in_log2_weight_denom,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [7:0]   out_pixel
);

    localparam logic signed [31:0] IF_INTERNAL_OFFS = 32'sd8192;

    logic busy;
    logic [7:0] out_pixel_next;

    logic signed [31:0] p0_s;
    logic signed [31:0] p1_s;
    logic signed [31:0] w0_s;
    logic signed [31:0] w1_s;
    logic signed [31:0] offset0_s;
    logic signed [31:0] offset1_s;
    logic signed [31:0] p_sel;
    logic signed [31:0] w_sel;
    logic signed [31:0] offset_sel;
    logic signed [31:0] shift_uni;
    logic signed [31:0] shift_bi;
    logic signed [63:0] round_uni;
    logic signed [63:0] round_bi;
    logic signed [63:0] prod_uni;
    logic signed [63:0] prod0;
    logic signed [63:0] prod1;
    logic signed [63:0] sum_uni;
    logic signed [63:0] base_uni;
    logic signed [63:0] value_uni;
    logic signed [31:0] offset_sum;
    logic signed [63:0] offset_term_bi;
    logic signed [63:0] sum_bi;
    logic signed [63:0] value_bi;
    logic signed [63:0] value_sel;

    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    function automatic logic [7:0] clip_u8(input logic signed [63:0] value);
        begin
            if (value < 64'sd0)
                clip_u8 = 8'd0;
            else if (value > 64'sd255)
                clip_u8 = 8'd255;
            else
                clip_u8 = value[7:0];
        end
    endfunction

    always_comb begin
        p0_s = in_p0;
        p1_s = in_p1;
        w0_s = in_w0;
        w1_s = in_w1;
        offset0_s = in_offset0;
        offset1_s = in_offset1;

        if (in_src0) begin
            p_sel = p0_s;
            w_sel = w0_s;
            offset_sel = offset0_s;
        end else begin
            p_sel = p1_s;
            w_sel = w1_s;
            offset_sel = offset1_s;
        end

        shift_uni = $signed({1'b0, in_log2_weight_denom}) + 32'sd6;
        shift_bi  = $signed({1'b0, in_log2_weight_denom}) + 32'sd7;

        round_uni = 64'sd1 <<< (shift_uni - 32'sd1);
        round_bi  = 64'sd1 <<< (shift_bi - 32'sd1);

        prod_uni = w_sel * (p_sel + IF_INTERNAL_OFFS);
        sum_uni  = prod_uni + round_uni;
        if (shift_uni == 32'sd0)
            base_uni = sum_uni;
        else
            base_uni = sum_uni >>> shift_uni;
        value_uni = base_uni + offset_sel;

        prod0 = w0_s * (p0_s + IF_INTERNAL_OFFS);
        prod1 = w1_s * (p1_s + IF_INTERNAL_OFFS);
        offset_sum = offset0_s + offset1_s;
        offset_term_bi = $signed(offset_sum) <<< (shift_bi - 32'sd1);
        sum_bi = prod0 + prod1 + round_bi + offset_term_bi;
        if (shift_bi == 32'sd0)
            value_bi = sum_bi;
        else
            value_bi = sum_bi >>> shift_bi;

        if (in_bidir)
            value_sel = value_bi;
        else
            value_sel = value_uni;

        out_pixel_next = clip_u8(value_sel);
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy      <= 1'b0;
            out_pixel <= 8'd0;
        end else if (input_accepted) begin
            busy      <= 1'b1;
            out_pixel <= out_pixel_next;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule