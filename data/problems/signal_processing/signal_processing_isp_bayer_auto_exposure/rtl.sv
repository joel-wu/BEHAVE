module signal_processing_isp_bayer_auto_exposure (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_top_offset,
    input  logic [15:0]  in_bottom_offset,
    input  logic [15:0]  in_left_offset,
    input  logic [15:0]  in_right_offset,
    input  logic [7:0]   in_center_illuminance,
    input  logic [15:0]  in_histogram_skewness,
    input  logic [127:0] in_pixels,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [1:0]   out_feedback,
    output logic signed [63:0] out_skewness
);

logic busy;
wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;
assign in_ready        = ~busy | output_consumed;
assign out_valid       = busy;

function automatic [13:0] source_sqrt(input logic [13:0] value);
    integer k;
    logic [14:0] estimate;
    begin
        estimate = value >> 1;
        for (k = 0; k < 13; k = k + 1) begin
            if (estimate != 0)
                estimate = (estimate + value / estimate) >> 1;
        end
        source_sqrt = estimate[13:0];
    end
endfunction

integer row;
integer col;
integer idx;

logic [15:0] top_v;
logic [15:0] bottom_v;
logic [15:0] left_v;
logic [15:0] right_v;
logic [7:0] center_v;
logic [15:0] threshold_v;

logic [7:0] p_v;
logic include_v;
logic signed [9:0] d_v;
logic [19:0] d2_v;
logic signed [29:0] d3_v;

logic [4:0] count_v;
logic [19:0] sum2_v;
logic signed [29:0] sum3_v;

logic [19:0] m2_v;
logic signed [29:0] m3_v;
logic [29:0] abs_m3_v;

logic [29:0] m3_shift_tmp_v;

logic [13:0] m2_shift_v;
logic [20:0] m3_shift_v;
logic [13:0] sqrt_m2_v;
logic [21:0] denominator_v;
logic [28:0] numerator_v;
logic [28:0] q_v;

logic signed [63:0] skewness_v;
logic signed [63:0] threshold64_v;
logic [1:0] feedback_v;

always_comb begin
    top_v         = in_top_offset;
    left_v        = in_left_offset;
    center_v      = in_center_illuminance;
    threshold_v   = in_histogram_skewness;
    bottom_v      = 16'd0;
    right_v       = 16'd0;
    p_v           = 8'd0;
    include_v     = 1'b0;
    d_v           = 10'sd0;
    d2_v          = 20'd0;
    d3_v          = 30'sd0;
    count_v       = 5'd0;
    sum2_v        = 20'd0;
    sum3_v        = 30'sd0;
    m2_v          = 20'd0;
    m3_v          = 30'sd0;
    abs_m3_v      = 30'd0;
    m3_shift_tmp_v = 30'd0;
    m2_shift_v    = 14'd0;
    m3_shift_v    = 21'd0;
    sqrt_m2_v     = 14'd0;
    denominator_v = 22'd0;
    numerator_v   = 29'd0;
    q_v           = 29'd0;
    skewness_v    = 64'sd0;
    threshold64_v = 64'sd0;
    feedback_v    = 2'b00;

    if (in_bottom_offset == 16'd0) begin
        bottom_v = 16'd4;
    end else begin
        bottom_v = in_bottom_offset >= 4 ? 16'd0 : 16'd4 - in_bottom_offset;
    end

    if (in_right_offset == 16'd0) begin
        right_v = 16'd4;
    end else begin
        right_v = in_right_offset >= 4 ? 16'd0 : 16'd4 - in_right_offset;
    end

    for (row = 0; row < 4; row = row + 1) begin
        for (col = 0; col < 4; col = col + 1) begin
            idx = (row << 2) + col;
            p_v = in_pixels[idx*8 +: 8];

            include_v = 1'b0;
            if ((row >= top_v) && (row < bottom_v) &&
                (col >= left_v) && (col < right_v)) begin
                include_v = 1'b1;
            end

            if (include_v) begin
                d_v  = $signed({2'b00, p_v}) - $signed({2'b00, center_v});
                d2_v = d_v * d_v;
                d3_v = d_v * $signed({1'b0, d2_v});

                count_v = count_v + 5'd1;
                sum2_v  = sum2_v + d2_v;
                sum3_v  = sum3_v + d3_v;
            end
        end
    end

    if (count_v != 5'd0) begin
        m2_v = sum2_v / count_v;
        if (sum3_v >= 30'sd0) begin
            m3_v = sum3_v / $signed({1'b0, count_v});
        end else begin
            m3_v = -((-sum3_v) / $signed({1'b0, count_v}));
        end
    end

    if (m3_v < 30'sd0) begin
        abs_m3_v = -m3_v;
    end else begin
        abs_m3_v = m3_v[29:0];
    end

    m2_shift_v     = m2_v[19:6];
    m3_shift_tmp_v = abs_m3_v >> 9;
    m3_shift_v     = m3_shift_tmp_v[20:0];

    sqrt_m2_v     = source_sqrt(m2_shift_v);
    denominator_v = m2_shift_v * sqrt_m2_v;
    numerator_v   = m3_shift_v * 21'd256;

    if (denominator_v != 22'd0) begin
        q_v = numerator_v / {7'd0, denominator_v};
    end

    if (m3_v < 30'sd0) begin
        skewness_v = -$signed({35'd0, q_v});
    end else begin
        skewness_v =  $signed({35'd0, q_v});
    end

    threshold64_v = $signed({48'd0, threshold_v});

    if (skewness_v < -threshold64_v) begin
        feedback_v = 2'b11;
    end else if (skewness_v > threshold64_v) begin
        feedback_v = 2'b01;
    end
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy         <= 1'b0;
        out_feedback <= 2'b00;
        out_skewness <= 64'sd0;
    end else if (input_accepted) begin
        busy         <= 1'b1;
        out_feedback <= feedback_v;
        out_skewness <= skewness_v;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule
