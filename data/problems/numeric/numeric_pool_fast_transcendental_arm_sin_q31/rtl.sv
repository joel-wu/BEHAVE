module fast_q31_sine (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_x,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_sin
);

function automatic logic signed [31:0] quarter(input logic [7:0] i);
    begin
        case (i)
            8'd0: quarter = 32'sd0;
            8'd1: quarter = 32'sd26352928;
            8'd2: quarter = 32'sd52701887;
            8'd3: quarter = 32'sd79042909;
            8'd4: quarter = 32'sd105372028;
            8'd5: quarter = 32'sd131685278;
            8'd6: quarter = 32'sd157978697;
            8'd7: quarter = 32'sd184248325;
            8'd8: quarter = 32'sd210490206;
            8'd9: quarter = 32'sd236700388;
            8'd10: quarter = 32'sd262874923;
            8'd11: quarter = 32'sd289009871;
            8'd12: quarter = 32'sd315101295;
            8'd13: quarter = 32'sd341145265;
            8'd14: quarter = 32'sd367137861;
            8'd15: quarter = 32'sd393075166;
            8'd16: quarter = 32'sd418953276;
            8'd17: quarter = 32'sd444768294;
            8'd18: quarter = 32'sd470516330;
            8'd19: quarter = 32'sd496193509;
            8'd20: quarter = 32'sd521795963;
            8'd21: quarter = 32'sd547319836;
            8'd22: quarter = 32'sd572761285;
            8'd23: quarter = 32'sd598116479;
            8'd24: quarter = 32'sd623381598;
            8'd25: quarter = 32'sd648552838;
            8'd26: quarter = 32'sd673626408;
            8'd27: quarter = 32'sd698598533;
            8'd28: quarter = 32'sd723465451;
            8'd29: quarter = 32'sd748223418;
            8'd30: quarter = 32'sd772868706;
            8'd31: quarter = 32'sd797397602;
            8'd32: quarter = 32'sd821806413;
            8'd33: quarter = 32'sd846091463;
            8'd34: quarter = 32'sd870249095;
            8'd35: quarter = 32'sd894275671;
            8'd36: quarter = 32'sd918167572;
            8'd37: quarter = 32'sd941921200;
            8'd38: quarter = 32'sd965532978;
            8'd39: quarter = 32'sd988999351;
            8'd40: quarter = 32'sd1012316784;
            8'd41: quarter = 32'sd1035481766;
            8'd42: quarter = 32'sd1058490808;
            8'd43: quarter = 32'sd1081340445;
            8'd44: quarter = 32'sd1104027237;
            8'd45: quarter = 32'sd1126547765;
            8'd46: quarter = 32'sd1148898640;
            8'd47: quarter = 32'sd1171076495;
            8'd48: quarter = 32'sd1193077991;
            8'd49: quarter = 32'sd1214899813;
            8'd50: quarter = 32'sd1236538675;
            8'd51: quarter = 32'sd1257991320;
            8'd52: quarter = 32'sd1279254516;
            8'd53: quarter = 32'sd1300325060;
            8'd54: quarter = 32'sd1321199781;
            8'd55: quarter = 32'sd1341875533;
            8'd56: quarter = 32'sd1362349204;
            8'd57: quarter = 32'sd1382617710;
            8'd58: quarter = 32'sd1402678000;
            8'd59: quarter = 32'sd1422527051;
            8'd60: quarter = 32'sd1442161874;
            8'd61: quarter = 32'sd1461579514;
            8'd62: quarter = 32'sd1480777044;
            8'd63: quarter = 32'sd1499751576;
            8'd64: quarter = 32'sd1518500250;
            8'd65: quarter = 32'sd1537020244;
            8'd66: quarter = 32'sd1555308768;
            8'd67: quarter = 32'sd1573363068;
            8'd68: quarter = 32'sd1591180426;
            8'd69: quarter = 32'sd1608758157;
            8'd70: quarter = 32'sd1626093616;
            8'd71: quarter = 32'sd1643184191;
            8'd72: quarter = 32'sd1660027308;
            8'd73: quarter = 32'sd1676620432;
            8'd74: quarter = 32'sd1692961062;
            8'd75: quarter = 32'sd1709046739;
            8'd76: quarter = 32'sd1724875040;
            8'd77: quarter = 32'sd1740443581;
            8'd78: quarter = 32'sd1755750017;
            8'd79: quarter = 32'sd1770792044;
            8'd80: quarter = 32'sd1785567396;
            8'd81: quarter = 32'sd1800073849;
            8'd82: quarter = 32'sd1814309216;
            8'd83: quarter = 32'sd1828271356;
            8'd84: quarter = 32'sd1841958164;
            8'd85: quarter = 32'sd1855367581;
            8'd86: quarter = 32'sd1868497586;
            8'd87: quarter = 32'sd1881346202;
            8'd88: quarter = 32'sd1893911494;
            8'd89: quarter = 32'sd1906191570;
            8'd90: quarter = 32'sd1918184581;
            8'd91: quarter = 32'sd1929888720;
            8'd92: quarter = 32'sd1941302225;
            8'd93: quarter = 32'sd1952423377;
            8'd94: quarter = 32'sd1963250501;
            8'd95: quarter = 32'sd1973781967;
            8'd96: quarter = 32'sd1984016189;
            8'd97: quarter = 32'sd1993951625;
            8'd98: quarter = 32'sd2003586779;
            8'd99: quarter = 32'sd2012920201;
            8'd100: quarter = 32'sd2021950484;
            8'd101: quarter = 32'sd2030676269;
            8'd102: quarter = 32'sd2039096241;
            8'd103: quarter = 32'sd2047209133;
            8'd104: quarter = 32'sd2055013723;
            8'd105: quarter = 32'sd2062508835;
            8'd106: quarter = 32'sd2069693342;
            8'd107: quarter = 32'sd2076566160;
            8'd108: quarter = 32'sd2083126254;
            8'd109: quarter = 32'sd2089372638;
            8'd110: quarter = 32'sd2095304370;
            8'd111: quarter = 32'sd2100920556;
            8'd112: quarter = 32'sd2106220352;
            8'd113: quarter = 32'sd2111202959;
            8'd114: quarter = 32'sd2115867626;
            8'd115: quarter = 32'sd2120213651;
            8'd116: quarter = 32'sd2124240380;
            8'd117: quarter = 32'sd2127947206;
            8'd118: quarter = 32'sd2131333572;
            8'd119: quarter = 32'sd2134398966;
            8'd120: quarter = 32'sd2137142927;
            8'd121: quarter = 32'sd2139565043;
            8'd122: quarter = 32'sd2141664948;
            8'd123: quarter = 32'sd2143442326;
            8'd124: quarter = 32'sd2144896910;
            8'd125: quarter = 32'sd2146028480;
            8'd126: quarter = 32'sd2146836866;
            8'd127: quarter = 32'sd2147321946;
            8'd128: quarter = 32'sd2147483647;
            default: quarter = 32'sd0;
        endcase
    end
