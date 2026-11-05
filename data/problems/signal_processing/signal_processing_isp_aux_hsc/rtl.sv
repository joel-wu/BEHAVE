module signal_processing_isp_aux_hsc (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [7:0]  in_c0,
    input  logic [7:0]  in_c1,
    input  logic [8:0]  in_hue,
    input  logic [8:0]  in_saturation,
    input  logic [7:0]  in_clip,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [7:0]  out_c0,
    output logic [7:0]  out_c1
);

function automatic logic [8:0] quarter_sine(input logic [6:0] index);
    begin
        case (index)
            7'd0: quarter_sine = 9'd0;
            7'd1: quarter_sine = 9'd4;
            7'd2: quarter_sine = 9'd9;
            7'd3: quarter_sine = 9'd13;
            7'd4: quarter_sine = 9'd18;
            7'd5: quarter_sine = 9'd22;
            7'd6: quarter_sine = 9'd27;
            7'd7: quarter_sine = 9'd31;
            7'd8: quarter_sine = 9'd36;
            7'd9: quarter_sine = 9'd40;
            7'd10: quarter_sine = 9'd44;
            7'd11: quarter_sine = 9'd49;
            7'd12: quarter_sine = 9'd53;
            7'd13: quarter_sine = 9'd58;
            7'd14: quarter_sine = 9'd62;
            7'd15: quarter_sine = 9'd66;
            7'd16: quarter_sine = 9'd71;
            7'd17: quarter_sine = 9'd75;
            7'd18: quarter_sine = 9'd79;
            7'd19: quarter_sine = 9'd83;
            7'd20: quarter_sine = 9'd88;
            7'd21: quarter_sine = 9'd92;
            7'd22: quarter_sine = 9'd96;
            7'd23: quarter_sine = 9'd100;
            7'd24: quarter_sine = 9'd104;
            7'd25: quarter_sine = 9'd108;
            7'd26: quarter_sine = 9'd112;
            7'd27: quarter_sine = 9'd116;
            7'd28: quarter_sine = 9'd120;
            7'd29: quarter_sine = 9'd124;
            7'd30: quarter_sine = 9'd128;
            7'd31: quarter_sine = 9'd132;
            7'd32: quarter_sine = 9'd136;
            7'd33: quarter_sine = 9'd139;
            7'd34: quarter_sine = 9'd143;
            7'd35: quarter_sine = 9'd147;
            7'd36: quarter_sine = 9'd150;
            7'd37: quarter_sine = 9'd154;
            7'd38: quarter_sine = 9'd158;
            7'd39: quarter_sine = 9'd161;
            7'd40: quarter_sine = 9'd165;
            7'd41: quarter_sine = 9'd168;
            7'd42: quarter_sine = 9'd171;
            7'd43: quarter_sine = 9'd175;
            7'd44: quarter_sine = 9'd178;
            7'd45: quarter_sine = 9'd181;
            7'd46: quarter_sine = 9'd184;
            7'd47: quarter_sine = 9'd187;
            7'd48: quarter_sine = 9'd190;
            7'd49: quarter_sine = 9'd193;
            7'd50: quarter_sine = 9'd196;
            7'd51: quarter_sine = 9'd199;
            7'd52: quarter_sine = 9'd202;
            7'd53: quarter_sine = 9'd204;
            7'd54: quarter_sine = 9'd207;
            7'd55: quarter_sine = 9'd210;
            7'd56: quarter_sine = 9'd212;
            7'd57: quarter_sine = 9'd215;
            7'd58: quarter_sine = 9'd217;
            7'd59: quarter_sine = 9'd219;
            7'd60: quarter_sine = 9'd222;
            7'd61: quarter_sine = 9'd224;
            7'd62: quarter_sine = 9'd226;
            7'd63: quarter_sine = 9'd228;
            7'd64: quarter_sine = 9'd230;
            7'd65: quarter_sine = 9'd232;
            7'd66: quarter_sine = 9'd234;
            7'd67: quarter_sine = 9'd236;
            7'd68: quarter_sine = 9'd237;
            7'd69: quarter_sine = 9'd239;
            7'd70: quarter_sine = 9'd241;
            7'd71: quarter_sine = 9'd242;
            7'd72: quarter_sine = 9'd243;
            7'd73: quarter_sine = 9'd245;
            7'd74: quarter_sine = 9'd246;
            7'd75: quarter_sine = 9'd247;
            7'd76: quarter_sine = 9'd248;
            7'd77: quarter_sine = 9'd249;
            7'd78: quarter_sine = 9'd250;
            7'd79: quarter_sine = 9'd251;
            7'd80: quarter_sine = 9'd252;
            7'd81: quarter_sine = 9'd253;
            7'd82: quarter_sine = 9'd254;
            7'd83: quarter_sine = 9'd254;
            7'd84: quarter_sine = 9'd255;
            7'd85: quarter_sine = 9'd255;
            7'd86: quarter_sine = 9'd255;
            7'd87: quarter_sine = 9'd256;
            7'd88: quarter_sine = 9'd256;
            7'd89: quarter_sine = 9'd256;
            7'd90: quarter_sine = 9'd256;
            default: quarter_sine = 9'd0;
        endcase
    end
endfunction

function automatic logic signed [9:0] sin_q8(input logic [8:0] angle);
    logic [6:0] index;
    logic negative;
    begin
        negative = angle > 9'd180;
        if (angle <= 9'd90) index = angle;
        else if (angle <= 9'd180) index = 9'd180 - angle;
        else if (angle <= 9'd270) index = angle - 9'd180;
        else index = 9'd360 - angle;
        sin_q8 = negative ? -$signed({1'b0, quarter_sine(index)})
                          :  $signed({1'b0, quarter_sine(index)});
    end
endfunction

function automatic logic [7:0] scaled_channel(
    input logic signed [19:0] rotated,
    input logic [8:0] saturation,
    input logic [7:0] clip_value
);
    logic signed [29:0] gain, limit, product, biased;
    begin
        gain = {21'b0, saturation};
        limit = {22'b0, clip_value};
        product = rotated * gain;
        biased = (product >>> 16) + 30'sd128;
        if (biased < 0) scaled_channel = 8'd0;
        else if (biased > limit) scaled_channel = clip_value;
        else scaled_channel = biased[7:0];
    end
endfunction

logic busy;
logic [8:0] angle, cos_angle;
logic signed [9:0] sine, cosine;
logic signed [8:0] u, v;
logic signed [19:0] rotated_u, rotated_v;

assign in_ready = !busy || out_ready;
assign out_valid = busy;

always_comb begin
    angle = in_hue < 9'd360 ? in_hue : in_hue - 9'd360;
    cos_angle = angle + 9'd90;
    if (cos_angle >= 9'd360) cos_angle = cos_angle - 9'd360;
    sine = sin_q8(angle);
    cosine = sin_q8(cos_angle);
    u = $signed({1'b0, in_c0}) - 9'sd128;
    v = $signed({1'b0, in_c1}) - 9'sd128;
    rotated_u = u * cosine + v * sine;
    rotated_v = v * cosine - u * sine;
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy <= 1'b0;
        out_c0 <= 8'd0;
        out_c1 <= 8'd0;
    end else if (in_valid && in_ready) begin
        busy <= 1'b1;
        out_c0 <= scaled_channel(rotated_u, in_saturation, in_clip);
        out_c1 <= scaled_channel(rotated_v, in_saturation, in_clip);
    end else if (out_valid && out_ready) begin
        busy <= 1'b0;
    end
end

endmodule
