module GemmaMLPGeGLU (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic [31:0] in_x_0,
    input logic [31:0] in_x_1,
    input logic [31:0] in_gate_proj_weight_0_0,
    input logic [31:0] in_gate_proj_weight_0_1,
    input logic [31:0] in_gate_proj_weight_1_0,
    input logic [31:0] in_gate_proj_weight_1_1,
    input logic [31:0] in_up_proj_weight_0_0,
    input logic [31:0] in_up_proj_weight_0_1,
    input logic [31:0] in_up_proj_weight_1_0,
    input logic [31:0] in_up_proj_weight_1_1,
    input logic [31:0] in_down_proj_weight_0_0,
    input logic [31:0] in_down_proj_weight_0_1,
    input logic [31:0] in_down_proj_weight_1_0,
    input logic [31:0] in_down_proj_weight_1_1,
    output logic out_valid,
    input logic out_ready,
    output logic [31:0] out_y_0,
    output logic [31:0] out_y_1
);

function automatic logic [63:0] fp_shift_jam(input logic [63:0] x, input integer n);
    if (n <= 0) fp_shift_jam = x;
    else if (n >= 64) fp_shift_jam = {63'b0, |x};
    else fp_shift_jam = (x >> n) | {63'b0, |(x << (64-n))};
endfunction

function automatic logic [31:0] fp_round_pack(
    input logic sign_bit, input integer exponent, input logic [63:0] sig
);
    integer e;
    logic [63:0] shifted, rounded;
    begin
        e = exponent;
        shifted = sig;
        if (e < 1) begin
            shifted = fp_shift_jam(shifted, 1-e);
            e = 1;
        end
        rounded = (shifted >> 3) + {63'b0, (shifted[2] &&
                  (shifted[1] || shifted[0] || shifted[3]))};
        if (rounded[24]) begin
            rounded = rounded >> 1;
            e = e+1;
        end
        if (e >= 255) fp_round_pack = {sign_bit, 8'hff, 23'b0};
        else if (rounded < 64'h800000)
            fp_round_pack = {sign_bit, 8'b0, rounded[22:0]};
        else fp_round_pack = {sign_bit, e[7:0], rounded[22:0]};
    end
endfunction

function automatic logic [31:0] fp32_add_bits(input logic [31:0] a, b);
    integer ea, eb, e, i;
    logic sa, sb, sign_bit;
    logic [63:0] ma, mb, hi, lo, sum;
    begin
        sa = a[31]; sb = b[31];
        ea = a[30:23] == 0 ? 1 : int'(a[30:23]);
        eb = b[30:23] == 0 ? 1 : int'(b[30:23]);
        ma = {40'b0, (a[30:23] != 0), a[22:0]};
        mb = {40'b0, (b[30:23] != 0), b[22:0]};
        if ((a[30:0] > 31'h7f800000) || (b[30:0] > 31'h7f800000))
            fp32_add_bits = 32'h7fc00000;
        else if ((a[30:0] == 31'h7f800000) && (b[30:0] == 31'h7f800000) && (sa != sb))
            fp32_add_bits = 32'h7fc00000;
        else if (a[30:0] == 31'h7f800000) fp32_add_bits = a;
        else if (b[30:0] == 31'h7f800000) fp32_add_bits = b;
        else if ((a[30:0] == 0) && (b[30:0] == 0))
            fp32_add_bits = {sa & sb, 31'b0};
        else begin
            if ((ea > eb) || ((ea == eb) && (ma >= mb))) begin
                hi = ma << 3; lo = fp_shift_jam(mb << 3, ea-eb);
                sign_bit = sa; e = ea;
            end else begin
                hi = mb << 3; lo = fp_shift_jam(ma << 3, eb-ea);
                sign_bit = sb; e = eb;
            end
            sum = sa == sb ? hi+lo : hi-lo;
            if (sum == 0) fp32_add_bits = 32'b0;
            else begin
                if (sum[27]) begin
                    sum = fp_shift_jam(sum, 1); e = e+1;
                end
                for (i=0; i<26; i=i+1) begin
                    if (!sum[26] && e>1) begin sum=sum<<1; e=e-1; end
                end
                fp32_add_bits = fp_round_pack(sign_bit, e, sum);
            end
        end
    end
endfunction

function automatic logic [31:0] fp32_mul_bits(input logic [31:0] a, b);
    integer ea, eb, e, i;
    logic sign_bit;
    logic [63:0] ma, mb, product, sig;
    begin
        sign_bit = a[31] ^ b[31];
        ea = a[30:23] == 0 ? 1 : int'(a[30:23]);
        eb = b[30:23] == 0 ? 1 : int'(b[30:23]);
        ma = {40'b0, (a[30:23] != 0), a[22:0]};
        mb = {40'b0, (b[30:23] != 0), b[22:0]};
        if ((a[30:0] > 31'h7f800000) || (b[30:0] > 31'h7f800000) ||
            ((a[30:0] == 31'h7f800000) && (b[30:0] == 0)) ||
            ((b[30:0] == 31'h7f800000) && (a[30:0] == 0)))
            fp32_mul_bits = 32'h7fc00000;
        else if ((a[30:0] == 31'h7f800000) || (b[30:0] == 31'h7f800000))
            fp32_mul_bits = {sign_bit, 8'hff, 23'b0};
        else if ((a[30:0] == 0) || (b[30:0] == 0))
            fp32_mul_bits = {sign_bit, 31'b0};
        else begin
            for (i=0; i<23; i=i+1) begin
                if (!ma[23]) begin ma=ma<<1; ea=ea-1; end
                if (!mb[23]) begin mb=mb<<1; eb=eb-1; end
            end
            product = ma*mb;
            e = ea+eb-127;
            if (product[47]) begin sig=fp_shift_jam(product,21); e=e+1; end
            else sig=fp_shift_jam(product,20);
            fp32_mul_bits = fp_round_pack(sign_bit, e, sig);
        end
    end
endfunction

    function automatic [31:0] gelu_tail(input integer j);
        case(j)
            0: gelu_tail=32'd0;
            1: gelu_tail=32'd16358963;
            2: gelu_tail=32'd31882236;
            3: gelu_tail=32'd46572264;
            4: gelu_tail=32'd60433112;
            5: gelu_tail=32'd73470446;
            6: gelu_tail=32'd85691505;
            7: gelu_tail=32'd97105076;
            8: gelu_tail=32'd107721450;
            9: gelu_tail=32'd117552381;
            10: gelu_tail=32'd126611033;
            11: gelu_tail=32'd134911925;
            12: gelu_tail=32'd142470870;
            13: gelu_tail=32'd149304907;
            14: gelu_tail=32'd155432230;
            15: gelu_tail=32'd160872113;
            16: gelu_tail=32'd165644830;
            17: gelu_tail=32'd169771573;
            18: gelu_tail=32'd173274370;
            19: gelu_tail=32'd176175991;
            20: gelu_tail=32'd178499867;
            21: gelu_tail=32'd180269994;
            22: gelu_tail=32'd181510845;
            23: gelu_tail=32'd182247279;
            24: gelu_tail=32'd182504450;
            25: gelu_tail=32'd182307718;
            26: gelu_tail=32'd181682560;
            27: gelu_tail=32'd180654483;
            28: gelu_tail=32'd179248939;
            29: gelu_tail=32'd177491248;
            30: gelu_tail=32'd175406510;
            31: gelu_tail=32'd173019538;
            32: gelu_tail=32'd170354782;
            33: gelu_tail=32'd167436259;
            34: gelu_tail=32'd164287495;
            35: gelu_tail=32'd160931457;
            36: gelu_tail=32'd157390507;
            37: gelu_tail=32'd153686342;
            38: gelu_tail=32'd149839956;
            39: gelu_tail=32'd145871596;
            40: gelu_tail=32'd141800726;
            41: gelu_tail=32'd137645995;
            42: gelu_tail=32'd133425213;
            43: gelu_tail=32'd129155328;
            44: gelu_tail=32'd124852410;
            45: gelu_tail=32'd120531640;
            46: gelu_tail=32'd116207299;
            47: gelu_tail=32'd111892768;
            48: gelu_tail=32'd107600529;
            49: gelu_tail=32'd103342169;
            50: gelu_tail=32'd99128387;
            51: gelu_tail=32'd94969009;
            52: gelu_tail=32'd90873003;
            53: gelu_tail=32'd86848495;
            54: gelu_tail=32'd82902792;
            55: gelu_tail=32'd79042409;
            56: gelu_tail=32'd75273086;
            57: gelu_tail=32'd71599827;
            58: gelu_tail=32'd68026921;
            59: gelu_tail=32'd64557978;
            60: gelu_tail=32'd61195959;
            61: gelu_tail=32'd57943210;
            62: gelu_tail=32'd54801496;
            63: gelu_tail=32'd51772036;
            64: gelu_tail=32'd48855536;
            65: gelu_tail=32'd46052227;
            66: gelu_tail=32'd43361896;
            67: gelu_tail=32'd40783922;
            68: gelu_tail=32'd38317310;
            69: gelu_tail=32'd35960726;
            70: gelu_tail=32'd33712524;
            71: gelu_tail=32'd31570784;
            72: gelu_tail=32'd29533337;
            73: gelu_tail=32'd27597799;
            74: gelu_tail=32'd25761596;
            75: gelu_tail=32'd24021991;
            76: gelu_tail=32'd22376112;
            77: gelu_tail=32'd20820975;
            78: gelu_tail=32'd19353504;
            79: gelu_tail=32'd17970557;
            80: gelu_tail=32'd16668943;
            81: gelu_tail=32'd15445443;
            82: gelu_tail=32'd14296822;
            83: gelu_tail=32'd13219852;
            84: gelu_tail=32'd12211319;
            85: gelu_tail=32'd11268042;
            86: gelu_tail=32'd10386880;
            87: gelu_tail=32'd9564746;
            88: gelu_tail=32'd8798615;
            89: gelu_tail=32'd8085530;
            90: gelu_tail=32'd7422613;
            91: gelu_tail=32'd6807066;
            92: gelu_tail=32'd6236180;
            93: gelu_tail=32'd5707338;
            94: gelu_tail=32'd5218016;
            95: gelu_tail=32'd4765788;
            96: gelu_tail=32'd4348326;
            97: gelu_tail=32'd3963403;
            98: gelu_tail=32'd3608889;
            99: gelu_tail=32'd3282756;
            100: gelu_tail=32'd2983074;
            101: gelu_tail=32'd2708009;
            102: gelu_tail=32'd2455826;
            103: gelu_tail=32'd2224880;
            104: gelu_tail=32'd2013622;
            105: gelu_tail=32'd1820588;
            106: gelu_tail=32'd1644404;
            107: gelu_tail=32'd1483777;
            108: gelu_tail=32'd1337496;
            109: gelu_tail=32'd1204426;
            110: gelu_tail=32'd1083509;
            111: gelu_tail=32'd973755;
            112: gelu_tail=32'd874243;
            113: gelu_tail=32'd784115;
            114: gelu_tail=32'd702575;
            115: gelu_tail=32'd628886;
            116: gelu_tail=32'd562364;
            117: gelu_tail=32'd502377;
            118: gelu_tail=32'd448341;
            119: gelu_tail=32'd399719;
            120: gelu_tail=32'd356015;
            121: gelu_tail=32'd316774;
            122: gelu_tail=32'd281577;
            123: gelu_tail=32'd250043;
            124: gelu_tail=32'd221819;
            125: gelu_tail=32'd196586;
            126: gelu_tail=32'd174050;
            127: gelu_tail=32'd153945;
            128: gelu_tail=32'd136027;
            129: gelu_tail=32'd120075;
            130: gelu_tail=32'd105889;
            131: gelu_tail=32'd93287;
            132: gelu_tail=32'd82103;
            133: gelu_tail=32'd72188;
            134: gelu_tail=32'd63408;
            135: gelu_tail=32'd55640;
            136: gelu_tail=32'd48776;
            137: gelu_tail=32'd42716;
            138: gelu_tail=32'd37373;
            139: gelu_tail=32'd32665;
            140: gelu_tail=32'd28522;
            141: gelu_tail=32'd24880;
            142: gelu_tail=32'd21682;
            143: gelu_tail=32'd18876;
            144: gelu_tail=32'd16417;
            145: gelu_tail=32'd14264;
            146: gelu_tail=32'd12382;
            147: gelu_tail=32'd10737;
            148: gelu_tail=32'd9301;
            149: gelu_tail=32'd8050;
            150: gelu_tail=32'd6960;
            151: gelu_tail=32'd6012;
            152: gelu_tail=32'd5187;
            153: gelu_tail=32'd4472;
            154: gelu_tail=32'd3851;
            155: gelu_tail=32'd3313;
            156: gelu_tail=32'd2848;
            157: gelu_tail=32'd2445;
            158: gelu_tail=32'd2098;
            159: gelu_tail=32'd1798;
            160: gelu_tail=32'd1539;
            161: gelu_tail=32'd1316;
            162: gelu_tail=32'd1125;
            163: gelu_tail=32'd960;
            164: gelu_tail=32'd819;
            165: gelu_tail=32'd697;
            166: gelu_tail=32'd594;
            167: gelu_tail=32'd505;
            168: gelu_tail=32'd429;
            169: gelu_tail=32'd364;
            170: gelu_tail=32'd308;
            171: gelu_tail=32'd261;
            172: gelu_tail=32'd221;
            173: gelu_tail=32'd187;
            174: gelu_tail=32'd158;
            175: gelu_tail=32'd133;
            176: gelu_tail=32'd112;
            177: gelu_tail=32'd94;
            178: gelu_tail=32'd79;
            179: gelu_tail=32'd67;
            180: gelu_tail=32'd56;
            181: gelu_tail=32'd47;
            182: gelu_tail=32'd39;
            183: gelu_tail=32'd33;
            184: gelu_tail=32'd28;
            185: gelu_tail=32'd23;
            186: gelu_tail=32'd19;
            187: gelu_tail=32'd16;
            188: gelu_tail=32'd13;
            189: gelu_tail=32'd11;
            190: gelu_tail=32'd9;
            191: gelu_tail=32'd8;
            192: gelu_tail=32'd6;
            193: gelu_tail=32'd5;
            194: gelu_tail=32'd4;
            195: gelu_tail=32'd4;
            196: gelu_tail=32'd3;
            197: gelu_tail=32'd2;
            198: gelu_tail=32'd2;
            199: gelu_tail=32'd2;
            200: gelu_tail=32'd1;
            201: gelu_tail=32'd1;
            202: gelu_tail=32'd1;
            203: gelu_tail=32'd1;
            204: gelu_tail=32'd1;
            205: gelu_tail=32'd1;
            206: gelu_tail=32'd0;
            207: gelu_tail=32'd0;
            208: gelu_tail=32'd0;
            209: gelu_tail=32'd0;
            210: gelu_tail=32'd0;
            211: gelu_tail=32'd0;
            212: gelu_tail=32'd0;
            213: gelu_tail=32'd0;
            214: gelu_tail=32'd0;
            215: gelu_tail=32'd0;
            216: gelu_tail=32'd0;
            217: gelu_tail=32'd0;
            218: gelu_tail=32'd0;
            219: gelu_tail=32'd0;
            220: gelu_tail=32'd0;
            221: gelu_tail=32'd0;
            222: gelu_tail=32'd0;
            223: gelu_tail=32'd0;
            224: gelu_tail=32'd0;
            225: gelu_tail=32'd0;
            226: gelu_tail=32'd0;
            227: gelu_tail=32'd0;
            228: gelu_tail=32'd0;
            229: gelu_tail=32'd0;
            230: gelu_tail=32'd0;
            231: gelu_tail=32'd0;
            232: gelu_tail=32'd0;
            233: gelu_tail=32'd0;
            234: gelu_tail=32'd0;
            235: gelu_tail=32'd0;
            236: gelu_tail=32'd0;
            237: gelu_tail=32'd0;
            238: gelu_tail=32'd0;
            239: gelu_tail=32'd0;
            240: gelu_tail=32'd0;
            241: gelu_tail=32'd0;
            242: gelu_tail=32'd0;
            243: gelu_tail=32'd0;
            244: gelu_tail=32'd0;
            245: gelu_tail=32'd0;
            246: gelu_tail=32'd0;
            247: gelu_tail=32'd0;
            248: gelu_tail=32'd0;
            249: gelu_tail=32'd0;
            250: gelu_tail=32'd0;
            251: gelu_tail=32'd0;
            252: gelu_tail=32'd0;
            253: gelu_tail=32'd0;
            254: gelu_tail=32'd0;
            255: gelu_tail=32'd0;
            256: gelu_tail=32'd0;
            default: gelu_tail=0;
        endcase
    endfunction

    function automatic [31:0] q30_word(input logic [31:0] value);
        integer top, shift;
        logic [31:0] significand, remainder, halfway;
        begin
            top=0;shift=0;significand=0;remainder=0;halfway=0;
            for (integer i=0;i<31;i=i+1) if ((value>>i)!=0) top=i;
            if (value==0) q30_word=0;
            else begin
                if (top<=23) significand=value<<(23-top);
                else begin
                    shift=top-23;significand=value>>shift;
                    remainder=value & ((32'd1<<shift)-1);halfway=32'd1<<(shift-1);
                    if (remainder>halfway || (remainder==halfway && significand[0])) significand=significand+1;
                    if (significand==32'h1000000) begin significand=significand>>1;top=top+1;end
                end
                q30_word=(32'(top+97)<<23)|(significand & 32'h7fffff);
            end
        end
    endfunction
    function automatic [31:0] gelu_word(input logic [31:0] x);
        logic [31:0] a,q,h;
        logic [63:0] correction;
        integer exponent,j,r;
        begin
            a=x & 32'h7fffffff;exponent=int'(a[30:23]);q=0;h=0;correction=0;j=0;r=0;
            if (a>32'h7f800000) gelu_word=32'h7fc00000;
            else if (exponent==0) gelu_word={x[31],31'd0};
            else if (a>=32'h41000000) gelu_word=x[31] ? 32'h80000000 : x;
            else begin
                if (exponent>=111) q=({8'd0,1'b1,a[22:0]})>>(134-exponent);
                j=int'(q>>11);r=int'(q & 2047);
                correction=(64'(gelu_tail(j))*64'(2048-r)+64'(gelu_tail(j+1))*64'(r))/64'd2048;
                h=q30_word(32'(correction));
                gelu_word=x[31] ? (h|32'h80000000) : fp32_add_bits(x,h|32'h80000000);
            end
        end
    endfunction
    logic [31:0] next_out_y_0, next_out_y_1;
    logic [31:0] g0,g1,u0,u1,m0,m1;
    always_comb begin
        g0=fp32_add_bits(fp32_mul_bits(in_gate_proj_weight_0_0,in_x_0),fp32_mul_bits(in_gate_proj_weight_0_1,in_x_1));
        g1=fp32_add_bits(fp32_mul_bits(in_gate_proj_weight_1_0,in_x_0),fp32_mul_bits(in_gate_proj_weight_1_1,in_x_1));
        u0=fp32_add_bits(fp32_mul_bits(in_up_proj_weight_0_0,in_x_0),fp32_mul_bits(in_up_proj_weight_0_1,in_x_1));
        u1=fp32_add_bits(fp32_mul_bits(in_up_proj_weight_1_0,in_x_0),fp32_mul_bits(in_up_proj_weight_1_1,in_x_1));
        m0=fp32_mul_bits(gelu_word(g0),u0);
        m1=fp32_mul_bits(gelu_word(g1),u1);
        next_out_y_0=fp32_add_bits(fp32_mul_bits(in_down_proj_weight_0_0,m0),fp32_mul_bits(in_down_proj_weight_0_1,m1));
        next_out_y_1=fp32_add_bits(fp32_mul_bits(in_down_proj_weight_1_0,m0),fp32_mul_bits(in_down_proj_weight_1_1,m1));
    end

    assign in_ready = !out_valid || out_ready;
    always_ff @(posedge clk) begin
        if (rst) begin out_valid <= 0; out_y_0 <= 0; out_y_1 <= 0; end
        else if (in_ready) begin
            out_valid <= in_valid;
            if (in_valid) begin out_y_0 <= next_out_y_0; out_y_1 <= next_out_y_1; end
            else begin out_y_0 <= 0; out_y_1 <= 0; end
        end
    end
endmodule
