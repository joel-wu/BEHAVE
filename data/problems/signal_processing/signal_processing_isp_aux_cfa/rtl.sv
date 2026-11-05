module signal_processing_isp_aux_cfa (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [1:0]  in_bayer_pattern,
    input  logic [15:0] in_clip,

    input  logic [15:0] in_p00,
    input  logic [15:0] in_p01,
    input  logic [15:0] in_p02,
    input  logic [15:0] in_p03,
    input  logic [15:0] in_p04,
    input  logic [15:0] in_p05,
    input  logic [15:0] in_p10,
    input  logic [15:0] in_p11,
    input  logic [15:0] in_p12,
    input  logic [15:0] in_p13,
    input  logic [15:0] in_p14,
    input  logic [15:0] in_p15,
    input  logic [15:0] in_p20,
    input  logic [15:0] in_p21,
    input  logic [15:0] in_p22,
    input  logic [15:0] in_p23,
    input  logic [15:0] in_p24,
    input  logic [15:0] in_p25,
    input  logic [15:0] in_p30,
    input  logic [15:0] in_p31,
    input  logic [15:0] in_p32,
    input  logic [15:0] in_p33,
    input  logic [15:0] in_p34,
    input  logic [15:0] in_p35,
    input  logic [15:0] in_p40,
    input  logic [15:0] in_p41,
    input  logic [15:0] in_p42,
    input  logic [15:0] in_p43,
    input  logic [15:0] in_p44,
    input  logic [15:0] in_p45,

    input  logic [15:0] in_p50,
    input  logic [15:0] in_p51,
    input  logic [15:0] in_p52,
    input  logic [15:0] in_p53,
    input  logic [15:0] in_p54,
    input  logic [15:0] in_p55,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [1:0]  out_pixel_index,
    output logic [15:0] out_r,
    output logic [15:0] out_g,
    output logic [15:0] out_b
);

function automatic logic [15:0] clip_channel(
    input logic signed [31:0] numerator, input logic [15:0] limit
);
    logic signed [31:0] value, wide_limit;
    begin
        wide_limit = {16'b0, limit};
        value = numerator / 32'sd16;
        if (value < 0) clip_channel = 16'd0;
        else if (value > wide_limit) clip_channel = limit;
        else clip_channel = value[15:0];
    end
endfunction

logic signed [31:0] p [0:5][0:5];
logic signed [31:0] c, far_v, far_h, diagonal, near_v, near_h;
logic signed [31:0] green, opposite, horizontal, vertical, red_num, green_num, blue_num;
logic [15:0] next_r [0:3], next_g [0:3], next_b [0:3];
logic [15:0] held_r [0:3], held_g [0:3], held_b [0:3];
logic [1:0] kind, pattern_mask, index;
logic busy;
integer y, x;

assign out_valid = busy;
assign in_ready = !busy || (out_ready && index == 2'd3);
assign out_pixel_index = index;
assign out_r = held_r[index];
assign out_g = held_g[index];
assign out_b = held_b[index];

always_comb begin
    p[0][0] = {16'b0, in_p00};
    p[0][1] = {16'b0, in_p01};
    p[0][2] = {16'b0, in_p02};
    p[0][3] = {16'b0, in_p03};
    p[0][4] = {16'b0, in_p04};
    p[0][5] = {16'b0, in_p05};
    p[1][0] = {16'b0, in_p10};
    p[1][1] = {16'b0, in_p11};
    p[1][2] = {16'b0, in_p12};
    p[1][3] = {16'b0, in_p13};
    p[1][4] = {16'b0, in_p14};
    p[1][5] = {16'b0, in_p15};
    p[2][0] = {16'b0, in_p20};
    p[2][1] = {16'b0, in_p21};
    p[2][2] = {16'b0, in_p22};
    p[2][3] = {16'b0, in_p23};
    p[2][4] = {16'b0, in_p24};
    p[2][5] = {16'b0, in_p25};
    p[3][0] = {16'b0, in_p30};
    p[3][1] = {16'b0, in_p31};
    p[3][2] = {16'b0, in_p32};
    p[3][3] = {16'b0, in_p33};
    p[3][4] = {16'b0, in_p34};
    p[3][5] = {16'b0, in_p35};
    p[4][0] = {16'b0, in_p40};
    p[4][1] = {16'b0, in_p41};
    p[4][2] = {16'b0, in_p42};
    p[4][3] = {16'b0, in_p43};
    p[4][4] = {16'b0, in_p44};
    p[4][5] = {16'b0, in_p45};
    p[5][0] = {16'b0, in_p50};
    p[5][1] = {16'b0, in_p51};
    p[5][2] = {16'b0, in_p52};
    p[5][3] = {16'b0, in_p53};
    p[5][4] = {16'b0, in_p54};
    p[5][5] = {16'b0, in_p55};
    case (in_bayer_pattern)
        2'd0: pattern_mask = 2'd0;
        2'd1: pattern_mask = 2'd3;
        2'd2: pattern_mask = 2'd2;
        default: pattern_mask = 2'd1;
    endcase
    for (integer i = 0; i < 4; i = i + 1) begin
        y = 2 + i/2;
        x = 2 + i%2;
        kind = i[1:0] ^ pattern_mask;
        c = p[y][x];
        far_v = p[y-2][x] + p[y+2][x];
        far_h = p[y][x-2] + p[y][x+2];
        diagonal = p[y-1][x-1] + p[y-1][x+1] + p[y+1][x-1] + p[y+1][x+1];
        near_v = p[y-1][x] + p[y+1][x];
        near_h = p[y][x-1] + p[y][x+1];
        green = 8*c - 2*(far_v+far_h) + 4*(near_v+near_h);
        opposite = 12*c - 3*(far_v+far_h) + 4*diagonal;
        horizontal = 10*c - 2*far_h - 2*diagonal + far_v + 8*near_h;
        vertical = 10*c - 2*far_v - 2*diagonal + far_h + 8*near_v;
        case (kind)
            2'd0: begin red_num=16*c; green_num=green; blue_num=opposite; end
            2'd1: begin red_num=horizontal; green_num=16*c; blue_num=vertical; end
            2'd2: begin red_num=vertical; green_num=16*c; blue_num=horizontal; end
            default: begin red_num=opposite; green_num=green; blue_num=16*c; end
        endcase
        next_r[i] = clip_channel(red_num, in_clip);
        next_g[i] = clip_channel(green_num, in_clip);
        next_b[i] = clip_channel(blue_num, in_clip);
    end
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy <= 1'b0;
        index <= 2'd0;
        for (integer i = 0; i < 4; i = i + 1) begin
            held_r[i] <= 16'd0;
            held_g[i] <= 16'd0;
            held_b[i] <= 16'd0;
        end
    end else if (in_valid && in_ready) begin
        busy <= 1'b1;
        index <= 2'd0;
        for (integer i = 0; i < 4; i = i + 1) begin
            held_r[i] <= next_r[i];
            held_g[i] <= next_g[i];
            held_b[i] <= next_b[i];
        end
    end else if (out_valid && out_ready) begin
        if (index == 2'd3) begin
            busy <= 1'b0;
            index <= 2'd0;
        end else index <= index + 2'd1;
    end
end
endmodule
