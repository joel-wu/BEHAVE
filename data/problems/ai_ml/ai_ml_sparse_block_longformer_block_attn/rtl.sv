module LongformerBlockAttention (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic [511:0] in_query,
    input logic [511:0] in_key,
    input logic [511:0] in_value,
    input logic [511:0] in_global_query,
    input logic [511:0] in_global_key,
    input logic [511:0] in_global_value,
    input logic [7:0] in_masked,
    input logic [7:0] in_global,
    output logic out_valid,
    input logic out_ready,
    output logic [511:0] out_attn_out
);
    typedef enum logic [1:0] {IDLE,SCORE,WEIGHT,MIX} phase_t;
    phase_t phase;
    logic [2:0] query_index;
    logic [511:0] queries, keys, values, global_queries, global_keys, global_values;
    logic [7:0] masked, globals, allowed;
    logic signed [63:0] scores [0:7];
    logic [16:0] weights [0:7];
    logic signed [63:0] peak;
    logic [19:0] denominator;
    integer j, c;
    logic eligible, found;
    logic signed [15:0] q_lane, k_lane, v_lane;
    logic signed [63:0] total, score_value, largest, grid, numerator, answer;
    logic [16:0] w;
    logic [19:0] weight_sum;

    function automatic logic signed [63:0] floor_div(
        input logic signed [63:0] a, input logic signed [63:0] b);
        floor_div = a>=0 ? a/b : -((-a+b-1)/b);
    endfunction

    function automatic logic [16:0] exp_word(input logic signed [63:0] index);
        begin
            case (index)
                64'sd0: exp_word=17'd65536;
                64'sd1: exp_word=17'd61565;
                64'sd2: exp_word=17'd57835;
                64'sd3: exp_word=17'd54331;
                64'sd4: exp_word=17'd51039;
                64'sd5: exp_word=17'd47947;
                64'sd6: exp_word=17'd45042;
                64'sd7: exp_word=17'd42313;
                64'sd8: exp_word=17'd39750;
                64'sd9: exp_word=17'd37341;
                64'sd10: exp_word=17'd35079;
                64'sd11: exp_word=17'd32954;
                64'sd12: exp_word=17'd30957;
                64'sd13: exp_word=17'd29081;
                64'sd14: exp_word=17'd27319;
                64'sd15: exp_word=17'd25664;
                64'sd16: exp_word=17'd24109;
                64'sd17: exp_word=17'd22649;
                64'sd18: exp_word=17'd21276;
                64'sd19: exp_word=17'd19987;
                64'sd20: exp_word=17'd18776;
                64'sd21: exp_word=17'd17639;
                64'sd22: exp_word=17'd16570;
                64'sd23: exp_word=17'd15566;
                64'sd24: exp_word=17'd14623;
                64'sd25: exp_word=17'd13737;
                64'sd26: exp_word=17'd12905;
                64'sd27: exp_word=17'd12123;
                64'sd28: exp_word=17'd11388;
                64'sd29: exp_word=17'd10698;
                64'sd30: exp_word=17'd10050;
                64'sd31: exp_word=17'd9441;
                64'sd32: exp_word=17'd8869;
                64'sd33: exp_word=17'd8332;
                64'sd34: exp_word=17'd7827;
                64'sd35: exp_word=17'd7353;
                64'sd36: exp_word=17'd6907;
                64'sd37: exp_word=17'd6489;
                64'sd38: exp_word=17'd6096;
                64'sd39: exp_word=17'd5726;
                64'sd40: exp_word=17'd5380;
                64'sd41: exp_word=17'd5054;
                64'sd42: exp_word=17'd4747;
                64'sd43: exp_word=17'd4460;
                64'sd44: exp_word=17'd4190;
                64'sd45: exp_word=17'd3936;
                64'sd46: exp_word=17'd3697;
                64'sd47: exp_word=17'd3473;
                64'sd48: exp_word=17'd3263;
                64'sd49: exp_word=17'd3065;
                64'sd50: exp_word=17'd2879;
                64'sd51: exp_word=17'd2705;
                64'sd52: exp_word=17'd2541;
                64'sd53: exp_word=17'd2387;
                64'sd54: exp_word=17'd2243;
                64'sd55: exp_word=17'd2107;
                64'sd56: exp_word=17'd1979;
                64'sd57: exp_word=17'd1859;
                64'sd58: exp_word=17'd1746;
                64'sd59: exp_word=17'd1641;
                64'sd60: exp_word=17'd1541;
                64'sd61: exp_word=17'd1448;
                64'sd62: exp_word=17'd1360;
                64'sd63: exp_word=17'd1278;
                64'sd64: exp_word=17'd1200;
                64'sd65: exp_word=17'd1128;
                64'sd66: exp_word=17'd1059;
                64'sd67: exp_word=17'd995;
                64'sd68: exp_word=17'd935;
                64'sd69: exp_word=17'd878;
                64'sd70: exp_word=17'd825;
                64'sd71: exp_word=17'd775;
                64'sd72: exp_word=17'd728;
                64'sd73: exp_word=17'd684;
                64'sd74: exp_word=17'd642;
                64'sd75: exp_word=17'd604;
                64'sd76: exp_word=17'd567;
                64'sd77: exp_word=17'd533;
                64'sd78: exp_word=17'd500;
                64'sd79: exp_word=17'd470;
                64'sd80: exp_word=17'd442;
                64'sd81: exp_word=17'd415;
                64'sd82: exp_word=17'd390;
                64'sd83: exp_word=17'd366;
                64'sd84: exp_word=17'd344;
                64'sd85: exp_word=17'd323;
                64'sd86: exp_word=17'd303;
                64'sd87: exp_word=17'd285;
                64'sd88: exp_word=17'd268;
                64'sd89: exp_word=17'd252;
                64'sd90: exp_word=17'd236;
                64'sd91: exp_word=17'd222;
                64'sd92: exp_word=17'd209;
                64'sd93: exp_word=17'd196;
                64'sd94: exp_word=17'd184;
                64'sd95: exp_word=17'd173;
                64'sd96: exp_word=17'd162;
                64'sd97: exp_word=17'd153;
                64'sd98: exp_word=17'd143;
                64'sd99: exp_word=17'd135;
                64'sd100: exp_word=17'd127;
                64'sd101: exp_word=17'd119;
                64'sd102: exp_word=17'd112;
                64'sd103: exp_word=17'd105;
                64'sd104: exp_word=17'd99;
                64'sd105: exp_word=17'd93;
                64'sd106: exp_word=17'd87;
                64'sd107: exp_word=17'd82;
                64'sd108: exp_word=17'd77;
                64'sd109: exp_word=17'd72;
                64'sd110: exp_word=17'd68;
                64'sd111: exp_word=17'd64;
                64'sd112: exp_word=17'd60;
                64'sd113: exp_word=17'd56;
                64'sd114: exp_word=17'd53;
                64'sd115: exp_word=17'd50;
                64'sd116: exp_word=17'd47;
                64'sd117: exp_word=17'd44;
                64'sd118: exp_word=17'd41;
                64'sd119: exp_word=17'd39;
                64'sd120: exp_word=17'd36;
                64'sd121: exp_word=17'd34;
                64'sd122: exp_word=17'd32;
                64'sd123: exp_word=17'd30;
                64'sd124: exp_word=17'd28;
                64'sd125: exp_word=17'd27;
                64'sd126: exp_word=17'd25;
                64'sd127: exp_word=17'd23;
                64'sd128: exp_word=17'd22;

                default: exp_word=0;
            endcase
        end
    endfunction

    assign in_ready = !rst && phase==IDLE && (!out_valid || out_ready);

    always_ff @(posedge clk) begin
        if (rst) begin
            phase<=IDLE; query_index<=0; out_valid<=0; out_attn_out<=0;
            queries<=0; keys<=0; values<=0; global_queries<=0; global_keys<=0; global_values<=0;
            masked<=0; globals<=0; allowed<=0; peak<=0; denominator<=0;
            for (j=0;j<8;j=j+1) begin scores[j]<=0; weights[j]<=0; end
        end else begin
            if (out_valid && out_ready) out_valid<=0;
            case (phase)
                IDLE: if (in_valid && in_ready) begin
                    queries<=in_query; keys<=in_key; values<=in_value;
                    global_queries<=in_global_query; global_keys<=in_global_key; global_values<=in_global_value;
                    masked<=in_masked; globals<=in_global;
                    query_index<=0; phase<=SCORE;
                end
                SCORE: begin
                    largest=0; found=0;
                    for (j=0;j<8;j=j+1) begin
                        eligible=!masked[query_index] && !masked[j] &&
                            (globals[query_index] || globals[j] ||
                             (j+2>=query_index && j<=query_index+2));
                        allowed[j]<=eligible;
                        total=0;
                        if (eligible) begin
                            for (c=0;c<4;c=c+1) begin
                                q_lane=globals[query_index] ? $signed(global_queries[16*(4*query_index+c) +: 16]) : $signed(queries[16*(4*query_index+c) +: 16]);
                                k_lane=globals[query_index] ? $signed(global_keys[16*(4*j+c) +: 16]) : $signed(keys[16*(4*j+c) +: 16]);
                                total=total+q_lane*k_lane;
                            end
                            score_value=floor_div(total,64'sd512);
                            scores[j]<=score_value;
                            if (!found || score_value>largest) largest=score_value;
                            found=1;
                        end else scores[j]<=0;
                    end
                    peak<=largest; phase<=WEIGHT;
                end
                WEIGHT: begin
                    weight_sum=0;
                    for (j=0;j<8;j=j+1) begin
                        grid=(peak-scores[j]+8)/16;
                        w=allowed[j] ? exp_word(grid) : 0;
                        weights[j]<=w;
                        weight_sum=weight_sum+w;
                    end
                    denominator<=weight_sum; phase<=MIX;
                end
                MIX: begin
                    for (c=0;c<4;c=c+1) begin
                        numerator=0;
                        for (j=0;j<8;j=j+1) begin
                            v_lane=globals[query_index] ? $signed(global_values[16*(4*j+c) +: 16]) : $signed(values[16*(4*j+c) +: 16]);
                            numerator=numerator+$signed({1'b0,weights[j]})*v_lane;
                        end
                        answer=denominator==0 ? 0 : floor_div(numerator,$signed({44'd0,denominator}));
                        out_attn_out[16*(4*query_index+c) +: 16]<=answer[15:0];
                    end
                    if (query_index==7) begin phase<=IDLE; out_valid<=1; query_index<=0; end
                    else begin query_index<=query_index+1; phase<=SCORE; end
                end
            endcase
        end
    end
endmodule
