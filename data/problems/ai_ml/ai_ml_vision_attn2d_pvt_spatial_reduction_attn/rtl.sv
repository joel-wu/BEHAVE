module pvt_spatial_reduction_attn (
    input logic clk, rst,
    input logic in_valid,
    output logic in_ready,
    input logic [1023:0] in_x,
    input logic [1023:0] in_sr_weight,
    input logic [63:0] in_sr_bias,
    input logic [63:0] in_norm_weight,
    input logic [63:0] in_norm_bias,
    input logic [255:0] in_q_weight,
    input logic [63:0] in_q_bias,
    input logic [511:0] in_kv_weight,
    input logic [127:0] in_kv_bias,
    input logic [255:0] in_proj_weight,
    input logic [63:0] in_proj_bias,
    output logic out_valid,
    input logic out_ready,
    output logic [1023:0] out_data
);
    typedef enum logic [2:0] {IDLE, REDUCE, NORMALIZE, PROJECT, ATTEND, MIX, OUTPUT} phase_t;
    phase_t phase;
    logic [3:0] row_index;
    logic [1023:0] x;
    logic [1023:0] sr_weight;
    logic [63:0] sr_bias;
    logic [63:0] norm_weight;
    logic [63:0] norm_bias;
    logic [255:0] q_weight;
    logic [63:0] q_bias;
    logic [511:0] kv_weight;
    logic [127:0] kv_bias;
    logic [255:0] proj_weight;
    logic [63:0] proj_bias;

    logic signed [15:0] reduced [0:15], normalized [0:15];
    logic signed [15:0] query [0:63], kv [0:31], context_value [0:63];
    logic [16:0] weights [0:63];
    logic [31:0] denominators [0:15];
    longint signed scores [0:3];
    longint signed total, mean_value, variance, delta, scale_value, unit_value, peak, index_value;
    integer p, co, ci, dy, dx, t, wi;

    function automatic longint signed lane(input logic [1023:0] word, input integer index);
        lane = $signed(word[index*16 +: 16]);
    endfunction
    function automatic logic signed [15:0] clip(input longint signed value);
        if (value < -32768) clip=-32768;
        else if (value > 32767) clip=32767;
        else clip=value[15:0];
    endfunction
    function automatic longint signed divide_floor(input longint signed num, input longint signed den);
        if (num < 0) divide_floor=-((-num+den-1)/den);
        else divide_floor=num/den;
    endfunction
    function automatic longint unsigned root(input longint unsigned value);
        longint unsigned r, remainder_value, trial;
        begin
            r=0; remainder_value=0;
            for (integer bit_index=31;bit_index>=0;bit_index=bit_index-1) begin
                remainder_value=(remainder_value<<2)|((value>>(2*bit_index))&3);
                trial=(r<<2)|1;
                r=r<<1;
                if (remainder_value>=trial) begin
                    remainder_value=remainder_value-trial;
                    r=r|1;
                end
            end
            root=r;
        end
    endfunction
    function automatic logic [16:0] exp_word(input longint signed index);
        case(index)
            0: exp_word=17'd65536;
            1: exp_word=17'd61565;
            2: exp_word=17'd57835;
            3: exp_word=17'd54331;
            4: exp_word=17'd51039;
            5: exp_word=17'd47947;
            6: exp_word=17'd45042;
            7: exp_word=17'd42313;
            8: exp_word=17'd39750;
            9: exp_word=17'd37341;
            10: exp_word=17'd35079;
            11: exp_word=17'd32954;
            12: exp_word=17'd30957;
            13: exp_word=17'd29081;
            14: exp_word=17'd27319;
            15: exp_word=17'd25664;
            16: exp_word=17'd24109;
            17: exp_word=17'd22649;
            18: exp_word=17'd21276;
            19: exp_word=17'd19987;
            20: exp_word=17'd18776;
            21: exp_word=17'd17639;
            22: exp_word=17'd16570;
            23: exp_word=17'd15566;
            24: exp_word=17'd14623;
            25: exp_word=17'd13737;
            26: exp_word=17'd12905;
            27: exp_word=17'd12123;
            28: exp_word=17'd11388;
            29: exp_word=17'd10698;
            30: exp_word=17'd10050;
            31: exp_word=17'd9441;
            32: exp_word=17'd8869;
            33: exp_word=17'd8332;
            34: exp_word=17'd7827;
            35: exp_word=17'd7353;
            36: exp_word=17'd6907;
            37: exp_word=17'd6489;
            38: exp_word=17'd6096;
            39: exp_word=17'd5726;
            40: exp_word=17'd5380;
            41: exp_word=17'd5054;
            42: exp_word=17'd4747;
            43: exp_word=17'd4460;
            44: exp_word=17'd4190;
            45: exp_word=17'd3936;
            46: exp_word=17'd3697;
            47: exp_word=17'd3473;
            48: exp_word=17'd3263;
            49: exp_word=17'd3065;
            50: exp_word=17'd2879;
            51: exp_word=17'd2705;
            52: exp_word=17'd2541;
            53: exp_word=17'd2387;
            54: exp_word=17'd2243;
            55: exp_word=17'd2107;
            56: exp_word=17'd1979;
            57: exp_word=17'd1859;
            58: exp_word=17'd1746;
            59: exp_word=17'd1641;
            60: exp_word=17'd1541;
            61: exp_word=17'd1448;
            62: exp_word=17'd1360;
            63: exp_word=17'd1278;
            64: exp_word=17'd1200;
            65: exp_word=17'd1128;
            66: exp_word=17'd1059;
            67: exp_word=17'd995;
            68: exp_word=17'd935;
            69: exp_word=17'd878;
            70: exp_word=17'd825;
            71: exp_word=17'd775;
            72: exp_word=17'd728;
            73: exp_word=17'd684;
            74: exp_word=17'd642;
            75: exp_word=17'd604;
            76: exp_word=17'd567;
            77: exp_word=17'd533;
            78: exp_word=17'd500;
            79: exp_word=17'd470;
            80: exp_word=17'd442;
            81: exp_word=17'd415;
            82: exp_word=17'd390;
            83: exp_word=17'd366;
            84: exp_word=17'd344;
            85: exp_word=17'd323;
            86: exp_word=17'd303;
            87: exp_word=17'd285;
            88: exp_word=17'd268;
            89: exp_word=17'd252;
            90: exp_word=17'd236;
            91: exp_word=17'd222;
            92: exp_word=17'd209;
            93: exp_word=17'd196;
            94: exp_word=17'd184;
            95: exp_word=17'd173;
            96: exp_word=17'd162;
            97: exp_word=17'd153;
            98: exp_word=17'd143;
            99: exp_word=17'd135;
            100: exp_word=17'd127;
            101: exp_word=17'd119;
            102: exp_word=17'd112;
            103: exp_word=17'd105;
            104: exp_word=17'd99;
            105: exp_word=17'd93;
            106: exp_word=17'd87;
            107: exp_word=17'd82;
            108: exp_word=17'd77;
            109: exp_word=17'd72;
            110: exp_word=17'd68;
            111: exp_word=17'd64;
            112: exp_word=17'd60;
            113: exp_word=17'd56;
            114: exp_word=17'd53;
            115: exp_word=17'd50;
            116: exp_word=17'd47;
            117: exp_word=17'd44;
            118: exp_word=17'd41;
            119: exp_word=17'd39;
            120: exp_word=17'd36;
            121: exp_word=17'd34;
            122: exp_word=17'd32;
            123: exp_word=17'd30;
            124: exp_word=17'd28;
            125: exp_word=17'd27;
            126: exp_word=17'd25;
            127: exp_word=17'd23;
            128: exp_word=17'd22;

            default: exp_word=0;
        endcase
    endfunction
    assign in_ready=!rst && phase==IDLE && (!out_valid || out_ready);
    always_ff @(posedge clk) begin
        if (rst) begin
            phase<=IDLE; row_index<=0; out_valid<=0; out_data<=0;
        end else begin
            if (out_valid && out_ready) out_valid<=0;
            case(phase)
                IDLE: if (in_valid && in_ready) begin
                    x<=in_x;
                    sr_weight<=in_sr_weight;
                    sr_bias<=in_sr_bias;
                    norm_weight<=in_norm_weight;
                    norm_bias<=in_norm_bias;
                    q_weight<=in_q_weight;
                    q_bias<=in_q_bias;
                    kv_weight<=in_kv_weight;
                    kv_bias<=in_kv_bias;
                    proj_weight<=in_proj_weight;
                    proj_bias<=in_proj_bias;

                    row_index<=0; phase<=REDUCE;
                end
                REDUCE: begin
                    p=row_index;
                    for (co=0;co<4;co=co+1) begin
                        total=0;
                        for (ci=0;ci<4;ci=ci+1) for (dy=0;dy<2;dy=dy+1) for (dx=0;dx<2;dx=dx+1) begin
                            t=(2*(p/2)+dy)*4+2*(p%2)+dx;
                            wi=((co*4+ci)*2+dy)*2+dx;
                            total=total+lane(x,t*4+ci)*lane(sr_weight,wi);
                        end
                        reduced[p*4+co]<=clip((total>>>8)+lane(sr_bias,co));
                    end
                    if (row_index==3) begin row_index<=0; phase<=NORMALIZE; end
                    else row_index<=row_index+1;
                end
                NORMALIZE: begin
                    p=row_index;
                    begin
                        total=0;
                        for (co=0;co<4;co=co+1) total=total+$signed(reduced[p*4+co]);
                        mean_value=total>>>2; variance=0;
                        for (co=0;co<4;co=co+1) begin
                            delta=$signed(reduced[p*4+co])-mean_value;
                            variance=variance+delta*delta;
                        end
                        scale_value=root((variance>>2)+1);
                        for (co=0;co<4;co=co+1) begin
                            delta=$signed(reduced[p*4+co])-mean_value;
                            unit_value=divide_floor(delta*256,scale_value);
                            normalized[p*4+co]<=clip(((unit_value*lane(norm_weight,co))>>>8)+lane(norm_bias,co));
                        end
                    end
                    if (row_index==3) begin row_index<=0; phase<=PROJECT; end
                    else row_index<=row_index+1;
                end
                PROJECT: begin
                    t=row_index;
                    for (co=0;co<4;co=co+1) begin
                        total=0;
                        for (ci=0;ci<4;ci=ci+1) total=total+lane(x,t*4+ci)*lane(q_weight,co*4+ci);
                        query[t*4+co]<=clip((total>>>8)+lane(q_bias,co));
                    end
                    p=row_index;
                    for (co=0;co<8;co=co+1) if (row_index<4) begin
                        total=0;
                        for (ci=0;ci<4;ci=ci+1) total=total+$signed(normalized[p*4+ci])*lane(kv_weight,co*4+ci);
                        kv[p*8+co]<=clip((total>>>8)+lane(kv_bias,co));
                    end
                    if (row_index==15) begin row_index<=0; phase<=ATTEND; end
                    else row_index<=row_index+1;
                end
                ATTEND: begin
                    t=row_index;
                    begin
                        peak=-64'sd9223372036854775807;
                        for (p=0;p<4;p=p+1) begin
                            total=0;
                            for (ci=0;ci<4;ci=ci+1) total=total+$signed(query[t*4+ci])*$signed(kv[p*8+ci]);
                            scores[p]=total>>>9;
                            if (scores[p]>peak) peak=scores[p];
                        end
                        total=0;
                        for (p=0;p<4;p=p+1) begin
                            index_value=(peak-scores[p]+8)>>>4;
                            weights[t*4+p]<=exp_word(index_value);
                            total=total+exp_word(index_value);
                        end
                        denominators[t]<=total;
                    end
                    if (row_index==15) begin row_index<=0; phase<=MIX; end
                    else row_index<=row_index+1;
                end
                MIX: begin
                    t=row_index;
                    for (co=0;co<4;co=co+1) begin
                        total=0;
                        for (p=0;p<4;p=p+1) total=total+$signed({1'b0,weights[t*4+p]})*$signed(kv[p*8+4+co]);
                        context_value[t*4+co]<=divide_floor(total,denominators[t]);
                    end
                    if (row_index==15) begin row_index<=0; phase<=OUTPUT; end
                    else row_index<=row_index+1;
                end
                OUTPUT: begin
                    t=row_index;
                    for (co=0;co<4;co=co+1) begin
                        total=0;
                        for (ci=0;ci<4;ci=ci+1) total=total+$signed(context_value[t*4+ci])*lane(proj_weight,co*4+ci);
                        out_data[(t*4+co)*16 +: 16]<=clip((total>>>8)+lane(proj_bias,co));
                    end
                    if (row_index==15) begin out_valid<=1; row_index<=0; phase<=IDLE; end
                    else row_index<=row_index+1;
                end
                default: phase<=IDLE;
            endcase
        end
    end
endmodule
