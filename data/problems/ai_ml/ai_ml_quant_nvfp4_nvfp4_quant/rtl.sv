module ai_ml_quant_nvfp4_nvfp4_quant (
    input logic clk,rst,in_valid,
    output logic in_ready,
    input logic [15:0] in_x0,
    input logic [15:0] in_x1,
    input logic [15:0] in_x2,
    input logic [15:0] in_x3,
    input logic [15:0] in_x4,
    input logic [15:0] in_x5,
    input logic [15:0] in_x6,
    input logic [15:0] in_x7,
    input logic [15:0] in_x8,
    input logic [15:0] in_x9,
    input logic [15:0] in_x10,
    input logic [15:0] in_x11,
    input logic [15:0] in_x12,
    input logic [15:0] in_x13,
    input logic [15:0] in_x14,
    input logic [15:0] in_x15,
    input logic [31:0] in_global_scale,
    input logic out_ready,
    output logic out_valid,
    output logic [7:0] out_scale,
    output logic [3:0] out_q0,
    output logic [3:0] out_q1,
    output logic [3:0] out_q2,
    output logic [3:0] out_q3,
    output logic [3:0] out_q4,
    output logic [3:0] out_q5,
    output logic [3:0] out_q6,
    output logic [3:0] out_q7,
    output logic [3:0] out_q8,
    output logic [3:0] out_q9,
    output logic [3:0] out_q10,
    output logic [3:0] out_q11,
    output logic [3:0] out_q12,
    output logic [3:0] out_q13,
    output logic [3:0] out_q14,
    output logic [3:0] out_q15,
    output logic [0:0] out_error
);

    function automatic [31:0] midpoint(input integer k);
        case(k)
            0:midpoint=32'h3b400000;
            1:midpoint=32'h3ba00000;
            2:midpoint=32'h3be00000;
            3:midpoint=32'h3c100000;
            4:midpoint=32'h3c300000;
            5:midpoint=32'h3c500000;
            6:midpoint=32'h3c700000;
            7:midpoint=32'h3c880000;
            8:midpoint=32'h3c980000;
            9:midpoint=32'h3ca80000;
            10:midpoint=32'h3cb80000;
            11:midpoint=32'h3cc80000;
            12:midpoint=32'h3cd80000;
            13:midpoint=32'h3ce80000;
            14:midpoint=32'h3cf80000;
            15:midpoint=32'h3d080000;
            16:midpoint=32'h3d180000;
            17:midpoint=32'h3d280000;
            18:midpoint=32'h3d380000;
            19:midpoint=32'h3d480000;
            20:midpoint=32'h3d580000;
            21:midpoint=32'h3d680000;
            22:midpoint=32'h3d780000;
            23:midpoint=32'h3d880000;
            24:midpoint=32'h3d980000;
            25:midpoint=32'h3da80000;
            26:midpoint=32'h3db80000;
            27:midpoint=32'h3dc80000;
            28:midpoint=32'h3dd80000;
            29:midpoint=32'h3de80000;
            30:midpoint=32'h3df80000;
            31:midpoint=32'h3e080000;
            32:midpoint=32'h3e180000;
            33:midpoint=32'h3e280000;
            34:midpoint=32'h3e380000;
            35:midpoint=32'h3e480000;
            36:midpoint=32'h3e580000;
            37:midpoint=32'h3e680000;
            38:midpoint=32'h3e780000;
            39:midpoint=32'h3e880000;
            40:midpoint=32'h3e980000;
            41:midpoint=32'h3ea80000;
            42:midpoint=32'h3eb80000;
            43:midpoint=32'h3ec80000;
            44:midpoint=32'h3ed80000;
            45:midpoint=32'h3ee80000;
            46:midpoint=32'h3ef80000;
            47:midpoint=32'h3f080000;
            48:midpoint=32'h3f180000;
            49:midpoint=32'h3f280000;
            50:midpoint=32'h3f380000;
            51:midpoint=32'h3f480000;
            52:midpoint=32'h3f580000;
            53:midpoint=32'h3f680000;
            54:midpoint=32'h3f780000;
            55:midpoint=32'h3f880000;
            56:midpoint=32'h3f980000;
            57:midpoint=32'h3fa80000;
            58:midpoint=32'h3fb80000;
            59:midpoint=32'h3fc80000;
            60:midpoint=32'h3fd80000;
            61:midpoint=32'h3fe80000;
            62:midpoint=32'h3ff80000;
            63:midpoint=32'h40080000;
            64:midpoint=32'h40180000;
            65:midpoint=32'h40280000;
            66:midpoint=32'h40380000;
            67:midpoint=32'h40480000;
            68:midpoint=32'h40580000;
            69:midpoint=32'h40680000;
            70:midpoint=32'h40780000;
            71:midpoint=32'h40880000;
            72:midpoint=32'h40980000;
            73:midpoint=32'h40a80000;
            74:midpoint=32'h40b80000;
            75:midpoint=32'h40c80000;
            76:midpoint=32'h40d80000;
            77:midpoint=32'h40e80000;
            78:midpoint=32'h40f80000;
            79:midpoint=32'h41080000;
            80:midpoint=32'h41180000;
            81:midpoint=32'h41280000;
            82:midpoint=32'h41380000;
            83:midpoint=32'h41480000;
            84:midpoint=32'h41580000;
            85:midpoint=32'h41680000;
            86:midpoint=32'h41780000;
            87:midpoint=32'h41880000;
            88:midpoint=32'h41980000;
            89:midpoint=32'h41a80000;
            90:midpoint=32'h41b80000;
            91:midpoint=32'h41c80000;
            92:midpoint=32'h41d80000;
            93:midpoint=32'h41e80000;
            94:midpoint=32'h41f80000;
            95:midpoint=32'h42080000;
            96:midpoint=32'h42180000;
            97:midpoint=32'h42280000;
            98:midpoint=32'h42380000;
            99:midpoint=32'h42480000;
            100:midpoint=32'h42580000;
            101:midpoint=32'h42680000;
            102:midpoint=32'h42780000;
            103:midpoint=32'h42880000;
            104:midpoint=32'h42980000;
            105:midpoint=32'h42a80000;
            106:midpoint=32'h42b80000;
            107:midpoint=32'h42c80000;
            108:midpoint=32'h42d80000;
            109:midpoint=32'h42e80000;
            110:midpoint=32'h42f80000;
            111:midpoint=32'h43080000;
            112:midpoint=32'h43180000;
            113:midpoint=32'h43280000;
            114:midpoint=32'h43380000;
            115:midpoint=32'h43480000;
            116:midpoint=32'h43580000;
            117:midpoint=32'h43680000;
            118:midpoint=32'h43780000;
            119:midpoint=32'h43880000;
            120:midpoint=32'h43980000;
            121:midpoint=32'h43a80000;
            122:midpoint=32'h43b80000;
            123:midpoint=32'h43c80000;
            124:midpoint=32'h43d80000;
            default:midpoint=0;
        endcase
    endfunction
    function automatic [31:0] boundary(input integer k);
        case(k)
            0:boundary=32'h3e800000;
            1:boundary=32'h3f400000;
            2:boundary=32'h3fa00000;
            3:boundary=32'h3fe00000;
            4:boundary=32'h40200000;
            5:boundary=32'h40600000;
            6:boundary=32'h40a00000;
            default:boundary=0;
        endcase
    endfunction
    function automatic [3:0] encode_element(input logic [31:0] v);
        logic found;
        logic [2:0] code;
        logic [31:0] magnitude;
        begin
            found=0;code=7;magnitude=v&32'h7fffffff;
            for(int k=0;k<7;k++) begin
                if(!found && (magnitude<boundary(k) || (magnitude==boundary(k) && (k&1)==0))) begin code=3'(k);found=1;end
            end
            encode_element={v[31] && magnitude!=0,code};
        end
    endfunction

    // Serialized, area-oriented reference. Each operation retains its own
    // specified binary32 rounding point.
    function automatic [31:0] round_pack(
        input logic sign_bit,
        input logic signed [10:0] exponent,
        input logic [26:0] significand
    );
        integer e, shift;
        logic [26:0] shifted;
        logic [24:0] rounded;
        begin
            e = int'(exponent);
            shifted = significand;
            if (e < 1) begin
                shift = 1-e;
                if (shift >= 27) shifted = {26'b0, |significand};
                else shifted = (significand >> shift) |
                    {26'b0, |(significand << (27-shift))};
                e = 1;
            end
            rounded = {1'b0, shifted[26:3]} +
                {24'b0, shifted[2] && (shifted[1] || shifted[0] || shifted[3])};
            if (rounded[24]) begin
                rounded = rounded >> 1;
                e = e+1;
            end
            if (e >= 255) round_pack = {sign_bit, 8'hff, 23'b0};
            else if (!rounded[23]) round_pack = {sign_bit, 8'b0, rounded[22:0]};
            else round_pack = {sign_bit, e[7:0], rounded[22:0]};
        end
    endfunction

    typedef enum logic [4:0] {
        IDLE, SCAN, BASE_START, MUL_PRODUCT, MUL_NORMALIZE, MUL_ROUND,
        MUL_CHECK, DIV_START, DIV_NORMALIZE, DIV_ALIGN, DIV_RUN,
        DIV_ROUND, DIV_DONE, SCALE_FIND, RESPONSE
    } phase_t;
    phase_t phase;
    logic [31:0] lanes [0:15];
    logic [31:0] global_scale, peak, denominator, raw_scale;
    logic [63:0] packed_q;
    logic [3:0] lane;
    logic bad, multiply_denominator, divide_scale;
    logic [7:0] scale_code, low_code, high_code;
    // E4M3FN = factor * 2**factor_exponent, with a four-bit factor.
    logic [3:0] factor;
    logic signed [10:0] factor_exponent;
    logic [27:0] product;
    logic [26:0] product_sig;
    logic signed [10:0] product_exp;
    logic [31:0] product_result;
    // One radix-2 divider for scale and lanes. The denominator has already
    // passed the finite-normal scale check; numerator zero is handled apart.
    logic [31:0] dividend_bits, divisor_bits, division_result;
    logic [23:0] dividend_mantissa, divisor_mantissa;
    logic [24:0] remainder_bits;
    logic [26:0] quotient, division_sig;
    logic signed [10:0] division_exp;
    logic division_sign;
    logic [4:0] division_bit;
    wire [25:0] difference = {1'b0, remainder_bits} - {2'b0, divisor_mantissa};
    wire [24:0] next_remainder = difference[25] ?
        {remainder_bits[23:0], 1'b0} : {difference[23:0], 1'b0};
    wire [26:0] next_quotient = {quotient[25:0], !difference[25]};
    wire [8:0] code_sum = {1'b0, low_code} + {1'b0, high_code};
    wire [7:0] middle_code = code_sum[8:1];

    assign in_ready = phase == IDLE;
    assign out_valid = phase == RESPONSE;
    assign out_error = out_valid && bad;
    assign out_scale = out_valid && !bad ? scale_code : 8'b0;
    assign out_q0 = out_valid && !bad ? packed_q[0 +: 4] : 4'b0;
    assign out_q1 = out_valid && !bad ? packed_q[4 +: 4] : 4'b0;
    assign out_q2 = out_valid && !bad ? packed_q[8 +: 4] : 4'b0;
    assign out_q3 = out_valid && !bad ? packed_q[12 +: 4] : 4'b0;
    assign out_q4 = out_valid && !bad ? packed_q[16 +: 4] : 4'b0;
    assign out_q5 = out_valid && !bad ? packed_q[20 +: 4] : 4'b0;
    assign out_q6 = out_valid && !bad ? packed_q[24 +: 4] : 4'b0;
    assign out_q7 = out_valid && !bad ? packed_q[28 +: 4] : 4'b0;
    assign out_q8 = out_valid && !bad ? packed_q[32 +: 4] : 4'b0;
    assign out_q9 = out_valid && !bad ? packed_q[36 +: 4] : 4'b0;
    assign out_q10 = out_valid && !bad ? packed_q[40 +: 4] : 4'b0;
    assign out_q11 = out_valid && !bad ? packed_q[44 +: 4] : 4'b0;
    assign out_q12 = out_valid && !bad ? packed_q[48 +: 4] : 4'b0;
    assign out_q13 = out_valid && !bad ? packed_q[52 +: 4] : 4'b0;
    assign out_q14 = out_valid && !bad ? packed_q[56 +: 4] : 4'b0;
    assign out_q15 = out_valid && !bad ? packed_q[60 +: 4] : 4'b0;

    always_ff @(posedge clk) begin
        if (rst) phase <= IDLE;
        else begin
            case (phase)
                IDLE: if (in_valid) begin
                    lanes[0] <= {in_x0, 16'b0};
                    lanes[1] <= {in_x1, 16'b0};
                    lanes[2] <= {in_x2, 16'b0};
                    lanes[3] <= {in_x3, 16'b0};
                    lanes[4] <= {in_x4, 16'b0};
                    lanes[5] <= {in_x5, 16'b0};
                    lanes[6] <= {in_x6, 16'b0};
                    lanes[7] <= {in_x7, 16'b0};
                    lanes[8] <= {in_x8, 16'b0};
                    lanes[9] <= {in_x9, 16'b0};
                    lanes[10] <= {in_x10, 16'b0};
                    lanes[11] <= {in_x11, 16'b0};
                    lanes[12] <= {in_x12, 16'b0};
                    lanes[13] <= {in_x13, 16'b0};
                    lanes[14] <= {in_x14, 16'b0};
                    lanes[15] <= {in_x15, 16'b0};
                    global_scale <= in_global_scale;
                    bad <= in_global_scale[31] || in_global_scale[30:23] == 0 ||
                           in_global_scale[30:23] == 255;
                    peak <= 0;
                    packed_q <= 0;
                    lane <= 0;
                    phase <= SCAN;
                end
                SCAN: begin
                    if (lanes[lane][30:23] == 255) bad <= 1;
                    if ({1'b0, lanes[lane][30:0]} > peak)
                        peak <= {1'b0, lanes[lane][30:0]};
                    if (lane == 15) phase <= BASE_START;
                    else lane <= lane+1'b1;
                end
                BASE_START: begin
                    if (bad) phase <= RESPONSE;
                    else begin
                        factor <= 6;
                        factor_exponent <= 0;
                        multiply_denominator <= 0;
                        phase <= MUL_PRODUCT;
                    end
                end
                MUL_PRODUCT: begin
                    product <= {1'b1, global_scale[22:0]} * factor;
                    phase <= MUL_NORMALIZE;
                end
                MUL_NORMALIZE: begin
                    if (product[27]) begin
                        product_sig <= product[27:1] | {26'b0, product[0]};
                        product_exp <= $signed({3'b0, global_scale[30:23]}) + factor_exponent + 11'sd4;
                    end else if (product[26]) begin
                        product_sig <= product[26:0];
                        product_exp <= $signed({3'b0, global_scale[30:23]}) + factor_exponent + 11'sd3;
                    end else if (product[25]) begin
                        product_sig <= {product[25:0], 1'b0};
                        product_exp <= $signed({3'b0, global_scale[30:23]}) + factor_exponent + 11'sd2;
                    end else if (product[24]) begin
                        product_sig <= {product[24:0], 2'b0};
                        product_exp <= $signed({3'b0, global_scale[30:23]}) + factor_exponent + 11'sd1;
                    end else begin
                        product_sig <= {product[23:0], 3'b0};
                        product_exp <= $signed({3'b0, global_scale[30:23]}) + factor_exponent;
                    end
                    phase <= MUL_ROUND;
                end
                MUL_ROUND: begin
                    product_result <= round_pack(1'b0, product_exp, product_sig);
                    phase <= MUL_CHECK;
                end
                MUL_CHECK: begin
                    if (product_result[30:23] == 0 || product_result[30:23] == 255) begin
                        bad <= 1;
                        phase <= RESPONSE;
                    end else begin
                        divisor_bits <= product_result;
                        if (multiply_denominator) begin
                            denominator <= product_result;
                            dividend_bits <= lanes[0];
                            lane <= 0;
                            divide_scale <= 0;
                        end else begin
                            dividend_bits <= peak;
                            divide_scale <= 1;
                        end
                        phase <= DIV_START;
                    end
                end
                DIV_START: begin
                    division_sign <= dividend_bits[31];
                    if (dividend_bits[30:0] == 0) begin
                        division_result <= {dividend_bits[31], 31'b0};
                        phase <= DIV_DONE;
                    end else begin
                        dividend_mantissa <= {dividend_bits[30:23] != 0, dividend_bits[22:0]};
                        divisor_mantissa <= {1'b1, divisor_bits[22:0]};
                        division_exp <= (dividend_bits[30:23] == 0 ? 11'sd1 :
                            $signed({3'b0, dividend_bits[30:23]})) -
                            $signed({3'b0, divisor_bits[30:23]}) + 11'sd127;
                        phase <= DIV_NORMALIZE;
                    end
                end
                DIV_NORMALIZE: begin
                    if (!dividend_mantissa[23]) begin
                        dividend_mantissa <= dividend_mantissa << 1;
                        division_exp <= division_exp-11'sd1;
                    end else phase <= DIV_ALIGN;
                end
                DIV_ALIGN: begin
                    if (dividend_mantissa < divisor_mantissa) begin
                        remainder_bits <= {dividend_mantissa, 1'b0};
                        division_exp <= division_exp-11'sd1;
                    end else remainder_bits <= {1'b0, dividend_mantissa};
                    quotient <= 0;
                    division_bit <= 26;
                    phase <= DIV_RUN;
                end
                DIV_RUN: begin
                    remainder_bits <= next_remainder;
                    quotient <= next_quotient;
                    if (division_bit == 0) begin
                        division_sig <= next_quotient | {26'b0, |next_remainder};
                        phase <= DIV_ROUND;
                    end else division_bit <= division_bit-1'b1;
                end
                DIV_ROUND: begin
                    division_result <= round_pack(division_sign, division_exp, division_sig);
                    phase <= DIV_DONE;
                end
                DIV_DONE: begin
                    if (divide_scale) begin
                        raw_scale <= division_result[30:0] == 0 ? 32'h3f800000 : division_result;
                        low_code <= 1;
                        high_code <= 126;
                        phase <= SCALE_FIND;
                    end else begin
                        packed_q[4*lane +: 4] <= encode_element(division_result);
                        if (lane == 15) phase <= RESPONSE;
                        else begin
                            lane <= lane+1'b1;
                            dividend_bits <= lanes[lane+1'b1];
                            divisor_bits <= denominator;
                            phase <= DIV_START;
                        end
                    end
                end
                SCALE_FIND: begin
                    if (low_code == high_code) begin
                        scale_code <= low_code;
                        if (low_code < 8) begin
                            factor <= low_code[3:0];
                            factor_exponent <= -11'sd9;
                        end else begin
                            factor <= {1'b1, low_code[2:0]};
                            factor_exponent <= $signed({7'b0, low_code[6:3]})-11'sd10;
                        end
                        multiply_denominator <= 1;
                        phase <= MUL_PRODUCT;
                    end else if (raw_scale < midpoint(int'(middle_code)-1) ||
                        (raw_scale == midpoint(int'(middle_code)-1) && !middle_code[0]))
                        high_code <= middle_code;
                    else low_code <= middle_code+1'b1;
                end
                RESPONSE: if (out_ready) phase <= IDLE;
                default: phase <= IDLE;
            endcase
        end
    end
endmodule
