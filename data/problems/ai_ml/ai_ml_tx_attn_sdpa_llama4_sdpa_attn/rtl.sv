module ai_ml_tx_attn_sdpa_llama4_sdpa_attn (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic [127:0] in_hidden_states,
    input logic [1023:0] in_q_weight,
    input logic [127:0] in_q_bias,
    input logic [511:0] in_k_weight,
    input logic [63:0] in_k_bias,
    input logic [511:0] in_v_weight,
    input logic [63:0] in_v_bias,
    input logic [1023:0] in_proj_weight,
    input logic [127:0] in_proj_bias,
    input logic [0:0] in_attention_mask_valid,
    input logic [7:0] in_attention_mask,
    input logic [127:0] in_attention_bias,
    output logic out_valid,
    input logic out_ready,
    output logic [127:0] out_attn_output
);
    logic [1:0] phase;
    integer length, head;
    logic [127:0] saved_hidden_states;
    logic [1023:0] saved_q_weight;
    logic [127:0] saved_q_bias;
    logic [511:0] saved_k_weight;
    logic [63:0] saved_k_bias;
    logic [511:0] saved_v_weight;
    logic [63:0] saved_v_bias;
    logic [1023:0] saved_proj_weight;
    logic [127:0] saved_proj_bias;
    logic [0:0] saved_attention_mask_valid;
    logic [7:0] saved_attention_mask;
    logic [127:0] saved_attention_bias;

    logic signed [15:0] queries[0:7], keys[0:31], vals[0:31], attended[0:7];
    integer i,j,d,position,index;
    reg signed [63:0] aq,ak,av,acc,maximum,total;
    reg signed [63:0] scores[0:7],weights[0:7];
    function automatic signed [63:0] lane(input logic [1023:0] word,input integer index);
        lane={{48{word[16*index+15]}},word[16*index+:16]};
    endfunction
    function automatic signed [15:0] sat(input signed [63:0] value);
        if(value>32767) sat=16'sh7fff;
        else if(value < -32768) sat=16'sh8000;
        else sat=value[15:0];
    endfunction
    function automatic signed [63:0] floor_div(input signed [63:0] a,b);
        floor_div=a/b;
        if(a<0 && a%b!=0) floor_div=floor_div-1;
    endfunction
    function automatic signed [63:0] exp_weight(input integer index);
        case(index)
            0:exp_weight=64'd65536;
            1:exp_weight=64'd61565;
            2:exp_weight=64'd57835;
            3:exp_weight=64'd54331;
            4:exp_weight=64'd51039;
            5:exp_weight=64'd47947;
            6:exp_weight=64'd45042;
            7:exp_weight=64'd42313;
            8:exp_weight=64'd39750;
            9:exp_weight=64'd37341;
            10:exp_weight=64'd35079;
            11:exp_weight=64'd32954;
            12:exp_weight=64'd30957;
            13:exp_weight=64'd29081;
            14:exp_weight=64'd27319;
            15:exp_weight=64'd25664;
            16:exp_weight=64'd24109;
            17:exp_weight=64'd22649;
            18:exp_weight=64'd21276;
            19:exp_weight=64'd19987;
            20:exp_weight=64'd18776;
            21:exp_weight=64'd17639;
            22:exp_weight=64'd16570;
            23:exp_weight=64'd15566;
            24:exp_weight=64'd14623;
            25:exp_weight=64'd13737;
            26:exp_weight=64'd12905;
            27:exp_weight=64'd12123;
            28:exp_weight=64'd11388;
            29:exp_weight=64'd10698;
            30:exp_weight=64'd10050;
            31:exp_weight=64'd9441;
            32:exp_weight=64'd8869;
            33:exp_weight=64'd8332;
            34:exp_weight=64'd7827;
            35:exp_weight=64'd7353;
            36:exp_weight=64'd6907;
            37:exp_weight=64'd6489;
            38:exp_weight=64'd6096;
            39:exp_weight=64'd5726;
            40:exp_weight=64'd5380;
            41:exp_weight=64'd5054;
            42:exp_weight=64'd4747;
            43:exp_weight=64'd4460;
            44:exp_weight=64'd4190;
            45:exp_weight=64'd3936;
            46:exp_weight=64'd3697;
            47:exp_weight=64'd3473;
            48:exp_weight=64'd3263;
            49:exp_weight=64'd3065;
            50:exp_weight=64'd2879;
            51:exp_weight=64'd2705;
            52:exp_weight=64'd2541;
            53:exp_weight=64'd2387;
            54:exp_weight=64'd2243;
            55:exp_weight=64'd2107;
            56:exp_weight=64'd1979;
            57:exp_weight=64'd1859;
            58:exp_weight=64'd1746;
            59:exp_weight=64'd1641;
            60:exp_weight=64'd1541;
            61:exp_weight=64'd1448;
            62:exp_weight=64'd1360;
            63:exp_weight=64'd1278;
            64:exp_weight=64'd1200;
            65:exp_weight=64'd1128;
            66:exp_weight=64'd1059;
            67:exp_weight=64'd995;
            68:exp_weight=64'd935;
            69:exp_weight=64'd878;
            70:exp_weight=64'd825;
            71:exp_weight=64'd775;
            72:exp_weight=64'd728;
            73:exp_weight=64'd684;
            74:exp_weight=64'd642;
            75:exp_weight=64'd604;
            76:exp_weight=64'd567;
            77:exp_weight=64'd533;
            78:exp_weight=64'd500;
            79:exp_weight=64'd470;
            80:exp_weight=64'd442;
            81:exp_weight=64'd415;
            82:exp_weight=64'd390;
            83:exp_weight=64'd366;
            84:exp_weight=64'd344;
            85:exp_weight=64'd323;
            86:exp_weight=64'd303;
            87:exp_weight=64'd285;
            88:exp_weight=64'd268;
            89:exp_weight=64'd252;
            90:exp_weight=64'd236;
            91:exp_weight=64'd222;
            92:exp_weight=64'd209;
            93:exp_weight=64'd196;
            94:exp_weight=64'd184;
            95:exp_weight=64'd173;
            96:exp_weight=64'd162;
            97:exp_weight=64'd153;
            98:exp_weight=64'd143;
            99:exp_weight=64'd135;
            100:exp_weight=64'd127;
            101:exp_weight=64'd119;
            102:exp_weight=64'd112;
            103:exp_weight=64'd105;
            104:exp_weight=64'd99;
            105:exp_weight=64'd93;
            106:exp_weight=64'd87;
            107:exp_weight=64'd82;
            108:exp_weight=64'd77;
            109:exp_weight=64'd72;
            110:exp_weight=64'd68;
            111:exp_weight=64'd64;
            112:exp_weight=64'd60;
            113:exp_weight=64'd56;
            114:exp_weight=64'd53;
            115:exp_weight=64'd50;
            116:exp_weight=64'd47;
            117:exp_weight=64'd44;
            118:exp_weight=64'd41;
            119:exp_weight=64'd39;
            120:exp_weight=64'd36;
            121:exp_weight=64'd34;
            122:exp_weight=64'd32;
            123:exp_weight=64'd30;
            124:exp_weight=64'd28;
            125:exp_weight=64'd27;
            126:exp_weight=64'd25;
            127:exp_weight=64'd23;
            128:exp_weight=64'd22;
            default:exp_weight=0;
        endcase
    endfunction
    assign in_ready=(phase==0) && (!out_valid || out_ready);
    always_ff @(posedge clk) begin
        if(rst) begin
            phase<=0;length<=0;head<=0;out_valid<=0;out_attn_output<=0;
            for(i=0;i<32;i=i+1) begin keys[i]<=0; vals[i]<=0; end
        end else begin
            if(out_valid && out_ready) out_valid<=0;
            case(phase)
                0:if(in_valid && in_ready) begin
                    saved_hidden_states<=in_hidden_states;
                    saved_q_weight<=in_q_weight;
                    saved_q_bias<=in_q_bias;
                    saved_k_weight<=in_k_weight;
                    saved_k_bias<=in_k_bias;
                    saved_v_weight<=in_v_weight;
                    saved_v_bias<=in_v_bias;
                    saved_proj_weight<=in_proj_weight;
                    saved_proj_bias<=in_proj_bias;
                    saved_attention_mask_valid<=in_attention_mask_valid;
                    saved_attention_mask<=in_attention_mask;
                    saved_attention_bias<=in_attention_bias;
                    phase<=1;
                end
                1:begin
                    position=length;
                    if(length==8) begin
                        position=7;
                        for(i=0;i<28;i=i+1) begin keys[i]<=keys[i+4];vals[i]<=vals[i+4];end
                    end else length<=length+1;
                    for(i=0;i<8;i=i+1) begin
                        aq=lane(saved_q_bias,i)*256;
                        for(d=0;d<8;d=d+1) aq=aq+lane(saved_hidden_states,d)*lane(saved_q_weight,i*8+d);
                        queries[i]<=sat(aq >>> 8);
                        if(i<4) begin
                            ak=lane(saved_k_bias,i)*256;av=lane(saved_v_bias,i)*256;
                            for(d=0;d<8;d=d+1) begin
                                ak=ak+lane(saved_hidden_states,d)*lane(saved_k_weight,i*8+d);
                                av=av+lane(saved_hidden_states,d)*lane(saved_v_weight,i*8+d);
                            end
                            keys[position*4+i]<=sat(ak >>> 8);
                            vals[position*4+i]<=sat(av >>> 8);
                        end
                    end
                    head<=0;phase<=2;
                end
                2:begin
                    maximum=-1000000000;
                    for(j=0;j<8;j=j+1) begin
                        scores[j]=0;
                        if(j<length) begin
                            acc=0;
                            for(d=0;d<4;d=d+1) acc=acc+$signed(queries[head*4+d])*$signed(keys[j*4+d]);
                            scores[j]=(acc >>> 9)+(saved_attention_mask_valid ? lane(saved_attention_bias,j) : 64'sd0);
                            if((!saved_attention_mask_valid || !saved_attention_mask[j]) && scores[j]>maximum)
                                maximum=scores[j];
                        end
                    end
                    total=0;
                    for(j=0;j<8;j=j+1) begin
                        weights[j]=0;
                        if(j<length && (!saved_attention_mask_valid || !saved_attention_mask[j])) begin
                            index=(maximum-scores[j]+8)/16;
                            weights[j]=exp_weight(index);
                        end
                        total=total+weights[j];
                    end
                    for(d=0;d<4;d=d+1) begin
                        acc=0;
                        for(j=0;j<8;j=j+1) acc=acc+weights[j]*$signed(vals[j*4+d]);
                        attended[head*4+d]<=total==0 ? 0 : floor_div(acc,total);
                    end
                    if(head==0) head<=1;
                    else phase<=3;
                end
                3:begin
                    for(i=0;i<8;i=i+1) begin
                        acc=lane(saved_proj_bias,i)*256;
                        for(d=0;d<8;d=d+1) acc=acc+$signed(attended[d])*lane(saved_proj_weight,i*8+d);
                        out_attn_output[16*i+:16]<=sat(acc >>> 8);
                    end
                    phase<=0;out_valid<=1;
                end
            endcase
        end
    end
endmodule
