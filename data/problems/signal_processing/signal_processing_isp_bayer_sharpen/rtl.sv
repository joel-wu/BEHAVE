module signal_processing_isp_bayer_sharpen (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [7:0]  in_luma,
    input  logic        in_frame_start,
    input  logic        in_line_start,
    input  logic        in_line_end,
    input  logic        in_frame_end,
    input  logic [9:0]  in_strength,
    input  logic [19:0] in_kernel_0_0,
    input  logic [19:0] in_kernel_0_1,
    input  logic [19:0] in_kernel_0_2,
    input  logic [19:0] in_kernel_0_3,
    input  logic [19:0] in_kernel_0_4,
    input  logic [19:0] in_kernel_0_5,
    input  logic [19:0] in_kernel_0_6,
    input  logic [19:0] in_kernel_0_7,
    input  logic [19:0] in_kernel_0_8,
    input  logic [19:0] in_kernel_1_0,
    input  logic [19:0] in_kernel_1_1,
    input  logic [19:0] in_kernel_1_2,
    input  logic [19:0] in_kernel_1_3,
    input  logic [19:0] in_kernel_1_4,
    input  logic [19:0] in_kernel_1_5,
    input  logic [19:0] in_kernel_1_6,
    input  logic [19:0] in_kernel_1_7,
    input  logic [19:0] in_kernel_1_8,
    input  logic [19:0] in_kernel_2_0,
    input  logic [19:0] in_kernel_2_1,
    input  logic [19:0] in_kernel_2_2,
    input  logic [19:0] in_kernel_2_3,
    input  logic [19:0] in_kernel_2_4,
    input  logic [19:0] in_kernel_2_5,
    input  logic [19:0] in_kernel_2_6,
    input  logic [19:0] in_kernel_2_7,
    input  logic [19:0] in_kernel_2_8,
    input  logic [19:0] in_kernel_3_0,
    input  logic [19:0] in_kernel_3_1,
    input  logic [19:0] in_kernel_3_2,
    input  logic [19:0] in_kernel_3_3,
    input  logic [19:0] in_kernel_3_4,
    input  logic [19:0] in_kernel_3_5,
    input  logic [19:0] in_kernel_3_6,
    input  logic [19:0] in_kernel_3_7,
    input  logic [19:0] in_kernel_3_8,
    input  logic [19:0] in_kernel_4_0,
    input  logic [19:0] in_kernel_4_1,
    input  logic [19:0] in_kernel_4_2,
    input  logic [19:0] in_kernel_4_3,
    input  logic [19:0] in_kernel_4_4,
    input  logic [19:0] in_kernel_4_5,
    input  logic [19:0] in_kernel_4_6,
    input  logic [19:0] in_kernel_4_7,
    input  logic [19:0] in_kernel_4_8,
    input  logic [19:0] in_kernel_5_0,
    input  logic [19:0] in_kernel_5_1,
    input  logic [19:0] in_kernel_5_2,
    input  logic [19:0] in_kernel_5_3,
    input  logic [19:0] in_kernel_5_4,
    input  logic [19:0] in_kernel_5_5,
    input  logic [19:0] in_kernel_5_6,
    input  logic [19:0] in_kernel_5_7,
    input  logic [19:0] in_kernel_5_8,
    input  logic [19:0] in_kernel_6_0,
    input  logic [19:0] in_kernel_6_1,
    input  logic [19:0] in_kernel_6_2,
    input  logic [19:0] in_kernel_6_3,
    input  logic [19:0] in_kernel_6_4,
    input  logic [19:0] in_kernel_6_5,
    input  logic [19:0] in_kernel_6_6,
    input  logic [19:0] in_kernel_6_7,
    input  logic [19:0] in_kernel_6_8,
    input  logic [19:0] in_kernel_7_0,
    input  logic [19:0] in_kernel_7_1,
    input  logic [19:0] in_kernel_7_2,
    input  logic [19:0] in_kernel_7_3,
    input  logic [19:0] in_kernel_7_4,
    input  logic [19:0] in_kernel_7_5,
    input  logic [19:0] in_kernel_7_6,
    input  logic [19:0] in_kernel_7_7,
    input  logic [19:0] in_kernel_7_8,
    input  logic [19:0] in_kernel_8_0,
    input  logic [19:0] in_kernel_8_1,
    input  logic [19:0] in_kernel_8_2,
    input  logic [19:0] in_kernel_8_3,
    input  logic [19:0] in_kernel_8_4,
    input  logic [19:0] in_kernel_8_5,
    input  logic [19:0] in_kernel_8_6,
    input  logic [19:0] in_kernel_8_7,
    input  logic [19:0] in_kernel_8_8,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [7:0]  out_luma,
    output logic        out_frame_start,
    output logic        out_line_start,
    output logic        out_line_end,
    output logic        out_frame_end
);

    logic [7:0]  frame_mem [0:7][0:7];
    logic [3:0]  line_width [0:7];
    logic [3:0]  line_index;
    logic [3:0]  column_index;

    logic [19:0] kernel_mem [0:8][0:8];
    logic [9:0]  strength_reg;

    logic        busy;
    logic [3:0]  out_y_idx;
    logic [3:0]  out_x_idx;
    logic [3:0]  frame_height;

    integer r;
    integer c;
    integer dy_i;
    integer dx_i;
    integer sy_i;
    integer sx_i;

    logic [47:0] correlation_c;
    logic [7:0]  pval_c;
    logic [7:0]  center_c;
    logic [27:0] smoothened_c;
    logic signed [28:0] edge_c;
    logic signed [38:0] sharpen_c;
    logic signed [28:0] sharpened_c;
    logic signed [31:0] outy_c;
    logic [7:0]  out_luma_calc;

    function automatic logic [7:0] clip_u8(input logic signed [31:0] val);
        begin
            clip_u8 = 8'd0;
            if (val < 0)
                clip_u8 = 8'd0;
            else if (val > 32'sd255)
                clip_u8 = 8'd255;
            else
                clip_u8 = val[7:0];
        end
    endfunction

    wire input_accepted    = in_valid & in_ready;
    wire output_consumed   = out_valid & out_ready;
    wire current_line_last = (out_x_idx == (line_width[out_y_idx[2:0]] - 4'd1));
    wire frame_last_line   = (out_y_idx == (frame_height - 4'd1));
    wire frame_complete    = output_consumed & current_line_last & frame_last_line;

    assign out_valid = busy;
    assign in_ready  = (~busy) | frame_complete;

    always_comb begin
        sy_i         = 0;
        sx_i         = 0;
        correlation_c = 48'd0;
        pval_c        = 8'd0;
        center_c      = 8'd0;
        smoothened_c  = 28'd0;
        edge_c        = 29'sd0;
        sharpen_c     = 39'sd0;
        sharpened_c   = 29'sd0;
        outy_c        = 32'sd0;
        out_luma_calc = 8'd0;

        if (busy) begin
            for (dy_i = 0; dy_i < 9; dy_i = dy_i + 1) begin
                sy_i = $signed({28'd0, out_y_idx}) + dy_i - 32'sd4;
                for (dx_i = 0; dx_i < 9; dx_i = dx_i + 1) begin
                    sx_i = $signed({28'd0, out_x_idx}) + dx_i - 32'sd4;
                    pval_c = 8'd0;
                    if ((sy_i >= 0) && (sy_i < 8) && (sx_i >= 0) && (sx_i < 8)) begin
                        if ((sy_i < $signed({28'd0, frame_height})) && (sx_i < $signed({28'd0, line_width[sy_i[2:0]]})))
                            pval_c = frame_mem[sy_i[2:0]][sx_i[2:0]];
                    end
                    correlation_c = correlation_c + (pval_c * kernel_mem[dy_i][dx_i]);
                end
            end

            smoothened_c = correlation_c[47:20];
            center_c     = frame_mem[out_y_idx[2:0]][out_x_idx[2:0]];
            edge_c       = $signed({{21{1'b0}}, center_c}) - $signed({1'b0, smoothened_c});
            sharpen_c    = $signed({{29{1'b0}}, strength_reg}) * $signed({{10{edge_c[28]}}, edge_c});

            sharpened_c = $signed(sharpen_c[38:10]);

            outy_c        = $signed({{24{1'b0}}, center_c}) + $signed({{3{sharpened_c[28]}}, sharpened_c});
            out_luma_calc = clip_u8(outy_c);
        end

        out_luma        = out_luma_calc;
        out_frame_start = 1'b0;
        out_line_start  = 1'b0;
        out_line_end    = 1'b0;
        out_frame_end   = 1'b0;

        if (busy) begin
            out_frame_start = (out_y_idx == 4'd0) && (out_x_idx == 4'd0);
            out_line_start  = (out_x_idx == 4'd0);
            out_line_end    = current_line_last;
            out_frame_end   = current_line_last && frame_last_line;
        end
    end

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            busy         <= 1'b0;
            line_index   <= 4'd0;
            column_index <= 4'd0;
            frame_height <= 4'd0;
            out_y_idx    <= 4'd0;
            out_x_idx    <= 4'd0;
            strength_reg <= 10'd0;
            for (r = 0; r < 8; r = r + 1) begin
                line_width[r] <= 4'd0;
                for (c = 0; c < 8; c = c + 1) begin
                    frame_mem[r][c] <= 8'd0;
                end
            end
            for (r = 0; r < 9; r = r + 1) begin
                for (c = 0; c < 9; c = c + 1) begin
                    kernel_mem[r][c] <= 20'd0;
                end
            end
        end else begin
            if (frame_complete) begin
                busy         <= 1'b0;
                out_y_idx    <= 4'd0;
                out_x_idx    <= 4'd0;
                frame_height <= 4'd0;
                strength_reg <= 10'd0;
                for (r = 0; r < 8; r = r + 1) begin
                    line_width[r] <= 4'd0;
                    for (c = 0; c < 8; c = c + 1) begin
                        frame_mem[r][c] <= 8'd0;
                    end
                end
                line_index   <= 4'd0;
                column_index <= 4'd0;
            end else if (output_consumed) begin
                if (current_line_last) begin
                    out_y_idx <= out_y_idx + 4'd1;
                    out_x_idx <= 4'd0;
                end else begin
                    out_y_idx <= out_y_idx;
                    out_x_idx <= out_x_idx + 4'd1;
                end
            end

            if (input_accepted) begin
                logic [3:0] work_line_index;
                logic [3:0] work_column_index;
                logic [3:0] y;
                logic [3:0] x;
                logic [3:0] col_after;
                logic [3:0] line_after;

                // If a prior frame is completing on this same cycle,
                // its NBA clears of line_index/column_index/line_width/frame_mem
                // are not visible yet to the read here. Compensate by
                // treating those as cleared so this new pixel sees fresh state.
                if (frame_complete) begin
                    work_line_index   = 4'd0;
                    work_column_index = 4'd0;
                end else begin
                    work_line_index   = line_index;
                    work_column_index = column_index;
                end

                if (in_frame_start) begin
                    for (r = 0; r < 8; r = r + 1) begin
                        line_width[r] <= 4'd0;
                        for (c = 0; c < 8; c = c + 1) begin
                            frame_mem[r][c] <= 8'd0;
                        end
                    end
                    work_line_index   = 4'd0;
                    work_column_index = 4'd0;
                end

                if (in_line_start)
                    work_column_index = 4'd0;

                y = work_line_index;
                x = work_column_index;

                if ((y < 8) && (x < 8))
                    frame_mem[y[2:0]][x[2:0]] <= in_luma;

                col_after  = x + 4'd1;
                line_after = work_line_index;

                if (in_line_end) begin
                    if (y < 8)
                        line_width[y[2:0]] <= col_after;
                    line_after = work_line_index + 4'd1;
                end

                column_index <= col_after;
                line_index   <= line_after;

                if (in_frame_end) begin
                    strength_reg <= in_strength;
                    frame_height <= line_after;

                    kernel_mem[0][0] <= in_kernel_0_0; kernel_mem[0][1] <= in_kernel_0_1; kernel_mem[0][2] <= in_kernel_0_2; kernel_mem[0][3] <= in_kernel_0_3; kernel_mem[0][4] <= in_kernel_0_4; kernel_mem[0][5] <= in_kernel_0_5; kernel_mem[0][6] <= in_kernel_0_6; kernel_mem[0][7] <= in_kernel_0_7; kernel_mem[0][8] <= in_kernel_0_8;
                    kernel_mem[1][0] <= in_kernel_1_0; kernel_mem[1][1] <= in_kernel_1_1; kernel_mem[1][2] <= in_kernel_1_2; kernel_mem[1][3] <= in_kernel_1_3; kernel_mem[1][4] <= in_kernel_1_4; kernel_mem[1][5] <= in_kernel_1_5; kernel_mem[1][6] <= in_kernel_1_6; kernel_mem[1][7] <= in_kernel_1_7; kernel_mem[1][8] <= in_kernel_1_8;
                    kernel_mem[2][0] <= in_kernel_2_0; kernel_mem[2][1] <= in_kernel_2_1; kernel_mem[2][2] <= in_kernel_2_2; kernel_mem[2][3] <= in_kernel_2_3; kernel_mem[2][4] <= in_kernel_2_4; kernel_mem[2][5] <= in_kernel_2_5; kernel_mem[2][6] <= in_kernel_2_6; kernel_mem[2][7] <= in_kernel_2_7; kernel_mem[2][8] <= in_kernel_2_8;
                    kernel_mem[3][0] <= in_kernel_3_0; kernel_mem[3][1] <= in_kernel_3_1; kernel_mem[3][2] <= in_kernel_3_2; kernel_mem[3][3] <= in_kernel_3_3; kernel_mem[3][4] <= in_kernel_3_4; kernel_mem[3][5] <= in_kernel_3_5; kernel_mem[3][6] <= in_kernel_3_6; kernel_mem[3][7] <= in_kernel_3_7; kernel_mem[3][8] <= in_kernel_3_8;
                    kernel_mem[4][0] <= in_kernel_4_0; kernel_mem[4][1] <= in_kernel_4_1; kernel_mem[4][2] <= in_kernel_4_2; kernel_mem[4][3] <= in_kernel_4_3; kernel_mem[4][4] <= in_kernel_4_4; kernel_mem[4][5] <= in_kernel_4_5; kernel_mem[4][6] <= in_kernel_4_6; kernel_mem[4][7] <= in_kernel_4_7; kernel_mem[4][8] <= in_kernel_4_8;
                    kernel_mem[5][0] <= in_kernel_5_0; kernel_mem[5][1] <= in_kernel_5_1; kernel_mem[5][2] <= in_kernel_5_2; kernel_mem[5][3] <= in_kernel_5_3; kernel_mem[5][4] <= in_kernel_5_4; kernel_mem[5][5] <= in_kernel_5_5; kernel_mem[5][6] <= in_kernel_5_6; kernel_mem[5][7] <= in_kernel_5_7; kernel_mem[5][8] <= in_kernel_5_8;
                    kernel_mem[6][0] <= in_kernel_6_0; kernel_mem[6][1] <= in_kernel_6_1; kernel_mem[6][2] <= in_kernel_6_2; kernel_mem[6][3] <= in_kernel_6_3; kernel_mem[6][4] <= in_kernel_6_4; kernel_mem[6][5] <= in_kernel_6_5; kernel_mem[6][6] <= in_kernel_6_6; kernel_mem[6][7] <= in_kernel_6_7; kernel_mem[6][8] <= in_kernel_6_8;
                    kernel_mem[7][0] <= in_kernel_7_0; kernel_mem[7][1] <= in_kernel_7_1; kernel_mem[7][2] <= in_kernel_7_2; kernel_mem[7][3] <= in_kernel_7_3; kernel_mem[7][4] <= in_kernel_7_4; kernel_mem[7][5] <= in_kernel_7_5; kernel_mem[7][6] <= in_kernel_7_6; kernel_mem[7][7] <= in_kernel_7_7; kernel_mem[7][8] <= in_kernel_7_8;
                    kernel_mem[8][0] <= in_kernel_8_0; kernel_mem[8][1] <= in_kernel_8_1; kernel_mem[8][2] <= in_kernel_8_2; kernel_mem[8][3] <= in_kernel_8_3; kernel_mem[8][4] <= in_kernel_8_4; kernel_mem[8][5] <= in_kernel_8_5; kernel_mem[8][6] <= in_kernel_8_6; kernel_mem[8][7] <= in_kernel_8_7; kernel_mem[8][8] <= in_kernel_8_8;

                    out_y_idx <= 4'd0;
                    out_x_idx <= 4'd0;

                    if (line_after != 4'd0) begin
                        busy <= 1'b1;
                    end else begin
                        // Empty frame (no completed lines) -> emit zero outputs
                        // and clear state so subsequent frame_start works cleanly.
                        busy         <= 1'b0;
                        frame_height <= 4'd0;
                        strength_reg <= 10'd0;
                        for (r = 0; r < 8; r = r + 1) begin
                            line_width[r] <= 4'd0;
                            for (c = 0; c < 8; c = c + 1) begin
                                frame_mem[r][c] <= 8'd0;
                            end
                        end
                        line_index   <= 4'd0;
                        column_index <= 4'd0;
                    end
                end
            end
        end
    end

endmodule
