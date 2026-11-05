module signal_processing_isp_bayer_dead_pixel_correction #(
    parameter int PIXEL_W = 16,
    parameter int THRESH_W = 16
) (
    input  logic                   clk,
    input  logic                   rst,

    input  logic                   in_valid,
    output logic                   in_ready,
    input  logic [PIXEL_W-1:0]     in_center,
    input  logic [PIXEL_W-1:0]     in_top_left,
    input  logic [PIXEL_W-1:0]     in_top_mid,
    input  logic [PIXEL_W-1:0]     in_top_right,
    input  logic [PIXEL_W-1:0]     in_mid_left,
    input  logic [PIXEL_W-1:0]     in_mid_right,
    input  logic [PIXEL_W-1:0]     in_bottom_left,
    input  logic [PIXEL_W-1:0]     in_bottom_mid,
    input  logic [PIXEL_W-1:0]     in_bottom_right,
    input  logic [THRESH_W-1:0]    in_threshold,

    output logic                   out_valid,
    input  logic                   out_ready,
    output logic [PIXEL_W-1:0]     out_pixel,
    output logic                   out_is_dead
);

    localparam int DIFF_W = PIXEL_W + 1;
    localparam int GRAD_W = PIXEL_W + 2;

    logic busy;
    wire input_accepted;
    wire output_consumed;

    logic [PIXEL_W-1:0] neighbor_min;
    logic [PIXEL_W-1:0] neighbor_max;

    logic [DIFF_W-1:0] d0;
    logic [DIFF_W-1:0] d1;
    logic [DIFF_W-1:0] d2;
    logic [DIFF_W-1:0] d3;
    logic [DIFF_W-1:0] d4;
    logic [DIFF_W-1:0] d5;
    logic [DIFF_W-1:0] d6;
    logic [DIFF_W-1:0] d7;

    logic cond1;
    logic cond2;
    logic is_dead_comb;

    logic signed [GRAD_W-1:0] vtmp;
    logic signed [GRAD_W-1:0] htmp;
    logic signed [GRAD_W-1:0] ltmp;
    logic signed [GRAD_W-1:0] rtmp;

    logic [GRAD_W-1:0] vertical_grad;
    logic [GRAD_W-1:0] horizontal_grad;
    logic [GRAD_W-1:0] left_diagonal_grad;
    logic [GRAD_W-1:0] right_diagonal_grad;

    logic [PIXEL_W:0] sum_vertical;
    logic [PIXEL_W:0] sum_horizontal;
    logic [PIXEL_W:0] sum_left_diagonal;
    logic [PIXEL_W:0] sum_right_diagonal;

    logic [PIXEL_W-1:0] mean_vertical;
    logic [PIXEL_W-1:0] mean_horizontal;
    logic [PIXEL_W-1:0] mean_left_diagonal;
    logic [PIXEL_W-1:0] mean_right_diagonal;

    logic [PIXEL_W-1:0] out_pixel_next;
    logic               out_is_dead_next;

    function automatic logic [PIXEL_W-1:0] min2_pix(
        input logic [PIXEL_W-1:0] a,
        input logic [PIXEL_W-1:0] b
    );
        begin
            if (a < b) min2_pix = a;
            else       min2_pix = b;
        end
    endfunction

    function automatic logic [PIXEL_W-1:0] max2_pix(
        input logic [PIXEL_W-1:0] a,
        input logic [PIXEL_W-1:0] b
    );
        begin
            if (a > b) max2_pix = a;
            else       max2_pix = b;
        end
    endfunction

    function automatic logic [DIFF_W-1:0] abs_diff_u(
        input logic [PIXEL_W-1:0] a,
        input logic [PIXEL_W-1:0] b
    );
        begin
            if (a >= b) abs_diff_u = {{1'b0}, a} - {{1'b0}, b};
            else        abs_diff_u = {{1'b0}, b} - {{1'b0}, a};
        end
    endfunction

    function automatic logic [GRAD_W-1:0] abs_signed_grad(
        input logic signed [GRAD_W-1:0] value
    );
        logic signed [GRAD_W-1:0] neg_value;
        begin
            neg_value = -value;
            if (value >= 0) abs_signed_grad = value[GRAD_W-1:0];
            else            abs_signed_grad = neg_value[GRAD_W-1:0];
        end
    endfunction

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_comb begin
        neighbor_min = in_top_left;
        neighbor_min = min2_pix(neighbor_min, in_top_mid);
        neighbor_min = min2_pix(neighbor_min, in_top_right);
        neighbor_min = min2_pix(neighbor_min, in_mid_left);
        neighbor_min = min2_pix(neighbor_min, in_mid_right);
        neighbor_min = min2_pix(neighbor_min, in_bottom_left);
        neighbor_min = min2_pix(neighbor_min, in_bottom_mid);
        neighbor_min = min2_pix(neighbor_min, in_bottom_right);

        neighbor_max = in_top_left;
        neighbor_max = max2_pix(neighbor_max, in_top_mid);
        neighbor_max = max2_pix(neighbor_max, in_top_right);
        neighbor_max = max2_pix(neighbor_max, in_mid_left);
        neighbor_max = max2_pix(neighbor_max, in_mid_right);
        neighbor_max = max2_pix(neighbor_max, in_bottom_left);
        neighbor_max = max2_pix(neighbor_max, in_bottom_mid);
        neighbor_max = max2_pix(neighbor_max, in_bottom_right);

        d0 = abs_diff_u(in_center, in_top_left);
        d1 = abs_diff_u(in_center, in_top_mid);
        d2 = abs_diff_u(in_center, in_top_right);
        d3 = abs_diff_u(in_center, in_mid_left);
        d4 = abs_diff_u(in_center, in_mid_right);
        d5 = abs_diff_u(in_center, in_bottom_left);
        d6 = abs_diff_u(in_center, in_bottom_mid);
        d7 = abs_diff_u(in_center, in_bottom_right);

        cond1 = (in_center <= neighbor_min) | (in_center >= neighbor_max);
        cond2 = (d0 > in_threshold) & (d1 > in_threshold) &
                (d2 > in_threshold) & (d3 > in_threshold) &
                (d4 > in_threshold) & (d5 > in_threshold) &
                (d6 > in_threshold) & (d7 > in_threshold);

        is_dead_comb = cond1 & cond2;

        vtmp = $signed({1'b0, in_center, 1'b0}) - $signed({2'b00, in_top_mid}) - $signed({2'b00, in_bottom_mid});
        htmp = $signed({1'b0, in_center, 1'b0}) - $signed({2'b00, in_mid_left}) - $signed({2'b00, in_mid_right});
        ltmp = $signed({1'b0, in_center, 1'b0}) - $signed({2'b00, in_top_left}) - $signed({2'b00, in_bottom_right});
        rtmp = $signed({1'b0, in_center, 1'b0}) - $signed({2'b00, in_top_right}) - $signed({2'b00, in_bottom_left});

        vertical_grad       = abs_signed_grad(vtmp);
        horizontal_grad     = abs_signed_grad(htmp);
        left_diagonal_grad  = abs_signed_grad(ltmp);
        right_diagonal_grad = abs_signed_grad(rtmp);

        sum_vertical       = {1'b0, in_top_mid} + {1'b0, in_bottom_mid};
        sum_horizontal     = {1'b0, in_mid_left} + {1'b0, in_mid_right};
        sum_left_diagonal  = {1'b0, in_top_left} + {1'b0, in_bottom_right};
        sum_right_diagonal = {1'b0, in_top_right} + {1'b0, in_bottom_left};

        mean_vertical       = sum_vertical[PIXEL_W:1];
        mean_horizontal     = sum_horizontal[PIXEL_W:1];
        mean_left_diagonal  = sum_left_diagonal[PIXEL_W:1];
        mean_right_diagonal = sum_right_diagonal[PIXEL_W:1];

        out_pixel_next   = in_center;
        out_is_dead_next = 1'b0;

        if (is_dead_comb) begin
            out_is_dead_next = 1'b1;
            if ((vertical_grad <= horizontal_grad) &&
                (vertical_grad <= left_diagonal_grad) &&
                (vertical_grad <= right_diagonal_grad)) begin
                out_pixel_next = mean_vertical;
            end else if ((horizontal_grad <= left_diagonal_grad) &&
                         (horizontal_grad <= right_diagonal_grad)) begin
                out_pixel_next = mean_horizontal;
            end else if (left_diagonal_grad <= right_diagonal_grad) begin
                out_pixel_next = mean_left_diagonal;
            end else begin
                out_pixel_next = mean_right_diagonal;
            end
        end
    end

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            busy        <= 1'b0;
            out_pixel   <= '0;
            out_is_dead <= 1'b0;
        end else if (input_accepted) begin
            busy        <= 1'b1;
            out_pixel   <= out_pixel_next;
            out_is_dead <= out_is_dead_next;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
