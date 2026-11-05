module signal_processing_isp_bayer_demosaic (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [1:0]   in_pattern,
    input  logic [4:0]   in_bit_depth,
    input  logic         in_row_lsb,
    input  logic         in_col_lsb,
    input  logic [15:0]  in_p00,
    input  logic [15:0]  in_p01,
    input  logic [15:0]  in_p02,
    input  logic [15:0]  in_p03,
    input  logic [15:0]  in_p04,
    input  logic [15:0]  in_p10,
    input  logic [15:0]  in_p11,
    input  logic [15:0]  in_p12,
    input  logic [15:0]  in_p13,
    input  logic [15:0]  in_p14,
    input  logic [15:0]  in_p20,
    input  logic [15:0]  in_p21,
    input  logic [15:0]  in_p22,
    input  logic [15:0]  in_p23,
    input  logic [15:0]  in_p24,
    input  logic [15:0]  in_p30,
    input  logic [15:0]  in_p31,
    input  logic [15:0]  in_p32,
    input  logic [15:0]  in_p33,
    input  logic [15:0]  in_p34,
    input  logic [15:0]  in_p40,
    input  logic [15:0]  in_p41,
    input  logic [15:0]  in_p42,
    input  logic [15:0]  in_p43,
    input  logic [15:0]  in_p44,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_r,
    output logic [15:0]  out_g,
    output logic [15:0]  out_b
);

    logic busy, red_row, red_col;
    logic signed [31:0] c, near_h, near_v, far_h, far_v, diagonal;
    logic signed [31:0] green, horizontal, vertical, opposite;
    logic signed [31:0] r, g, b;

    function automatic logic [15:0] clip(
        input logic signed [31:0] value, input logic [4:0] depth
    );
        logic signed [31:0] maximum;
        begin
            maximum = depth >= 16 ? 32'sd65535 : (32'sd1 <<< depth)-1;
            if (value < 0) clip = 0;
            else if (value > maximum) clip = maximum[15:0];
            else clip = value[15:0];
        end
    endfunction

    assign in_ready = !busy || out_ready;
    assign out_valid = busy;

    always_comb begin
        c = $signed({16'd0,in_p22});
        near_h = $signed({16'd0,in_p21}) + $signed({16'd0,in_p23});
        near_v = $signed({16'd0,in_p12}) + $signed({16'd0,in_p32});
        far_h = $signed({16'd0,in_p20}) + $signed({16'd0,in_p24});
        far_v = $signed({16'd0,in_p02}) + $signed({16'd0,in_p42});
        diagonal = $signed({16'd0,in_p11}) + $signed({16'd0,in_p13}) +
                   $signed({16'd0,in_p31}) + $signed({16'd0,in_p33});
        green = (8*c + 4*(near_h+near_v) - 2*(far_h+far_v)) >>> 4;
        horizontal = (10*c + 8*near_h - 2*far_h + far_v - 2*diagonal) >>> 4;
        vertical = (10*c + 8*near_v - 2*far_v + far_h - 2*diagonal) >>> 4;
        opposite = (12*c + 4*diagonal - 3*(far_h+far_v)) >>> 4;
        red_row = in_pattern[0];
        red_col = in_pattern[0] ^ in_pattern[1];
        if (in_row_lsb == red_row && in_col_lsb == red_col) begin
            r = c; g = green; b = opposite;
        end else if (in_row_lsb != red_row && in_col_lsb != red_col) begin
            r = opposite; g = green; b = c;
        end else if (in_row_lsb == red_row) begin
            r = horizontal; g = c; b = vertical;
        end else begin
            r = vertical; g = c; b = horizontal;
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy <= 0;
            out_r <= 0; out_g <= 0; out_b <= 0;
        end else if (in_ready) begin
            busy <= in_valid;
            if (in_valid) begin
                out_r <= clip(r,in_bit_depth);
                out_g <= clip(g,in_bit_depth);
                out_b <= clip(b,in_bit_depth);
            end
        end
    end
endmodule
