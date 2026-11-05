module signal_processing_isp_bayer_color_space_conversion (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [1:0]  in_conv_standard,
    input  logic [4:0]  in_bit_depth,
    input  logic [15:0] in_r,
    input  logic [15:0] in_g,
    input  logic [15:0] in_b,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [7:0]  out_y,
    output logic [7:0]  out_u,
    output logic [7:0]  out_v
);

    logic busy;
    logic [15:0] mask16;
    logic signed [31:0] r, g, b;
    logic signed [31:0] y, u, v;

    function automatic logic signed [31:0] round_q8(input logic signed [31:0] value);
        if (value >= 0) round_q8 = (value + 32'sd128) >>> 8;
        else round_q8 = -((-value + 32'sd128) >>> 8);
    endfunction

    function automatic logic signed [31:0] luma_offset(input logic [4:0] depth);
        case (depth)
            5'd0: luma_offset = 32'sd1;
            5'd1: luma_offset = 32'sd1;
            5'd2: luma_offset = 32'sd2;
            5'd3: luma_offset = 32'sd2;
            5'd4: luma_offset = 32'sd4;
            5'd5: luma_offset = 32'sd5;
            5'd6: luma_offset = 32'sd8;
            5'd7: luma_offset = 32'sd11;
            5'd8: luma_offset = 32'sd16;
            5'd9: luma_offset = 32'sd22;
            5'd10: luma_offset = 32'sd32;
            5'd11: luma_offset = 32'sd45;
            5'd12: luma_offset = 32'sd64;
            5'd13: luma_offset = 32'sd90;
            5'd14: luma_offset = 32'sd128;
            5'd15: luma_offset = 32'sd181;
            5'd16: luma_offset = 32'sd256;
            5'd17: luma_offset = 32'sd362;
            5'd18: luma_offset = 32'sd512;
            5'd19: luma_offset = 32'sd724;
            5'd20: luma_offset = 32'sd1024;
            5'd21: luma_offset = 32'sd1448;
            5'd22: luma_offset = 32'sd2048;
            5'd23: luma_offset = 32'sd2896;
            5'd24: luma_offset = 32'sd4096;
            5'd25: luma_offset = 32'sd5792;
            5'd26: luma_offset = 32'sd8192;
            5'd27: luma_offset = 32'sd11585;
            5'd28: luma_offset = 32'sd16384;
            5'd29: luma_offset = 32'sd23170;
            5'd30: luma_offset = 32'sd32768;
            5'd31: luma_offset = 32'sd46340;
            default: luma_offset = 0;
        endcase
    endfunction

    function automatic logic [7:0] output_code(
        input logic signed [31:0] value,
        input logic [4:0] depth,
        input logic luma
    );
        logic signed [31:0] result, offset, limit;
        integer shift;
        begin
            if (depth < 8) begin
                case (depth)
                    0: offset = 256;
                    1: offset = 181;
                    2: offset = 128;
                    3: offset = 91;
                    4: offset = 64;
                    5: offset = 45;
                    6: offset = 32;
                    7: offset = 23;
                    default: offset = 0;
                endcase
                if (!luma) offset = 128;
                shift = 8 - depth;
                limit = ((32'sd1 <<< depth) - 1) <<< shift;
                result = (value <<< shift) + offset;
                if (result < 0) result = 0;
                if (result > limit) result = limit;
            end else begin
                offset = luma ? luma_offset(depth) : (32'sd1 <<< (depth - 1));
                limit = $signed((32'd1 << depth) - 32'd1);
                result = value + offset;
                if (result < 0) result = 0;
                if (result > limit) result = limit;
                shift = depth - 8;
                if (shift != 0) result = (result + (32'sd1 <<< (shift-1))) >>> shift;
            end
            output_code = result > 255 ? 8'd255 : result[7:0];
        end
    endfunction

    assign in_ready = !busy || out_ready;
    assign out_valid = busy;

    always_comb begin
        mask16 = in_bit_depth >= 16 ? 16'hffff : (16'hffff >> (16-in_bit_depth));
        r = $signed({16'd0, (in_r & mask16)});
        g = $signed({16'd0, (in_g & mask16)});
        b = $signed({16'd0, (in_b & mask16)});
        if (in_conv_standard == 1) begin
            y = 47*r + 157*g + 16*b;
            u = -26*r - 86*g + 112*b;
            v = 112*r - 102*g - 10*b;
        end else begin
            y = 77*r + 150*g + 29*b;
            u = -44*r - 87*g + 138*b;
            v = 131*r - 110*g - 21*b;
        end
    end

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            busy <= 0;
            out_y <= 0;
            out_u <= 0;
            out_v <= 0;
        end else if (in_ready) begin
            busy <= in_valid;
            if (in_valid) begin
                out_y <= output_code(round_q8(y), in_bit_depth, 1'b1);
                out_u <= output_code(round_q8(u), in_bit_depth, 1'b0);
                out_v <= output_code(round_q8(v), in_bit_depth, 1'b0);
            end
        end
    end
endmodule
