module ai_ml_moe_route_moe_deepseek_v3_sigmoid_gate (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_router_logits_0,
    input  logic [31:0] in_router_logits_1,
    input  logic [31:0] in_router_logits_2,
    input  logic [31:0] in_router_logits_3,
    input  logic [31:0] in_router_logits_4,
    input  logic [31:0] in_router_logits_5,
    input  logic [31:0] in_router_logits_6,
    input  logic [31:0] in_router_logits_7,
    input  logic [31:0] in_e_score_correction_bias_0,
    input  logic [31:0] in_e_score_correction_bias_1,
    input  logic [31:0] in_e_score_correction_bias_2,
    input  logic [31:0] in_e_score_correction_bias_3,
    input  logic [31:0] in_e_score_correction_bias_4,
    input  logic [31:0] in_e_score_correction_bias_5,
    input  logic [31:0] in_e_score_correction_bias_6,
    input  logic [31:0] in_e_score_correction_bias_7,
    input  logic        in_norm_topk_prob,
    input  logic [31:0] in_routed_scaling_factor,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [2:0]  out_topk_indices_0,
    output logic [2:0]  out_topk_indices_1,
    output logic [31:0] out_topk_weights_0,
    output logic [31:0] out_topk_weights_1
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
            fp32_div_bits = fp_round_pack(sign_bit, e, quotient);
        end
    end
endfunction
    function automatic logic [31:0] fp32_is_nan(input logic [31:0] x);
        logic [7:0] expf;
        logic [22:0] fracf;
        begin
            expf = x[30:23];
            fracf = x[22:0];
            if ((expf == 8'hff) && (fracf != 23'h0)) fp32_is_nan = 32'd1;
            else fp32_is_nan = 32'd0;
        end
    endfunction

    function automatic logic signed [1:0] fp32_cmp_bits(input logic [31:0] a, b);
    if ((a[30:0] > 31'h7f800000) || (b[30:0] > 31'h7f800000))
        fp32_cmp_bits = 2'sb10; // -2: unordered, never an ordered <= result.
    else if ((a == b) || ((a[30:0] == 0) && (b[30:0] == 0))) fp32_cmp_bits = 0;
    else if (a[31] != b[31]) fp32_cmp_bits = a[31] ? -2'sd1 : 2'sd1;
    else if ((a[30:0] < b[30:0]) != a[31]) fp32_cmp_bits = -2'sd1;
    else fp32_cmp_bits = 2'sd1;
endfunction
function automatic logic greater(input logic [31:0] ax, bx);
    greater = fp32_cmp_bits(ax,bx) == 2'sd1;
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
    function automatic [31:0] exp2_finite(input logic [31:0] x);
        integer exponent,shift,q,n,j,r;
        logic [31:0] magnitude,mantissa,lo,hi,significand;
        logic [63:0] y;
        logic [7:0] encoded_exp;
        begin
            magnitude=x & 32'h7fffffff; exponent=int'(x[30:23]);
            q=0;n=0;j=0;r=0;shift=0;mantissa=0;lo=0;hi=0;significand=0;y=0;encoded_exp=0;
            if (magnitude>32'h7f800000) exp2_finite=32'h7fc00000;
            else if (magnitude==32'h7f800000) exp2_finite=x[31] ? 0 : 32'h7f800000;
            else if (exponent==0) exp2_finite=32'h3f800000;
            else if (x[31] && magnitude>32'h42fc0000) exp2_finite=0;
            else if (!x[31] && magnitude>=32'h43000000) exp2_finite=32'h7f800000;
            else begin
                if (exponent<111) q=x[31] ? -1 : 0;
                else begin
                    shift=134-exponent;
                    mantissa={8'd0,1'b1,x[22:0]};
                    if (x[31]) q=-int'((mantissa+(32'd1<<shift)-1)>>shift);
                    else q=int'(mantissa>>shift);
                end
                n=q>>>16; j=(q & 65535)>>11; r=q & 2047;
                lo=exp2_node(j);hi=exp2_node(j+1);
                y={32'd0,lo}+(({32'd0,hi}-{32'd0,lo})*r>>11);
                significand=32'(y>>7);
                if (y[6:0]>7'd64 || (y[6:0]==7'd64 && significand[0])) significand=significand+1;
                if (significand==32'h1000000) begin significand=significand>>1;n=n+1;end
                encoded_exp=8'(n+127);
                exp2_finite=n>127 ? 32'h7f800000 : {1'b0,encoded_exp,significand[22:0]};
            end
        end
    endfunction
    function automatic [31:0] exp_finite(input logic [31:0] x);
        exp_finite=exp2_finite(fp32_mul_bits(x,32'h3fb8aa3b));
    endfunction

    function automatic [31:0] sigmoid_contract(input logic [31:0] x);
        logic [31:0] a, denominator;
        begin
            a=exp_finite(x | 32'h80000000);
            denominator=fp32_add_bits(32'h3f800000,a);
            sigmoid_contract=fp32_div_bits(x[31] ? a : 32'h3f800000,denominator);
        end
    endfunction

    function automatic ahead(input logic [31:0] a,b,input integer ia,ib);
        if (fp32_is_nan(a)) ahead=fp32_is_nan(b) && ia<ib;
        else if (fp32_is_nan(b)) ahead=1;
        else if (greater(a,b)) ahead=1;
        else if (greater(b,a)) ahead=0;
        else ahead=ia<ib;
    endfunction
    logic [31:0] gates[0:7], choices[0:7], group_score[0:1];
    logic [31:0] denominator, w0, w1;
    integer idx[0:7], temp, sel0, sel1;
    logic [2:0] next_out_topk_indices_0, next_out_topk_indices_1;
    logic [31:0] next_out_topk_weights_0, next_out_topk_weights_1;
    always_comb begin
        temp=0;
        gates[0]=sigmoid_contract(in_router_logits_0);
        choices[0]=fp32_add_bits(gates[0],in_e_score_correction_bias_0);
        idx[0]=0;
        gates[1]=sigmoid_contract(in_router_logits_1);
        choices[1]=fp32_add_bits(gates[1],in_e_score_correction_bias_1);
        idx[1]=1;
        gates[2]=sigmoid_contract(in_router_logits_2);
        choices[2]=fp32_add_bits(gates[2],in_e_score_correction_bias_2);
        idx[2]=2;
        gates[3]=sigmoid_contract(in_router_logits_3);
        choices[3]=fp32_add_bits(gates[3],in_e_score_correction_bias_3);
        idx[3]=3;
        gates[4]=sigmoid_contract(in_router_logits_4);
        choices[4]=fp32_add_bits(gates[4],in_e_score_correction_bias_4);
        idx[4]=4;
        gates[5]=sigmoid_contract(in_router_logits_5);
        choices[5]=fp32_add_bits(gates[5],in_e_score_correction_bias_5);
        idx[5]=5;
        gates[6]=sigmoid_contract(in_router_logits_6);
        choices[6]=fp32_add_bits(gates[6],in_e_score_correction_bias_6);
        idx[6]=6;
        gates[7]=sigmoid_contract(in_router_logits_7);
        choices[7]=fp32_add_bits(gates[7],in_e_score_correction_bias_7);
        idx[7]=7;
        for (int g=0;g<2;g++) begin
            for (int i=0;i<3;i++) begin
                for (int j=i+1;j<4;j++) begin
                    if (ahead(choices[idx[4*g+j]],choices[idx[4*g+i]],idx[4*g+j],idx[4*g+i])) begin
                        temp=idx[4*g+i]; idx[4*g+i]=idx[4*g+j]; idx[4*g+j]=temp;
                    end
                end
            end
            group_score[g]=fp32_add_bits(choices[idx[4*g]],choices[idx[4*g+1]]);
        end
        if (ahead(group_score[0],group_score[1],0,1)) begin sel0=idx[0];sel1=idx[1];end
        else begin sel0=idx[4];sel1=idx[5];end
        denominator=fp32_add_bits(fp32_add_bits(gates[sel0],gates[sel1]),32'h1e3ce508);
        w0=in_norm_topk_prob ? fp32_div_bits(gates[sel0],denominator) : gates[sel0];
        w1=in_norm_topk_prob ? fp32_div_bits(gates[sel1],denominator) : gates[sel1];
        next_out_topk_indices_0=3'(sel0); next_out_topk_indices_1=3'(sel1);
        next_out_topk_weights_0=fp32_mul_bits(w0,in_routed_scaling_factor);
        next_out_topk_weights_1=fp32_mul_bits(w1,in_routed_scaling_factor);
    end

    assign in_ready = !out_valid || out_ready;
    always_ff @(posedge clk) begin
        if (rst) begin out_valid <= 0; out_topk_indices_0 <= 0; out_topk_indices_1 <= 0; out_topk_weights_0 <= 0; out_topk_weights_1 <= 0; end
        else if (in_ready) begin
            out_valid <= in_valid;
            if (in_valid) begin out_topk_indices_0 <= next_out_topk_indices_0; out_topk_indices_1 <= next_out_topk_indices_1; out_topk_weights_0 <= next_out_topk_weights_0; out_topk_weights_1 <= next_out_topk_weights_1; end
            else begin out_topk_indices_0 <= 0; out_topk_indices_1 <= 0; out_topk_weights_0 <= 0; out_topk_weights_1 <= 0; end
        end
    end
endmodule
