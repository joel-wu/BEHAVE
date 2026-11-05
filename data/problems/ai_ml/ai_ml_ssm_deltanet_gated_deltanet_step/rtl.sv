module ai_ml_ssm_deltanet_gated_deltanet_step #(
    parameter integer K = 8,
    parameter integer V = 8
) (
    input logic clk, rst, in_valid,
    output logic in_ready,
    input logic in_start,
    input logic [32*K-1:0] in_q,
    input logic [32*K-1:0] in_k,
    input logic [32*V-1:0] in_v,
    input logic [31:0] in_g,
    input logic [31:0] in_beta,
    input logic [31:0] in_A_log,
    input logic [31:0] in_dt_bias,
    output logic out_valid,
    input logic out_ready,
    output logic [32*V-1:0] out_o
);

function automatic logic [63:0] fp_shift_jam(input logic [63:0] x, input integer n);
    if (n <= 0) fp_shift_jam = x;
    else if (n >= 64) fp_shift_jam = {63'b0, |x};
    else fp_shift_jam = (x >> n) | {63'b0, |(x << (64-n))};
endfunction

function automatic logic [26:0] fp_shift_jam27(input logic [26:0] x, input logic [10:0] n);
    if (n == 0) fp_shift_jam27 = x;
    else if (n >= 27) fp_shift_jam27 = {26'b0, |x};
    else fp_shift_jam27 = (x >> n) | {26'b0, |(x << (27-n))};
endfunction

function automatic logic [31:0] fp_round_pack(
    input logic sign_bit, input logic signed [10:0] exponent, input logic [26:0] sig
);
    logic signed [10:0] e;
    logic [26:0] shifted;
    logic [24:0] rounded;
    begin
        e = exponent;
        shifted = sig;
        if (e < 1) begin
            shifted = fp_shift_jam27(shifted, 11'sd1-e);
            e = 1;
        end
        rounded = {1'b0, shifted[26:3]} + {24'b0, (shifted[2] &&
                  (shifted[1] || shifted[0] || shifted[3]))};
        if (rounded[24]) begin
            rounded = rounded >> 1;
            e = e+1;
        end
        if (e >= 255) fp_round_pack = {sign_bit, 8'hff, 23'b0};
        else if (!rounded[23])
            fp_round_pack = {sign_bit, 8'b0, rounded[22:0]};
        else fp_round_pack = {sign_bit, e[7:0], rounded[22:0]};
    end
endfunction

function automatic logic [4:0] leading24(input logic [23:0] word);
    begin
        leading24 = 24;
        for (integer bit_index=0; bit_index<24; bit_index=bit_index+1)
            if (word[bit_index]) leading24 = 5'(23-bit_index);
    end
endfunction

function automatic logic [4:0] leading27(input logic [26:0] word);
    begin
        leading27 = 27;
        for (integer bit_index=0; bit_index<27; bit_index=bit_index+1)
            if (word[bit_index]) leading27 = 5'(26-bit_index);
    end
endfunction

// Packet: special flag, special value, sign, signed exponent, guard/round/sticky significand.
function automatic logic [31:0] fp_finish(input logic [71:0] packet);
    fp_finish = packet[71] ? packet[70:39] :
        fp_round_pack(packet[38], $signed(packet[37:27]), packet[26:0]);
endfunction

function automatic logic [71:0] fp32_add_stage(input logic [31:0] a, b);
    logic [8:0] ea, eb, e;
    logic [4:0] distance;
    logic sa, sb, sign_bit;
    logic [23:0] ma, mb;
    logic [26:0] hi, lo;
    logic [27:0] sum;
    begin
        sa = a[31]; sb = b[31];
        ea = a[30:23] == 0 ? 9'd1 : {1'b0, a[30:23]};
        eb = b[30:23] == 0 ? 9'd1 : {1'b0, b[30:23]};
        ma = {(a[30:23] != 0), a[22:0]};
        mb = {(b[30:23] != 0), b[22:0]};
        if ((a[30:0] > 31'h7f800000) || (b[30:0] > 31'h7f800000))
            fp32_add_stage = {1'b1, 32'h7fc00000, 39'b0};
        else if ((a[30:0] == 31'h7f800000) && (b[30:0] == 31'h7f800000) && (sa != sb))
            fp32_add_stage = {1'b1, 32'h7fc00000, 39'b0};
        else if (a[30:0] == 31'h7f800000) fp32_add_stage = {1'b1, a, 39'b0};
        else if (b[30:0] == 31'h7f800000) fp32_add_stage = {1'b1, b, 39'b0};
        else if ((a[30:0] == 0) && (b[30:0] == 0))
            fp32_add_stage = {1'b1, sa & sb, 70'b0};
        else begin
            if ((ea > eb) || ((ea == eb) && (ma >= mb))) begin
                hi = {ma, 3'b0}; lo = fp_shift_jam27({mb, 3'b0}, {2'b0, ea-eb});
                sign_bit = sa; e = ea;
            end else begin
                hi = {mb, 3'b0}; lo = fp_shift_jam27({ma, 3'b0}, {2'b0, eb-ea});
                sign_bit = sb; e = eb;
            end
            sum = sa == sb ? hi+lo : hi-lo;
            if (sum == 0) fp32_add_stage = {1'b1, 71'b0};
            else begin
                if (sum[27]) begin
                    sum = {1'b0, sum[27:2], sum[1] | sum[0]}; e = e+1;
                end
                distance = leading27(sum[26:0]);
                if ({4'b0, distance} >= e) distance = 5'(e-1);
                sum = sum << distance;
                e = e - {4'b0, distance};
                fp32_add_stage = {1'b0, 32'b0, sign_bit, 2'b0, e, sum[26:0]};
            end
        end
    end
endfunction

function automatic logic [31:0] fp32_add_bits(input logic [31:0] a, b);
    fp32_add_bits = fp_finish(fp32_add_stage(a, b));
endfunction

function automatic logic [71:0] fp32_mul_stage(input logic [31:0] a, b);
    logic signed [10:0] ea, eb, e;
    logic [4:0] za, zb;
    logic sign_bit;
    logic [23:0] ma, mb;
    logic [47:0] product;
    logic [63:0] sig;
    begin
        sign_bit = a[31] ^ b[31];
        ea = a[30:23] == 0 ? 11'sd1 : $signed({3'b0, a[30:23]});
        eb = b[30:23] == 0 ? 11'sd1 : $signed({3'b0, b[30:23]});
        ma = {(a[30:23] != 0), a[22:0]};
        mb = {(b[30:23] != 0), b[22:0]};
        if ((a[30:0] > 31'h7f800000) || (b[30:0] > 31'h7f800000) ||
            ((a[30:0] == 31'h7f800000) && (b[30:0] == 0)) ||
            ((b[30:0] == 31'h7f800000) && (a[30:0] == 0)))
            fp32_mul_stage = {1'b1, 32'h7fc00000, 39'b0};
        else if ((a[30:0] == 31'h7f800000) || (b[30:0] == 31'h7f800000))
            fp32_mul_stage = {1'b1, sign_bit, 8'hff, 62'b0};
        else if ((a[30:0] == 0) || (b[30:0] == 0))
            fp32_mul_stage = {1'b1, sign_bit, 70'b0};
        else begin
            za = leading24(ma); zb = leading24(mb);
            ma = ma << za; mb = mb << zb;
            ea = ea - $signed({6'b0, za}); eb = eb - $signed({6'b0, zb});
            product = ma*mb;
            e = ea+eb-127;
            if (product[47]) begin sig=fp_shift_jam({16'b0, product},21); e=e+1; end
            else sig=fp_shift_jam({16'b0, product},20);
            fp32_mul_stage = {1'b0, 32'b0, sign_bit, e, sig[26:0]};
        end
    end
endfunction

function automatic logic [31:0] fp32_mul_bits(input logic [31:0] a, b);
    fp32_mul_bits = fp_finish(fp32_mul_stage(a, b));
endfunction

function automatic logic [31:0] fp32_sqrt_bits(input logic [31:0] a);
    integer e, i;
    logic [63:0] mant, radicand, root, rem, trial;
    begin
        if (a[30:0] > 31'h7f800000) fp32_sqrt_bits = 32'h7fc00000;
        else if (a[30:0] == 0) fp32_sqrt_bits = a;
        else if (a[31]) fp32_sqrt_bits = 32'h7fc00000;
        else if (a[30:0] == 31'h7f800000) fp32_sqrt_bits = a;
        else begin
            e = a[30:23] == 0 ? 1 : int'(a[30:23]);
            mant = {40'b0, (a[30:23] != 0), a[22:0]};
            for (i=0; i<23; i=i+1) begin
                if (!mant[23]) begin mant=mant<<1; e=e-1; end
            end
            e = e-127;
            if (e & 1) mant = mant<<1;
            radicand = mant<<29;
            root = 0; rem = 0;
            for (i=26; i>=0; i=i-1) begin
                rem = (rem<<2) | ((radicand>>(2*i)) & 64'd3);
                trial = (root<<2) | 64'd1;
                root = root<<1;
                if (rem >= trial) begin rem=rem-trial; root=root|64'd1; end
            end
            root = root | {63'b0, (rem != 0)};
            fp32_sqrt_bits = fp_round_pack(1'b0, 11'((e >>> 1)+127), root[26:0]);
        end
    end
endfunction

function automatic logic [31:0] fp32_div_bits(input logic [31:0] a, b);
    integer ea, eb, e, i;
    logic sign_bit;
    logic [63:0] ma, mb, numerator, quotient, remainder;
    begin
        sign_bit = a[31] ^ b[31];
        ea = a[30:23] == 0 ? 1 : int'(a[30:23]);
        eb = b[30:23] == 0 ? 1 : int'(b[30:23]);
        ma = {40'b0, (a[30:23] != 0), a[22:0]};
        mb = {40'b0, (b[30:23] != 0), b[22:0]};
        if ((a[30:0] > 31'h7f800000) || (b[30:0] > 31'h7f800000) ||
            ((a[30:0] == 31'h7f800000) && (b[30:0] == 31'h7f800000)) ||
            ((a[30:0] == 0) && (b[30:0] == 0)))
            fp32_div_bits = 32'h7fc00000;
        else if ((a[30:0] == 31'h7f800000) || (b[30:0] == 0))
            fp32_div_bits = {sign_bit, 8'hff, 23'b0};
        else if ((a[30:0] == 0) || (b[30:0] == 31'h7f800000))
            fp32_div_bits = {sign_bit, 31'b0};
        else begin
            for (i=0; i<23; i=i+1) begin
                if (!ma[23]) begin ma=ma<<1; ea=ea-1; end
                if (!mb[23]) begin mb=mb<<1; eb=eb-1; end
            end
            e = ea-eb+127;
            if (ma < mb) begin ma=ma<<1; e=e-1; end
            numerator = ma<<26;
            quotient = numerator/mb;
            remainder = numerator%mb;
            quotient = quotient | {63'b0, (remainder != 0)};
            fp32_div_bits = fp_round_pack(sign_bit, 11'(e), quotient[26:0]);
        end
    end
endfunction

    function automatic [31:0] exp2_node(input integer index);
        case(index)
            0: exp2_node = 32'd1073741824;
            1: exp2_node = 32'd1097253708;
            2: exp2_node = 32'd1121280436;
            3: exp2_node = 32'd1145833280;
            4: exp2_node = 32'd1170923762;
            5: exp2_node = 32'd1196563654;
            6: exp2_node = 32'd1222764986;
            7: exp2_node = 32'd1249540052;
            8: exp2_node = 32'd1276901417;
            9: exp2_node = 32'd1304861917;
            10: exp2_node = 32'd1333434672;
            11: exp2_node = 32'd1362633090;
            12: exp2_node = 32'd1392470869;
            13: exp2_node = 32'd1422962010;
            14: exp2_node = 32'd1454120821;
            15: exp2_node = 32'd1485961921;
            16: exp2_node = 32'd1518500250;
            17: exp2_node = 32'd1551751076;
            18: exp2_node = 32'd1585730000;
            19: exp2_node = 32'd1620452965;
            20: exp2_node = 32'd1655936265;
            21: exp2_node = 32'd1692196547;
            22: exp2_node = 32'd1729250827;
            23: exp2_node = 32'd1767116489;
            24: exp2_node = 32'd1805811301;
            25: exp2_node = 32'd1845353420;
            26: exp2_node = 32'd1885761398;
            27: exp2_node = 32'd1927054196;
            28: exp2_node = 32'd1969251188;
            29: exp2_node = 32'd2012372174;
            30: exp2_node = 32'd2056437387;
            31: exp2_node = 32'd2101467502;
            32: exp2_node = 32'd2147483648;
            default: exp2_node=32'd2147483648;
        endcase
    endfunction
    // Packet: special flag/value, otherwise signed Q8.16 range reduction.
    function automatic [56:0] exp2_quantize(input logic [31:0] x);
        integer exponent, shift, q;
        logic [31:0] magnitude, mantissa;
        begin
            magnitude=x & 32'h7fffffff; exponent=int'(x[30:23]);
            q=0; shift=0; mantissa=0;
            if (magnitude>32'h7f800000) exp2_quantize={1'b1,32'h7fc00000,24'b0};
            else if (magnitude==32'h7f800000)
                exp2_quantize={1'b1,(x[31] ? 32'b0 : 32'h7f800000),24'b0};
            else if (exponent==0) exp2_quantize={1'b1,32'h3f800000,24'b0};
            else if (x[31] && magnitude>32'h42fc0000) exp2_quantize={1'b1,56'b0};
            else if (!x[31] && magnitude>=32'h43000000)
                exp2_quantize={1'b1,32'h7f800000,24'b0};
            else begin
                if (exponent<111) q=x[31] ? -1 : 0;
                else begin
                    shift=134-exponent;
                    mantissa={8'd0,1'b1,x[22:0]};
                    if (x[31]) q=-int'((mantissa+(32'd1<<shift)-1)>>shift);
                    else q=int'(mantissa>>shift);
                end
                exp2_quantize={1'b0,32'b0,q[23:0]};
            end
        end
    endfunction

    function automatic [31:0] exp2_pack(input logic [31:0] y, input logic signed [8:0] n);
        logic [24:0] significand;
        logic signed [8:0] adjusted;
        logic [7:0] encoded_exp;
        begin
            significand=y[31:7]; adjusted=n;
            if (y[6:0]>7'd64 || (y[6:0]==7'd64 && significand[0])) significand=significand+1;
            if (significand[24]) begin significand=significand>>1;adjusted=n+1;end
            encoded_exp=8'(adjusted+127);
            exp2_pack=adjusted>127 ? 32'h7f800000 : {1'b0,encoded_exp,significand[22:0]};
        end
    endfunction

    function automatic [31:0] exp2_finite(input logic [31:0] x);
        logic [56:0] quantized;
        logic [31:0] lo, hi, y;
        logic [36:0] product;
        begin
            quantized=exp2_quantize(x);
            lo=exp2_node(int'(quantized[15:11]));
            hi=exp2_node(int'(quantized[15:11])+1);
            product=26'(hi-lo)*quantized[10:0];
            y=lo+{6'b0,product[36:11]};
            exp2_finite=quantized[56] ? quantized[55:24] :
                exp2_pack(y, $signed({quantized[23],quantized[23:16]}));
        end
    endfunction
    function automatic [31:0] exp_finite(input logic [31:0] x);
        exp_finite=exp2_finite(fp32_mul_bits(x,32'h3fb8aa3b));
    endfunction

    function automatic [31:0] dimension_bits(input integer value);
        integer exponent;
        logic [63:0] significand;
        begin
            exponent = 0;
            for (integer bit_index = 0; bit_index < 31; bit_index++)
                if ((value >> bit_index) != 0) exponent = bit_index;
            significand = 64'(value);
            if (exponent <= 26) significand = significand << (26-exponent);
            else significand = fp_shift_jam(significand, exponent-26);
            dimension_bits = fp_round_pack(0, 11'(exponent+127), significand[26:0]);
        end
    endfunction
    localparam logic [31:0] QUERY_SCALE =
        fp32_div_bits(32'h3f800000, fp32_sqrt_bits(dimension_bits(K)));

    // One shared scalar datapath. Matrix state stays inside the kernel.
    typedef enum logic [5:0] {
        IDLE, NORM_SQUARE, NORM_SUM, NORM_EPS, NORM_ROOT, NORM_DIV, QUERY_MUL,
        GATE_BIAS, SP_EXP_SCALE, SP_EXP, SP_DEN, SP_DIV, SP_SQUARE, SP_MUL, SP_DIV_TERM,
        SP_SUM, SP_DOUBLE, SP_FINISH, A_EXP_SCALE, A_EXP, DECAY_MUL, DECAY_EXP_SCALE, DECAY_EXP,
        H_DECAY, PROJECT_MUL, PROJECT_SUM, RESIDUAL, BETA_MUL,
        UPDATE_MUL, UPDATE_ADD, READ_MUL, READ_SUM, DONE
    } stage_t;
    stage_t stage;
    logic [31:0] H [0:K*V-1];
    logic [31:0] q [0:K-1], k [0:K-1], v [0:V-1];
    logic [31:0] saved_A, saved_bias, g, beta;
    logic [31:0] accumulator, temporary, root, gate_arg, exponential;
    logic [31:0] w, w2, term, total, dt, decay, delta;
    logic norm_key;
    logic [(K > 1 ? $clog2(K) : 1)-1:0] i;
    logic [(V > 1 ? $clog2(V) : 1)-1:0] j;
    logic [(K*V > 1 ? $clog2(K*V) : 1)-1:0] position;
    logic [2:0] series_index;
    logic [31:0] operand_a, operand_b, alu_a, alu_b, alu_value, alu_result, divisor;
    logic [71:0] alu_packet, alu_packet_value;
    logic [56:0] exp_quantized_value;
    logic [23:0] exp_quantized;
    logic [31:0] exp_lo, exp_y;
    logic [25:0] exp_difference;
    logic [10:0] exp_fraction;
    logic [36:0] exp_product;
    logic signed [8:0] exp_exponent;
    logic [2:0] operation, alu_operation;
    typedef enum logic [2:0] {
        FETCH, EXECUTE, PACK, EXP_TABLE, EXP_PRODUCT, EXP_ADD, EXP_PACK, WRITEBACK
    } compute_t;
    compute_t compute_phase;
    logic slow_busy, slow_done;
    logic [31:0] slow_result;
    wire slow_operation = alu_operation == 2 || alu_operation == 3;
    gated_delta_iterative_fp iterative_fp (
        .clk(clk), .rst(rst),
        .start(compute_phase == EXECUTE && slow_operation && !slow_busy && !slow_done),
        .square_root(alu_operation == 3), .a(alu_a), .b(alu_b),
        .busy(slow_busy), .done(slow_done), .result(slow_result)
    );

    always_comb begin
        case (series_index)
            0: divisor = 32'h40400000;
            1: divisor = 32'h40a00000;
            2: divisor = 32'h40e00000;
            3: divisor = 32'h41100000;
            default: divisor = 32'h41300000;
        endcase
        operand_a = 0;
        operand_b = 0;
        operation = 0;
        case (stage)
            NORM_SQUARE: begin
                operand_a = norm_key ? k[i] : q[i];
                operand_b = operand_a; operation = 1;
            end
            NORM_SUM, PROJECT_SUM, READ_SUM: begin
                operand_a = accumulator; operand_b = temporary;
            end
            NORM_EPS: begin operand_a = accumulator; operand_b = 32'h358637bd; end
            NORM_ROOT: begin operand_a = temporary; operation = 3; end
            NORM_DIV: begin
                operand_a = norm_key ? k[i] : q[i]; operand_b = root; operation = 2;
            end
            QUERY_MUL: begin operand_a = q[i]; operand_b = QUERY_SCALE; operation = 1; end
            GATE_BIAS: begin operand_a = g; operand_b = saved_bias; end
            SP_EXP_SCALE: begin
                operand_a = gate_arg | 32'h80000000; operand_b = 32'h3fb8aa3b; operation = 1;
            end
            SP_EXP, A_EXP, DECAY_EXP: begin operand_a = temporary; operation = 4; end
            SP_DEN: begin operand_a = 32'h40000000; operand_b = exponential; end
            SP_DIV: begin operand_a = exponential; operand_b = temporary; operation = 2; end
            SP_SQUARE: begin operand_a = w; operand_b = w; operation = 1; end
            SP_MUL: begin operand_a = term; operand_b = w2; operation = 1; end
            SP_DIV_TERM: begin operand_a = term; operand_b = divisor; operation = 2; end
            SP_SUM: begin operand_a = total; operand_b = temporary; end
            SP_DOUBLE: begin operand_a = 32'h40000000; operand_b = total; operation = 1; end
            SP_FINISH: begin operand_a = gate_arg; operand_b = temporary; end
            A_EXP_SCALE: begin operand_a = saved_A; operand_b = 32'h3fb8aa3b; operation = 1; end
            DECAY_MUL: begin operand_a = temporary; operand_b = dt; operation = 1; end
            DECAY_EXP_SCALE: begin operand_a = temporary; operand_b = 32'h3fb8aa3b; operation = 1; end
            H_DECAY: begin operand_a = H[position]; operand_b = decay; operation = 1; end
            PROJECT_MUL: begin operand_a = H[i*V+j]; operand_b = k[i]; operation = 1; end
            RESIDUAL: begin operand_a = v[j]; operand_b = accumulator ^ 32'h80000000; end
            BETA_MUL: begin operand_a = beta; operand_b = temporary; operation = 1; end
            UPDATE_MUL: begin operand_a = k[i]; operand_b = delta; operation = 1; end
            UPDATE_ADD: begin operand_a = H[i*V+j]; operand_b = temporary; end
            READ_MUL: begin operand_a = H[i*V+j]; operand_b = q[i]; operation = 1; end
            default: begin end
        endcase
    end

    always_comb begin
        alu_packet_value = alu_operation == 1 ? fp32_mul_stage(alu_a, alu_b) :
            fp32_add_stage(alu_a, alu_b);
        exp_quantized_value = exp2_quantize(alu_a);
        case (alu_operation)
            2, 3: alu_value = slow_result;
            default: alu_value = 0;
        endcase
    end

    // Separate array selection, arithmetic, and state writeback clock edges.
    always_ff @(posedge clk) begin
        if (rst) begin
            compute_phase <= FETCH;
            alu_operation <= 0;
        end else if (stage == IDLE || stage == DONE) compute_phase <= FETCH;
        else case (compute_phase)
            FETCH: begin
                alu_a <= operand_a; alu_b <= operand_b; alu_operation <= operation;
                compute_phase <= EXECUTE;
            end
            EXECUTE: if (!slow_operation || slow_done) begin
                if (alu_operation == 0 || alu_operation == 1) begin
                    alu_packet <= alu_packet_value;
                    compute_phase <= PACK;
                end else if (alu_operation == 4) begin
                    if (exp_quantized_value[56]) begin
                        alu_result <= exp_quantized_value[55:24];
                        compute_phase <= WRITEBACK;
                    end else begin
                        exp_quantized <= exp_quantized_value[23:0];
                        compute_phase <= EXP_TABLE;
                    end
                end else begin
                    alu_result <= alu_value;
                    compute_phase <= WRITEBACK;
                end
            end
            PACK: begin alu_result <= fp_finish(alu_packet); compute_phase <= WRITEBACK; end
            EXP_TABLE: begin
                exp_lo <= exp2_node(int'(exp_quantized[15:11]));
                exp_difference <= 26'(exp2_node(int'(exp_quantized[15:11])+1) -
                    exp2_node(int'(exp_quantized[15:11])));
                exp_fraction <= exp_quantized[10:0];
                exp_exponent <= $signed({exp_quantized[23],exp_quantized[23:16]});
                compute_phase <= EXP_PRODUCT;
            end
            EXP_PRODUCT: begin exp_product <= exp_difference*exp_fraction; compute_phase <= EXP_ADD; end
            EXP_ADD: begin exp_y <= exp_lo+{6'b0,exp_product[36:11]}; compute_phase <= EXP_PACK; end
            EXP_PACK: begin alu_result <= exp2_pack(exp_y,exp_exponent); compute_phase <= WRITEBACK; end
            WRITEBACK: compute_phase <= FETCH;
            default: compute_phase <= FETCH;
        endcase
    end

    assign in_ready = !rst && stage == IDLE && (!out_valid || out_ready);
    always_ff @(posedge clk) begin
        if (rst) begin
            stage <= IDLE;
            for (integer index = 0; index < K*V; index++) H[index] <= 0;
            saved_A <= 0; saved_bias <= 0;
            out_valid <= 0; out_o <= 0;
            i <= 0; j <= 0; position <= 0; series_index <= 0; norm_key <= 0;
        end else begin
            if (out_valid && out_ready) out_valid <= 0;
            if (stage == IDLE || stage == DONE || compute_phase == WRITEBACK) case (stage)
                IDLE: if (in_valid && in_ready) begin
                    for (integer lane = 0; lane < K; lane++) begin
                        q[lane] <= in_q[32*lane +: 32];
                        k[lane] <= in_k[32*lane +: 32];
                    end
                    for (integer lane = 0; lane < V; lane++) v[lane] <= in_v[32*lane +: 32];
                    if (in_start) begin
                        for (integer index = 0; index < K*V; index++) H[index] <= 0;
                        saved_A <= in_A_log; saved_bias <= in_dt_bias;
                    end
                    g <= in_g; beta <= in_beta;
                    i <= 0; norm_key <= 0; stage <= NORM_SQUARE;
                end
                NORM_SQUARE: begin temporary <= alu_result; stage <= NORM_SUM; end
                NORM_SUM: begin
                    accumulator <= i == 0 ? temporary : alu_result;
                    if (i == K-1) stage <= NORM_EPS;
                    else begin i <= i+1; stage <= NORM_SQUARE; end
                end
                NORM_EPS: begin temporary <= alu_result; stage <= NORM_ROOT; end
                NORM_ROOT: begin root <= alu_result; i <= 0; stage <= NORM_DIV; end
                NORM_DIV: begin
                    if (norm_key) begin
                        k[i] <= alu_result;
                        if (i == K-1) stage <= GATE_BIAS;
                        else i <= i+1;
                    end else begin q[i] <= alu_result; stage <= QUERY_MUL; end
                end
                QUERY_MUL: begin
                    q[i] <= alu_result;
                    if (i == K-1) begin i <= 0; norm_key <= 1; stage <= NORM_SQUARE; end
                    else begin i <= i+1; stage <= NORM_DIV; end
                end
                GATE_BIAS: begin gate_arg <= alu_result; stage <= SP_EXP_SCALE; end
                SP_EXP_SCALE: begin temporary <= alu_result; stage <= SP_EXP; end
                SP_EXP: begin exponential <= alu_result; stage <= SP_DEN; end
                SP_DEN: begin temporary <= alu_result; stage <= SP_DIV; end
                SP_DIV: begin w <= alu_result; term <= alu_result; total <= alu_result; stage <= SP_SQUARE; end
                SP_SQUARE: begin w2 <= alu_result; series_index <= 0; stage <= SP_MUL; end
                SP_MUL: begin term <= alu_result; stage <= SP_DIV_TERM; end
                SP_DIV_TERM: begin temporary <= alu_result; stage <= SP_SUM; end
                SP_SUM: begin
                    total <= alu_result;
                    if (series_index == 4) stage <= SP_DOUBLE;
                    else begin series_index <= series_index+1; stage <= SP_MUL; end
                end
                SP_DOUBLE: begin temporary <= alu_result; stage <= SP_FINISH; end
                SP_FINISH: begin dt <= gate_arg[31] ? temporary : alu_result; stage <= A_EXP_SCALE; end
                A_EXP_SCALE: begin temporary <= alu_result; stage <= A_EXP; end
                A_EXP: begin temporary <= alu_result; stage <= DECAY_MUL; end
                DECAY_MUL: begin temporary <= alu_result ^ 32'h80000000; stage <= DECAY_EXP_SCALE; end
                DECAY_EXP_SCALE: begin temporary <= alu_result; stage <= DECAY_EXP; end
                DECAY_EXP: begin decay <= alu_result; position <= 0; stage <= H_DECAY; end
                H_DECAY: begin
                    H[position] <= alu_result;
                    if (position == K*V-1) begin i <= 0; j <= 0; stage <= PROJECT_MUL; end
                    else position <= position+1;
                end
                PROJECT_MUL: begin temporary <= alu_result; stage <= PROJECT_SUM; end
                PROJECT_SUM: begin
                    accumulator <= i == 0 ? temporary : alu_result;
                    if (i == K-1) stage <= RESIDUAL;
                    else begin i <= i+1; stage <= PROJECT_MUL; end
                end
                RESIDUAL: begin temporary <= alu_result; stage <= BETA_MUL; end
                BETA_MUL: begin delta <= alu_result; i <= 0; stage <= UPDATE_MUL; end
                UPDATE_MUL: begin temporary <= alu_result; stage <= UPDATE_ADD; end
                UPDATE_ADD: begin
                    H[i*V+j] <= alu_result;
                    if (i == K-1) begin i <= 0; stage <= READ_MUL; end
                    else begin i <= i+1; stage <= UPDATE_MUL; end
                end
                READ_MUL: begin temporary <= alu_result; stage <= READ_SUM; end
                READ_SUM: begin
                    accumulator <= i == 0 ? temporary : alu_result;
                    if (i == K-1) begin
                        out_o[32*j +: 32] <= i == 0 ? temporary : alu_result;
                        if (j == V-1) stage <= DONE;
                        else begin j <= j+1; i <= 0; stage <= PROJECT_MUL; end
                    end else begin i <= i+1; stage <= READ_MUL; end
                end
                DONE: begin out_valid <= 1; stage <= IDLE; end
                default: stage <= IDLE;
            endcase
        end
    end
endmodule

// Exact binary32 divide/sqrt. Each quotient/root bit has its own clock edge.
// The kernel holds its operation and operands until done.
module gated_delta_iterative_fp (
    input logic clk, rst, start, square_root,
    input logic [31:0] a, b,
    output logic busy, done,
    output logic [31:0] result
);
    typedef enum logic [3:0] {
        IDLE, NORMALIZE, PREPARE, DIVIDE, ROOT, ROUND, FINISH
    } phase_t;
    phase_t phase;
    logic is_root, sign_bit;
    logic [23:0] ma, mb;
    logic signed [10:0] ea, eb, exponent;
    logic [24:0] div_rem;
    logic [26:0] quotient, root;
    logic [28:0] root_rem;
    logic [53:0] radicand;
    logic [5:0] remaining;
    logic [26:0] significand;

    wire div_bit = div_rem >= {1'b0, mb};
    wire [24:0] div_reduced = div_bit ? div_rem - {1'b0, mb} : div_rem;
    wire [26:0] next_quotient = {quotient[25:0], div_bit};
    wire [28:0] root_shifted = {root_rem[26:0], radicand[53:52]};
    wire [28:0] root_trial = {root, 2'b01};
    wire root_bit = root_shifted >= root_trial;
    wire [28:0] root_reduced = root_bit ? root_shifted - root_trial : root_shifted;
    wire [26:0] next_root = {root[25:0], root_bit};
    wire signed [10:0] unbiased = ea - 11'sd127;

    function automatic [31:0] round_pack(
        input logic sign_value,
        input logic signed [10:0] e,
        input logic [26:0] sig
    );
        logic [26:0] shifted;
        logic [24:0] rounded;
        logic signed [10:0] adjusted;
        integer distance;
        begin
            shifted = sig;
            adjusted = e;
            if (e < 1) begin
                distance = 1 - int'(e);
                if (distance >= 27) shifted = {26'b0, |sig};
                else shifted = (sig >> distance) | {26'b0, |(sig << (27-distance))};
                adjusted = 1;
            end
            rounded = {1'b0, shifted[26:3]} + {24'b0,
                shifted[2] && (shifted[1] || shifted[0] || shifted[3])};
            if (rounded[24]) begin rounded = rounded >> 1; adjusted = adjusted + 1; end
            if (adjusted >= 255) round_pack = {sign_value, 8'hff, 23'b0};
            else if (!rounded[23]) round_pack = {sign_value, 8'b0, rounded[22:0]};
            else round_pack = {sign_value, adjusted[7:0], rounded[22:0]};
        end
    endfunction

    assign busy = phase != IDLE && phase != FINISH;
    assign done = phase == FINISH;
    always_ff @(posedge clk) begin
        if (rst) begin
            phase <= IDLE;
            result <= 0;
        end else case (phase)
            IDLE: if (start) begin
                is_root <= square_root;
                sign_bit <= square_root ? 1'b0 : a[31] ^ b[31];
                ma <= {a[30:23] != 0, a[22:0]};
                mb <= {b[30:23] != 0, b[22:0]};
                ea <= a[30:23] == 0 ? 11'sd1 : $signed({3'b0, a[30:23]});
                eb <= b[30:23] == 0 ? 11'sd1 : $signed({3'b0, b[30:23]});
                phase <= NORMALIZE;
                if (square_root) begin
                    if (a[30:0] > 31'h7f800000 || (a[31] && a[30:0] != 0)) begin
                        result <= 32'h7fc00000; phase <= FINISH;
                    end else if (a[30:0] == 0 || a[30:0] == 31'h7f800000) begin
                        result <= a; phase <= FINISH;
                    end
                end else begin
                    if (a[30:0] > 31'h7f800000 || b[30:0] > 31'h7f800000 ||
                        (a[30:0] == 0 && b[30:0] == 0) ||
                        (a[30:0] == 31'h7f800000 && b[30:0] == 31'h7f800000)) begin
                        result <= 32'h7fc00000; phase <= FINISH;
                    end else if (a[30:0] == 31'h7f800000 || b[30:0] == 0) begin
                        result <= {a[31] ^ b[31], 8'hff, 23'b0}; phase <= FINISH;
                    end else if (a[30:0] == 0 || b[30:0] == 31'h7f800000) begin
                        result <= {a[31] ^ b[31], 31'b0}; phase <= FINISH;
                    end
                end
            end
            NORMALIZE: begin
                if (!ma[23]) begin ma <= ma << 1; ea <= ea - 1; end
                if (!is_root && !mb[23]) begin mb <= mb << 1; eb <= eb - 1; end
                if (ma[23] && (is_root || mb[23])) phase <= PREPARE;
            end
            PREPARE: begin
                remaining <= 26;
                quotient <= 0; root <= 0; root_rem <= 0;
                if (is_root) begin
                    radicand <= unbiased[0] ? {ma, 30'b0} : {1'b0, ma, 29'b0};
                    exponent <= (unbiased >>> 1) + 11'sd127;
                    phase <= ROOT;
                end else begin
                    div_rem <= ma < mb ? {ma, 1'b0} : {1'b0, ma};
                    exponent <= ea - eb + 11'sd127 - (ma < mb ? 11'sd1 : 11'sd0);
                    phase <= DIVIDE;
                end
            end
            DIVIDE: begin
                div_rem <= {div_reduced[23:0], 1'b0};
                quotient <= next_quotient;
                remaining <= remaining - 1;
                if (remaining == 0) begin
                    significand <= next_quotient | {26'b0, |div_reduced};
                    phase <= ROUND;
                end
            end
            ROOT: begin
                radicand <= radicand << 2;
                root_rem <= root_reduced;
                root <= next_root;
                remaining <= remaining - 1;
                if (remaining == 0) begin
                    significand <= next_root | {26'b0, |root_reduced};
                    phase <= ROUND;
                end
            end
            ROUND: begin result <= round_pack(sign_bit, exponent, significand); phase <= FINISH; end
            FINISH: phase <= IDLE;
            default: phase <= IDLE;
        endcase
    end
endmodule
