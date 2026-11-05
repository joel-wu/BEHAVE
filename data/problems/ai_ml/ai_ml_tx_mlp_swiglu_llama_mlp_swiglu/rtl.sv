module ai_ml_tx_mlp_swiglu_llama_mlp_swiglu (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic [63:0] in_x,
    input logic [511:0] in_gate_weight,
    input logic [511:0] in_up_weight,
    input logic [511:0] in_down_weight,
    input logic [127:0] in_gate_bias,
    input logic [127:0] in_up_bias,
    input logic [63:0] in_down_bias,
    output logic out_valid,
    input logic out_ready,
    output logic [63:0] out_y
);
    logic busy, down_phase;
    logic [2:0] row;
    logic [63:0] x_values, down_bias;
    logic [511:0] gate_weight, up_weight, down_weight;
    logic [127:0] gate_bias, up_bias, hidden;
    integer i;
    logic signed [15:0] x_lane, gate_lane, up_lane, hidden_lane, down_lane;
    logic signed [63:0] gate_sum, up_sum, down_sum, gate_value, up_value, activation, product;

    function automatic signed [63:0] rne(input signed [63:0] v, input integer shift);
        reg [63:0] magnitude, quotient, remainder, half;
        begin
            magnitude = v < 0 ? -v : v;
            quotient = magnitude >> shift;
            remainder = magnitude & ((64'd1 << shift)-1);
            half = 64'd1 << (shift-1);
            if (remainder > half || (remainder == half && quotient[0])) quotient = quotient + 1;
            rne = v < 0 ? -$signed(quotient) : $signed(quotient);
        end
    endfunction

    function automatic signed [63:0] sat16(input signed [63:0] v);
        begin
            if (v < -32768) sat16 = -32768;
            else if (v > 32767) sat16 = 32767;
            else sat16 = v;
        end
    endfunction

    function automatic [19:0] grid(input integer index);
        begin
            case (index)
                0: grid = 20'd0;
                1: grid = 20'd2112;
                2: grid = 20'd4352;
                3: grid = 20'd6718;
                4: grid = 20'd9211;
                5: grid = 20'd11827;
                6: grid = 20'd14565;
                7: grid = 20'd17423;
                8: grid = 20'd20397;
                9: grid = 20'd23484;
                10: grid = 20'd26679;
                11: grid = 20'd29981;
                12: grid = 20'd33383;
                13: grid = 20'd36882;
                14: grid = 20'd40473;
                15: grid = 20'd44150;
                16: grid = 20'd47911;
                17: grid = 20'd51748;
                18: grid = 20'd55658;
                19: grid = 20'd59636;
                20: grid = 20'd63676;
                21: grid = 20'd67775;
                22: grid = 20'd71926;
                23: grid = 20'd76126;
                24: grid = 20'd80371;
                25: grid = 20'd84655;
                26: grid = 20'd88976;
                27: grid = 20'd93328;
                28: grid = 20'd97709;
                29: grid = 20'd102114;
                30: grid = 20'd106541;
                31: grid = 20'd110987;
                32: grid = 20'd115448;
                33: grid = 20'd119922;
                34: grid = 20'd124406;
                35: grid = 20'd128898;
                36: grid = 20'd133396;
                37: grid = 20'd137898;
                38: grid = 20'd142403;
                39: grid = 20'd146907;
                40: grid = 20'd151411;
                41: grid = 20'd155913;
                42: grid = 20'd160412;
                43: grid = 20'd164906;
                44: grid = 20'd169395;
                45: grid = 20'd173878;
                46: grid = 20'd178354;
                47: grid = 20'd182823;
                48: grid = 20'd187284;
                49: grid = 20'd191736;
                50: grid = 20'd196180;
                51: grid = 20'd200616;
                52: grid = 20'd205042;
                53: grid = 20'd209458;
                54: grid = 20'd213866;
                55: grid = 20'd218264;
                56: grid = 20'd222652;
                57: grid = 20'd227032;
                58: grid = 20'd231401;
                59: grid = 20'd235762;
                60: grid = 20'd240113;
                61: grid = 20'd244455;
                62: grid = 20'd248789;
                63: grid = 20'd253113;
                64: grid = 20'd257429;
                65: grid = 20'd261737;
                66: grid = 20'd266036;
                67: grid = 20'd270327;
                68: grid = 20'd274611;
                69: grid = 20'd278887;
                70: grid = 20'd283156;
                71: grid = 20'd287417;
                72: grid = 20'd291672;
                73: grid = 20'd295920;
                74: grid = 20'd300161;
                75: grid = 20'd304397;
                76: grid = 20'd308626;
                77: grid = 20'd312849;
                78: grid = 20'd317067;
                79: grid = 20'd321280;
                80: grid = 20'd325487;
                81: grid = 20'd329689;
                82: grid = 20'd333887;
                83: grid = 20'd338080;
                84: grid = 20'd342268;
                85: grid = 20'd346452;
                86: grid = 20'd350632;
                87: grid = 20'd354808;
                88: grid = 20'd358981;
                89: grid = 20'd363150;
                90: grid = 20'd367315;
                91: grid = 20'd371477;
                92: grid = 20'd375636;
                93: grid = 20'd379792;
                94: grid = 20'd383946;
                95: grid = 20'd388096;
                96: grid = 20'd392244;
                97: grid = 20'd396389;
                98: grid = 20'd400532;
                99: grid = 20'd404672;
                100: grid = 20'd408811;
                101: grid = 20'd412947;
                102: grid = 20'd417081;
                103: grid = 20'd421214;
                104: grid = 20'd425345;
                105: grid = 20'd429473;
                106: grid = 20'd433601;
                107: grid = 20'd437726;
                108: grid = 20'd441851;
                109: grid = 20'd445973;
                110: grid = 20'd450095;
                111: grid = 20'd454215;
                112: grid = 20'd458334;
                113: grid = 20'd462452;
                114: grid = 20'd466569;
                115: grid = 20'd470684;
                116: grid = 20'd474799;
                117: grid = 20'd478912;
                118: grid = 20'd483025;
                119: grid = 20'd487137;
                120: grid = 20'd491248;
                121: grid = 20'd495359;
                122: grid = 20'd499468;
                123: grid = 20'd503577;
                124: grid = 20'd507685;
                125: grid = 20'd511793;
                126: grid = 20'd515900;
                127: grid = 20'd520006;
                128: grid = 20'd524112;
                default: grid = 0;
            endcase
        end
    endfunction

    function automatic signed [63:0] silu(input signed [63:0] v);
        reg signed [63:0] magnitude, positive, residual, numerator, left_value, right_value;
        integer index;
        begin
            magnitude = v < 0 ? -v : v;
            positive = magnitude;
            if (magnitude < 2048) begin
                index = magnitude >> 4;
                residual = magnitude & 15;
                left_value = {44'd0, grid(index)};
                right_value = {44'd0, grid(index+1)};
                numerator = (16-residual)*left_value + residual*right_value;
                positive = rne(numerator,12);
            end
            silu = v < 0 ? positive-magnitude : positive;
        end
    endfunction

    assign in_ready = !rst && !busy && (!out_valid || out_ready);

    always_comb begin
        gate_sum = $signed(gate_bias[16*row +: 16]);
        gate_sum = gate_sum * 256;
        up_sum = $signed(up_bias[16*row +: 16]);
        up_sum = up_sum * 256;
        down_sum = 0;
        x_lane = 0; gate_lane = 0; up_lane = 0;
        hidden_lane = 0; down_lane = 0;
        for (i=0; i<4; i=i+1) begin
            x_lane = $signed(x_values[16*i +: 16]);
            gate_lane = $signed(gate_weight[16*(4*row+i) +: 16]);
            up_lane = $signed(up_weight[16*(4*row+i) +: 16]);
            gate_sum = gate_sum + x_lane * gate_lane;
            up_sum = up_sum + x_lane * up_lane;
        end
        if (down_phase) begin
            down_sum = $signed(down_bias[16*row +: 16]);
            down_sum = down_sum * 256;
            for (i=0; i<8; i=i+1) begin
                hidden_lane = $signed(hidden[16*i +: 16]);
                down_lane = $signed(down_weight[16*(8*row+i) +: 16]);
                down_sum = down_sum + hidden_lane * down_lane;
            end
        end
        gate_value = sat16(rne(gate_sum,8));
        up_value = sat16(rne(up_sum,8));
        activation = silu(gate_value);
        product = sat16(rne(activation*up_value,8));
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy <= 0; down_phase <= 0; row <= 0;
            x_values <= 0; gate_weight <= 0; up_weight <= 0; down_weight <= 0;
            gate_bias <= 0; up_bias <= 0; down_bias <= 0; hidden <= 0;
            out_valid <= 0; out_y <= 0;
        end else begin
            if (out_valid && out_ready) out_valid <= 0;
            if (in_valid && in_ready) begin
                x_values <= in_x;
                gate_weight <= in_gate_weight; up_weight <= in_up_weight; down_weight <= in_down_weight;
                gate_bias <= in_gate_bias; up_bias <= in_up_bias; down_bias <= in_down_bias;
                busy <= 1; down_phase <= 0; row <= 0;
            end else if (busy) begin
                if (!down_phase) begin
                    hidden[16*row +: 16] <= product[15:0];
                    if (row == 7) begin row <= 0; down_phase <= 1; end
                    else row <= row+1;
                end else begin
                    out_y[16*row +: 16] <= sat16(rne(down_sum,8));
                    if (row == 3) begin busy <= 0; out_valid <= 1; row <= 0; end
                    else row <= row+1;
                end
            end
        end
    end
endmodule