endfunction

function automatic logic signed [31:0] sine_sample(input logic [9:0] index);
    logic [8:0] i;
    begin
        i = index[8:0];
        if (i <= 128) sine_sample = quarter(8'(i));
        else if (i <= 256) sine_sample = quarter(8'(256 - i));
        else if (i == 384) sine_sample = 32'sh80000000;
        else if (i < 384) sine_sample = -quarter(8'(i - 256));
        else sine_sample = -quarter(8'(512 - i));
    end
endfunction

function automatic logic [31:0] calculate(input logic [31:0] x);
    logic [30:0] phase;
    logic [8:0] index;
    logic [31:0] fraction;
    logic signed [32:0] wa, wb;
    logic signed [31:0] a, b;
    logic signed [64:0] pa, pb, total;
    begin
        phase = x[30:0] + 31'd0;
        index = phase[30:22];
        fraction = {1'b0, phase[21:0], 9'b0};
        a = sine_sample({1'b0, index});
        b = sine_sample({1'b0, index} + 10'd1);
        wa = 33'sh080000000 - $signed({1'b0, fraction});
        wb = $signed({1'b0, fraction});
        pa = wa * a;
        pb = wb * b;
        total = ((pa >>> 32) + (pb >>> 32)) <<< 1;
        calculate = total[31:0];
    end
endfunction

logic pending;
assign out_valid = pending;
assign in_ready = !pending || out_ready;
always_ff @(posedge clk) begin
    if (rst) begin
        pending <= 1'b0;
        out_sin <= 32'b0;
    end else if (in_valid && in_ready) begin
        pending <= 1'b1;
        out_sin <= calculate(in_x);
    end else if (out_valid && out_ready) begin
        pending <= 1'b0;
    end
end
endmodule
