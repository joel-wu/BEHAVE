module SwinWindowAttention2D (
    input logic clk, rst,
    input logic in_valid,
    output logic in_ready,
    input logic [511:0] in_x,
    input logic [1023:0] in_q_weight,
    input logic [127:0] in_q_bias,
    input logic [1023:0] in_k_weight,
    input logic [127:0] in_k_bias,
    input logic [1023:0] in_v_weight,
    input logic [127:0] in_v_bias,
    input logic [1023:0] in_proj_weight,
    input logic [127:0] in_proj_bias,
    input logic [287:0] in_rel_pos_bias_table,
    input logic [15:0] in_mask,
    output logic out_valid,
    input logic out_ready,
    output logic [511:0] out_y
);
    // One window in flight. Projections, attention rows and output rows use
    // separate cycles; this does not impose latency on submitted designs.
    logic [1:0] phase;
    integer row, head;
    logic [511:0] saved_x;
    logic [1023:0] saved_q_weight;
    logic [127:0] saved_q_bias;
    logic [1023:0] saved_k_weight;
    logic [127:0] saved_k_bias;
    logic [1023:0] saved_v_weight;
    logic [127:0] saved_v_bias;
    logic [1023:0] saved_proj_weight;
    logic [127:0] saved_proj_bias;
    logic [287:0] saved_rel_pos_bias_table;
    logic [15:0] saved_mask;

    logic signed [15:0] queries[0:31], keys[0:31], values[0:31], attended[0:31];
    integer col, dim, channel, offset, index;
    reg signed [63:0] aq, ak, av, dot, maximum, total, acc;
    reg signed [63:0] scores[0:3], weights[0:3];

    function automatic signed [63:0] lane(input logic [1023:0] word, input integer i);
        lane = {{48{word[16*i+15]}},word[16*i+:16]};
    endfunction
    function automatic signed [15:0] sat(input signed [63:0] value);
        if (value > 32767) sat = 16'sh7fff;
        else if (value < -32768) sat = 16'sh8000;
        else sat = value[15:0];
    endfunction
    function automatic signed [63:0] floor_div(input signed [63:0] a, b);
        floor_div = a / b;
        if (a < 0 && a % b != 0) floor_div = floor_div - 1;
    endfunction
    function automatic signed [63:0] exp_weight(input integer i);
        case(i)
            0: exp_weight = 64'd65536;
            1: exp_weight = 64'd61565;
            2: exp_weight = 64'd57835;
            3: exp_weight = 64'd54331;
            4: exp_weight = 64'd51039;
            5: exp_weight = 64'd47947;
            6: exp_weight = 64'd45042;
            7: exp_weight = 64'd42313;
            8: exp_weight = 64'd39750;
            9: exp_weight = 64'd37341;
            10: exp_weight = 64'd35079;
            11: exp_weight = 64'd32954;
            12: exp_weight = 64'd30957;
            13: exp_weight = 64'd29081;
            14: exp_weight = 64'd27319;
            15: exp_weight = 64'd25664;
            16: exp_weight = 64'd24109;
            17: exp_weight = 64'd22649;
            18: exp_weight = 64'd21276;
            19: exp_weight = 64'd19987;
            20: exp_weight = 64'd18776;
            21: exp_weight = 64'd17639;
            22: exp_weight = 64'd16570;
            23: exp_weight = 64'd15566;
            24: exp_weight = 64'd14623;
            25: exp_weight = 64'd13737;
            26: exp_weight = 64'd12905;
            27: exp_weight = 64'd12123;
            28: exp_weight = 64'd11388;
            29: exp_weight = 64'd10698;
            30: exp_weight = 64'd10050;
            31: exp_weight = 64'd9441;
            32: exp_weight = 64'd8869;
            33: exp_weight = 64'd8332;
            34: exp_weight = 64'd7827;
            35: exp_weight = 64'd7353;
            36: exp_weight = 64'd6907;
            37: exp_weight = 64'd6489;
            38: exp_weight = 64'd6096;
            39: exp_weight = 64'd5726;
            40: exp_weight = 64'd5380;
            41: exp_weight = 64'd5054;
            42: exp_weight = 64'd4747;
            43: exp_weight = 64'd4460;
            44: exp_weight = 64'd4190;
            45: exp_weight = 64'd3936;
            46: exp_weight = 64'd3697;
            47: exp_weight = 64'd3473;
            48: exp_weight = 64'd3263;
            49: exp_weight = 64'd3065;
            50: exp_weight = 64'd2879;
            51: exp_weight = 64'd2705;
            52: exp_weight = 64'd2541;
            53: exp_weight = 64'd2387;
            54: exp_weight = 64'd2243;
            55: exp_weight = 64'd2107;
            56: exp_weight = 64'd1979;
            57: exp_weight = 64'd1859;
            58: exp_weight = 64'd1746;
            59: exp_weight = 64'd1641;
            60: exp_weight = 64'd1541;
            61: exp_weight = 64'd1448;
            62: exp_weight = 64'd1360;
            63: exp_weight = 64'd1278;
            64: exp_weight = 64'd1200;
            65: exp_weight = 64'd1128;
            66: exp_weight = 64'd1059;
            67: exp_weight = 64'd995;
            68: exp_weight = 64'd935;
            69: exp_weight = 64'd878;
            70: exp_weight = 64'd825;
            71: exp_weight = 64'd775;
            72: exp_weight = 64'd728;
            73: exp_weight = 64'd684;
            74: exp_weight = 64'd642;
            75: exp_weight = 64'd604;
            76: exp_weight = 64'd567;
            77: exp_weight = 64'd533;
            78: exp_weight = 64'd500;
            79: exp_weight = 64'd470;
            80: exp_weight = 64'd442;
            81: exp_weight = 64'd415;
            82: exp_weight = 64'd390;
            83: exp_weight = 64'd366;
            84: exp_weight = 64'd344;
            85: exp_weight = 64'd323;
            86: exp_weight = 64'd303;
            87: exp_weight = 64'd285;
            88: exp_weight = 64'd268;
            89: exp_weight = 64'd252;
            90: exp_weight = 64'd236;
            91: exp_weight = 64'd222;
            92: exp_weight = 64'd209;
            93: exp_weight = 64'd196;
            94: exp_weight = 64'd184;
            95: exp_weight = 64'd173;
            96: exp_weight = 64'd162;
            97: exp_weight = 64'd153;
            98: exp_weight = 64'd143;
            99: exp_weight = 64'd135;
            100: exp_weight = 64'd127;
            101: exp_weight = 64'd119;
            102: exp_weight = 64'd112;
            103: exp_weight = 64'd105;
            104: exp_weight = 64'd99;
            105: exp_weight = 64'd93;
            106: exp_weight = 64'd87;
            107: exp_weight = 64'd82;
            108: exp_weight = 64'd77;
            109: exp_weight = 64'd72;
            110: exp_weight = 64'd68;
            111: exp_weight = 64'd64;
            112: exp_weight = 64'd60;
            113: exp_weight = 64'd56;
            114: exp_weight = 64'd53;
            115: exp_weight = 64'd50;
            116: exp_weight = 64'd47;
            117: exp_weight = 64'd44;
            118: exp_weight = 64'd41;
            119: exp_weight = 64'd39;
            120: exp_weight = 64'd36;
            121: exp_weight = 64'd34;
            122: exp_weight = 64'd32;
            123: exp_weight = 64'd30;
            124: exp_weight = 64'd28;
            125: exp_weight = 64'd27;
            126: exp_weight = 64'd25;
            127: exp_weight = 64'd23;
            128: exp_weight = 64'd22;
            default: exp_weight = 0;
        endcase
    endfunction

    assign in_ready = (phase == 0) && (!out_valid || out_ready);
    always_ff @(posedge clk) begin
        if (rst) begin
            phase <= 0;
            row <= 0;
            head <= 0;
            out_valid <= 0;
            out_y <= 0;
        end else begin
            if (out_valid && out_ready) out_valid <= 0;
            case (phase)
                0: if (in_valid && in_ready) begin
                    saved_x <= in_x;
                    saved_q_weight <= in_q_weight;
                    saved_q_bias <= in_q_bias;
                    saved_k_weight <= in_k_weight;
                    saved_k_bias <= in_k_bias;
                    saved_v_weight <= in_v_weight;
                    saved_v_bias <= in_v_bias;
                    saved_proj_weight <= in_proj_weight;
                    saved_proj_bias <= in_proj_bias;
                    saved_rel_pos_bias_table <= in_rel_pos_bias_table;
                    saved_mask <= in_mask;
                    row <= 0;
                    phase <= 1;
                end
                1: begin
                    for (channel=0; channel<8; channel=channel+1) begin
                        aq = lane(saved_q_bias,channel) * 256;
                        ak = lane(saved_k_bias,channel) * 256;
                        av = lane(saved_v_bias,channel) * 256;
                        for (dim=0; dim<8; dim=dim+1) begin
                            aq = aq + lane(saved_x,row*8+dim)*lane(saved_q_weight,channel*8+dim);
                            ak = ak + lane(saved_x,row*8+dim)*lane(saved_k_weight,channel*8+dim);
                            av = av + lane(saved_x,row*8+dim)*lane(saved_v_weight,channel*8+dim);
                        end
                        queries[row*8+channel] <= sat(aq >>> 8);
                        keys[row*8+channel] <= sat(ak >>> 8);
                        values[row*8+channel] <= sat(av >>> 8);
                    end
                    if (row==3) begin row<=0; head<=0; phase<=2; end
                    else row<=row+1;
                end
                2: begin
                    maximum = -1000000000;
                    for (col=0; col<4; col=col+1) begin
                        dot = 0;
                        for (dim=0; dim<4; dim=dim+1)
                            dot = dot + $signed(queries[row*8+head*4+dim]) * $signed(keys[col*8+head*4+dim]);
                        offset = (row/2-col/2+1)*3 + row%2-col%2+1;
                        scores[col] = (dot >>> 9) + lane(saved_rel_pos_bias_table,offset*2+head);
                        if (!saved_mask[row*4+col] && scores[col]>maximum) maximum=scores[col];
                    end
                    total = 0;
                    for (col=0; col<4; col=col+1) begin
                        weights[col] = 0;
                        if (!saved_mask[row*4+col]) begin
                            index = (maximum-scores[col]+8)/16;
                            weights[col] = exp_weight(index);
                        end
                        total = total+weights[col];
                    end
                    for (dim=0; dim<4; dim=dim+1) begin
                        acc = 0;
                        for (col=0; col<4; col=col+1)
                            acc = acc + weights[col]*$signed(values[col*8+head*4+dim]);
                        attended[row*8+head*4+dim] <= total==0 ? 0 : floor_div(acc,total);
                    end
                    if (head==0) head<=1;
                    else if (row==3) begin row<=0; phase<=3; end
                    else begin head<=0; row<=row+1; end
                end
                3: begin
                    for (channel=0; channel<8; channel=channel+1) begin
                        acc = lane(saved_proj_bias,channel)*256;
                        for (dim=0; dim<8; dim=dim+1)
                            acc = acc + $signed(attended[row*8+dim])*lane(saved_proj_weight,channel*8+dim);
                        out_y[16*(row*8+channel)+:16] <= sat(acc >>> 8);
                    end
                    if (row==3) begin out_valid<=1; phase<=0; end
                    else row<=row+1;
                end
            endcase
        end
    end
endmodule
