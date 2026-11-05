module ai_ml_sample_filter_sample_topk_topp_combined (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [31:0]  in_score0,
    input  logic [31:0]  in_score1,
    input  logic [31:0]  in_score2,
    input  logic [31:0]  in_score3,
    input  logic [31:0]  in_score4,
    input  logic [31:0]  in_score5,
    input  logic [31:0]  in_score6,
    input  logic [31:0]  in_score7,
    input  logic [31:0]  in_temperature,
    input  logic [3:0]   in_top_k,
    input  logic [31:0]  in_top_p,
    input  logic [3:0]   in_min_tokens_to_keep_k,
    input  logic [3:0]   in_min_tokens_to_keep_p,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_score0,
    output logic [31:0]  out_score1,
    output logic [31:0]  out_score2,
    output logic [31:0]  out_score3,
    output logic [31:0]  out_score4,
    output logic [31:0]  out_score5,
    output logic [31:0]  out_score6,
    output logic [31:0]  out_score7,
    output logic [2:0]   out_token,
    output logic out_error
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
localparam logic [31:0] NEG_INF=32'hff800000, POS_INF=32'h7f800000, FP32_QNAN=32'h7fc00000;
logic busy;
assign in_ready = !busy || out_ready;
assign out_valid = busy;
function automatic logic is_nan32(input logic [31:0] x);
    is_nan32 = (x[30:23] == 8'hFF) && (x[22:0] != 23'd0);
endfunction

//   – RNE rounding on the 24-bit mantissa

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
function automatic signed [135:0] grid(input logic [31:0] x);
    reg [135:0] code;
    integer shift;
    begin
        code={1'b1,x[22:0]};
        shift=integer'(x[30:23])-146;
        if (x[30:23]==0 || shift < -24) code=0;
        else if (shift>=0) code=code<<shift;
        else code=(code+(136'd1<<(-shift-1)))>>(-shift);
        grid=x[31] ? -$signed(code) : $signed(code);
    end
endfunction
function automatic [19:0] exp_weight(input signed [135:0] d);
    case (d)
        136'd0: exp_weight=20'd65536;
        136'd1: exp_weight=20'd61565;
        136'd2: exp_weight=20'd57835;
        136'd3: exp_weight=20'd54331;
        136'd4: exp_weight=20'd51039;
        136'd5: exp_weight=20'd47947;
        136'd6: exp_weight=20'd45042;
        136'd7: exp_weight=20'd42313;
        136'd8: exp_weight=20'd39750;
        136'd9: exp_weight=20'd37341;
        136'd10: exp_weight=20'd35079;
        136'd11: exp_weight=20'd32954;
        136'd12: exp_weight=20'd30957;
        136'd13: exp_weight=20'd29081;
        136'd14: exp_weight=20'd27319;
        136'd15: exp_weight=20'd25664;
        136'd16: exp_weight=20'd24109;
        136'd17: exp_weight=20'd22649;
        136'd18: exp_weight=20'd21276;
        136'd19: exp_weight=20'd19987;
        136'd20: exp_weight=20'd18776;
        136'd21: exp_weight=20'd17639;
        136'd22: exp_weight=20'd16570;
        136'd23: exp_weight=20'd15566;
        136'd24: exp_weight=20'd14623;
        136'd25: exp_weight=20'd13737;
        136'd26: exp_weight=20'd12905;
        136'd27: exp_weight=20'd12123;
        136'd28: exp_weight=20'd11388;
        136'd29: exp_weight=20'd10698;
        136'd30: exp_weight=20'd10050;
        136'd31: exp_weight=20'd9441;
        136'd32: exp_weight=20'd8869;
        136'd33: exp_weight=20'd8332;
        136'd34: exp_weight=20'd7827;
        136'd35: exp_weight=20'd7353;
        136'd36: exp_weight=20'd6907;
        136'd37: exp_weight=20'd6489;
        136'd38: exp_weight=20'd6096;
        136'd39: exp_weight=20'd5726;
        136'd40: exp_weight=20'd5380;
        136'd41: exp_weight=20'd5054;
        136'd42: exp_weight=20'd4747;
        136'd43: exp_weight=20'd4460;
        136'd44: exp_weight=20'd4190;
        136'd45: exp_weight=20'd3936;
        136'd46: exp_weight=20'd3697;
        136'd47: exp_weight=20'd3473;
        136'd48: exp_weight=20'd3263;
        136'd49: exp_weight=20'd3065;
        136'd50: exp_weight=20'd2879;
        136'd51: exp_weight=20'd2705;
        136'd52: exp_weight=20'd2541;
        136'd53: exp_weight=20'd2387;
        136'd54: exp_weight=20'd2243;
        136'd55: exp_weight=20'd2107;
        136'd56: exp_weight=20'd1979;
        136'd57: exp_weight=20'd1859;
        136'd58: exp_weight=20'd1746;
        136'd59: exp_weight=20'd1641;
        136'd60: exp_weight=20'd1541;
        136'd61: exp_weight=20'd1448;
        136'd62: exp_weight=20'd1360;
        136'd63: exp_weight=20'd1278;
        136'd64: exp_weight=20'd1200;
        136'd65: exp_weight=20'd1128;
        136'd66: exp_weight=20'd1059;
        136'd67: exp_weight=20'd995;
        136'd68: exp_weight=20'd935;
        136'd69: exp_weight=20'd878;
        136'd70: exp_weight=20'd825;
        136'd71: exp_weight=20'd775;
        136'd72: exp_weight=20'd728;
        136'd73: exp_weight=20'd684;
        136'd74: exp_weight=20'd642;
        136'd75: exp_weight=20'd604;
        136'd76: exp_weight=20'd567;
        136'd77: exp_weight=20'd533;
        136'd78: exp_weight=20'd500;
        136'd79: exp_weight=20'd470;
        136'd80: exp_weight=20'd442;
        136'd81: exp_weight=20'd415;
        136'd82: exp_weight=20'd390;
        136'd83: exp_weight=20'd366;
        136'd84: exp_weight=20'd344;
        136'd85: exp_weight=20'd323;
        136'd86: exp_weight=20'd303;
        136'd87: exp_weight=20'd285;
        136'd88: exp_weight=20'd268;
        136'd89: exp_weight=20'd252;
        136'd90: exp_weight=20'd236;
        136'd91: exp_weight=20'd222;
        136'd92: exp_weight=20'd209;
        136'd93: exp_weight=20'd196;
        136'd94: exp_weight=20'd184;
        136'd95: exp_weight=20'd173;
        136'd96: exp_weight=20'd162;
        136'd97: exp_weight=20'd153;
        136'd98: exp_weight=20'd143;
        136'd99: exp_weight=20'd135;
        136'd100: exp_weight=20'd127;
        136'd101: exp_weight=20'd119;
        136'd102: exp_weight=20'd112;
        136'd103: exp_weight=20'd105;
        136'd104: exp_weight=20'd99;
        136'd105: exp_weight=20'd93;
        136'd106: exp_weight=20'd87;
        136'd107: exp_weight=20'd82;
        136'd108: exp_weight=20'd77;
        136'd109: exp_weight=20'd72;
        136'd110: exp_weight=20'd68;
        136'd111: exp_weight=20'd64;
        136'd112: exp_weight=20'd60;
        136'd113: exp_weight=20'd56;
        136'd114: exp_weight=20'd53;
        136'd115: exp_weight=20'd50;
        136'd116: exp_weight=20'd47;
        136'd117: exp_weight=20'd44;
        136'd118: exp_weight=20'd41;
        136'd119: exp_weight=20'd39;
        136'd120: exp_weight=20'd36;
        136'd121: exp_weight=20'd34;
        136'd122: exp_weight=20'd32;
        136'd123: exp_weight=20'd30;
        136'd124: exp_weight=20'd28;
        136'd125: exp_weight=20'd27;
        136'd126: exp_weight=20'd25;
        136'd127: exp_weight=20'd23;
        136'd128: exp_weight=20'd22;
        default: exp_weight=0;
    endcase
endfunction
reg [31:0] scaled[0:7], filtered[0:7], source_scores[0:7];
reg [19:0] weights[0:7], total, cumulative;
reg [191:0] denominator, numerator;
reg invalid;
integer order[0:7];
integer i, j, last, temporary, count, minimum, token, te, pe;
always_ff @(posedge clk) begin
    if (rst) begin
        busy<=0;
        out_error<=0;
        out_token<=0;
        out_score0<=0;
        out_score1<=0;
        out_score2<=0;
        out_score3<=0;
        out_score4<=0;
        out_score5<=0;
        out_score6<=0;
        out_score7<=0;
    end else if (in_valid && in_ready) begin
        busy<=1;
        source_scores[0]=in_score0;
        source_scores[1]=in_score1;
        source_scores[2]=in_score2;
        source_scores[3]=in_score3;
        source_scores[4]=in_score4;
        source_scores[5]=in_score5;
        source_scores[6]=in_score6;
        source_scores[7]=in_score7;
        te=in_temperature[30:23]; pe=in_top_p[30:23];
        invalid=in_temperature[31] || te==0 || te==255 || pe==255
            || (in_top_p[31] && in_top_p[30:0]!=0)
            || (!in_top_p[31] && in_top_p>32'h3f800000);
        token=0;
        for (i=0;i<8;i=i+1) begin
            filtered[i]=NEG_INF;
            scaled[i]=fp32_div_bits(source_scores[i],in_temperature);
            if (is_nan32(scaled[i])) scaled[i]=NEG_INF;
            order[i]=i;
        end
        if (!invalid) begin
            for (last=7;last>0;last=last-1)
                for (j=0;j<last;j=j+1)
                    if (greater(scaled[order[j]],scaled[order[j+1]])) begin
                        temporary=order[j];order[j]=order[j+1];order[j+1]=temporary;
                    end
            count=in_top_k;
            if (count<in_min_tokens_to_keep_k) count=in_min_tokens_to_keep_k;
            if (count<1) count=1;
            if (count>8) count=8;
            total=0;
            for (i=0;i<8;i=i+1) begin
                if (!greater(scaled[order[8-count]],scaled[i])) filtered[i]=scaled[i];
                weights[i]=0;
                if (scaled[order[7]]==POS_INF) begin
                    if (filtered[i]==POS_INF) weights[i]=65536;
                end else if (filtered[i]!=NEG_INF)
                    weights[i]=exp_weight(grid(scaled[order[7]])-grid(filtered[i]));
                total=total+weights[i];
            end
            denominator=192'd1<<(pe==0 ? 149 : 150-pe);
            numerator=in_top_p[22:0];
            if (pe!=0) numerator=numerator | 192'h800000;
            minimum=in_min_tokens_to_keep_p;
            if (minimum<1) minimum=1;
            if (minimum>8) minimum=8;
            cumulative=0;
            for (j=0;j<8;j=j+1) begin
                i=order[j]; cumulative=cumulative+weights[i];
                if (j<8-minimum && cumulative*denominator<=total*(denominator-numerator))
                    filtered[i]=NEG_INF;
            end
            for (i=1;i<8;i=i+1) if (greater(filtered[i],filtered[token])) token=i;
        end
        out_error<=invalid;
        out_token<=token[2:0];
        out_score0<=filtered[0];
        out_score1<=filtered[1];
        out_score2<=filtered[2];
        out_score3<=filtered[3];
        out_score4<=filtered[4];
        out_score5<=filtered[5];
        out_score6<=filtered[6];
        out_score7<=filtered[7];
    end else if (out_ready) busy<=0;
end
endmodule
