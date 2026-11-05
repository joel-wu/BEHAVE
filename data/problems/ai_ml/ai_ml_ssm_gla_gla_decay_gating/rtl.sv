module ai_ml_ssm_gla_gla_decay_gating (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic signed [15:0] in_gk_logit,
    input  logic signed [15:0] in_clamp_min,
    input  logic        in_clamp_en,

    output logic        out_valid,
    input  logic        out_ready,
    output logic signed [15:0] out_gk
);

logic busy;
logic signed [15:0] out_gk_next;

wire input_accepted;
wire output_consumed;

assign out_valid = busy;
assign in_ready = (~busy) | output_consumed;

assign input_accepted = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

function automatic logic [15:0] correction_rom(input logic [7:0] index);
    case (index)
        8'd0: correction_rom = 16'd45426;
        8'd1: correction_rom = 16'd43410;
        8'd2: correction_rom = 16'd41458;
        8'd3: correction_rom = 16'd39570;
        8'd4: correction_rom = 16'd37745;
        8'd5: correction_rom = 16'd35983;
        8'd6: correction_rom = 16'd34283;
        8'd7: correction_rom = 16'd32646;
        8'd8: correction_rom = 16'd31069;
        8'd9: correction_rom = 16'd29553;
        8'd10: correction_rom = 16'd28095;
        8'd11: correction_rom = 16'd26696;
        8'd12: correction_rom = 16'd25354;
        8'd13: correction_rom = 16'd24068;
        8'd14: correction_rom = 16'd22836;
        8'd15: correction_rom = 16'd21657;
        8'd16: correction_rom = 16'd20530;
        8'd17: correction_rom = 16'd19453;
        8'd18: correction_rom = 16'd18425;
        8'd19: correction_rom = 16'd17445;
        8'd20: correction_rom = 16'd16510;
        8'd21: correction_rom = 16'd15620;
        8'd22: correction_rom = 16'd14773;
        8'd23: correction_rom = 16'd13966;
        8'd24: correction_rom = 16'd13200;
        8'd25: correction_rom = 16'd12471;
        8'd26: correction_rom = 16'd11780;
        8'd27: correction_rom = 16'd11123;
        8'd28: correction_rom = 16'd10500;
        8'd29: correction_rom = 16'd9910;
        8'd30: correction_rom = 16'd9350;
        8'd31: correction_rom = 16'd8820;
        8'd32: correction_rom = 16'd8318;
        8'd33: correction_rom = 16'd7843;
        8'd34: correction_rom = 16'd7394;
        8'd35: correction_rom = 16'd6969;
        8'd36: correction_rom = 16'd6567;
        8'd37: correction_rom = 16'd6187;
        8'd38: correction_rom = 16'd5829;
        8'd39: correction_rom = 16'd5490;
        8'd40: correction_rom = 16'd5170;
        8'd41: correction_rom = 16'd4868;
        8'd42: correction_rom = 16'd4583;
        8'd43: correction_rom = 16'd4315;
        8'd44: correction_rom = 16'd4061;
        8'd45: correction_rom = 16'd3822;
        8'd46: correction_rom = 16'd3597;
        8'd47: correction_rom = 16'd3384;
        8'd48: correction_rom = 16'd3184;
        8'd49: correction_rom = 16'd2996;
        8'd50: correction_rom = 16'd2818;
        8'd51: correction_rom = 16'd2651;
        8'd52: correction_rom = 16'd2493;
        8'd53: correction_rom = 16'd2345;
        8'd54: correction_rom = 16'd2205;
        8'd55: correction_rom = 16'd2074;
        8'd56: correction_rom = 16'd1950;
        8'd57: correction_rom = 16'd1833;
        8'd58: correction_rom = 16'd1724;
        8'd59: correction_rom = 16'd1620;
        8'd60: correction_rom = 16'd1523;
        8'd61: correction_rom = 16'd1432;
        8'd62: correction_rom = 16'd1346;
        8'd63: correction_rom = 16'd1265;
        8'd64: correction_rom = 16'd1189;
        8'd65: correction_rom = 16'd1118;
        8'd66: correction_rom = 16'd1051;
        8'd67: correction_rom = 16'd988;
        8'd68: correction_rom = 16'd928;
        8'd69: correction_rom = 16'd872;
        8'd70: correction_rom = 16'd820;
        8'd71: correction_rom = 16'd770;
        8'd72: correction_rom = 16'd724;
        8'd73: correction_rom = 16'd680;
        8'd74: correction_rom = 16'd639;
        8'd75: correction_rom = 16'd601;
        8'd76: correction_rom = 16'd565;
        8'd77: correction_rom = 16'd530;
        8'd78: correction_rom = 16'd498;
        8'd79: correction_rom = 16'd468;
        8'd80: correction_rom = 16'd440;
        8'd81: correction_rom = 16'd414;
        8'd82: correction_rom = 16'd389;
        8'd83: correction_rom = 16'd365;
        8'd84: correction_rom = 16'd343;
        8'd85: correction_rom = 16'd322;
        8'd86: correction_rom = 16'd303;
        8'd87: correction_rom = 16'd284;
        8'd88: correction_rom = 16'd267;
        8'd89: correction_rom = 16'd251;
        8'd90: correction_rom = 16'd236;
        8'd91: correction_rom = 16'd222;
        8'd92: correction_rom = 16'd208;
        8'd93: correction_rom = 16'd196;
        8'd94: correction_rom = 16'd184;
        8'd95: correction_rom = 16'd173;
        8'd96: correction_rom = 16'd162;
        8'd97: correction_rom = 16'd152;
        8'd98: correction_rom = 16'd143;
        8'd99: correction_rom = 16'd135;
        8'd100: correction_rom = 16'd126;
        8'd101: correction_rom = 16'd119;
        8'd102: correction_rom = 16'd112;
        8'd103: correction_rom = 16'd105;
        8'd104: correction_rom = 16'd98;
        8'd105: correction_rom = 16'd92;
        8'd106: correction_rom = 16'd87;
        8'd107: correction_rom = 16'd82;
        8'd108: correction_rom = 16'd77;
        8'd109: correction_rom = 16'd72;
        8'd110: correction_rom = 16'd68;
        8'd111: correction_rom = 16'd64;
        8'd112: correction_rom = 16'd60;
        8'd113: correction_rom = 16'd56;
        8'd114: correction_rom = 16'd53;
        8'd115: correction_rom = 16'd50;
        8'd116: correction_rom = 16'd47;
        8'd117: correction_rom = 16'd44;
        8'd118: correction_rom = 16'd41;
        8'd119: correction_rom = 16'd39;
        8'd120: correction_rom = 16'd36;
        8'd121: correction_rom = 16'd34;
        8'd122: correction_rom = 16'd32;
        8'd123: correction_rom = 16'd30;
        8'd124: correction_rom = 16'd28;
        8'd125: correction_rom = 16'd27;
        8'd126: correction_rom = 16'd25;
        8'd127: correction_rom = 16'd23;
        8'd128: correction_rom = 16'd22;
        default: correction_rom = 16'd0;
    endcase
endfunction

always_comb begin
    logic signed [31:0] x, result, clamp;
    logic [31:0] a, j, r, correction, magnitude, q, remainder;
    x = {{16{in_gk_logit[15]}}, in_gk_logit};
    clamp = {{16{in_clamp_min[15]}}, in_clamp_min};
    a = x < 0 ? -x : x;
    j = a >> 4;
    r = a & 15;
    correction = 0;
    if (a < 2048)
        correction = (16-r) * correction_rom(j[7:0]) + r * correction_rom(8'(j+1));
    magnitude = (x < 0 ? -x : 0) * 4096 + correction;
    q = magnitude >> 16;
    remainder = magnitude & 65535;
    if (remainder > 32768 || (remainder == 32768 && q[0])) q = q + 1;
    result = -$signed(q);
    if (in_clamp_en && result < clamp) result = clamp;
    out_gk_next = result[15:0];
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy   <= 1'b0;
        out_gk <= 16'sd0;
    end else if (input_accepted) begin
        busy   <= 1'b1;
        out_gk <= out_gk_next;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule
