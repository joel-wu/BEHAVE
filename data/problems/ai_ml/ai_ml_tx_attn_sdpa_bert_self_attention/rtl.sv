module ai_ml_tx_attn_sdpa_bert_self_attention (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [255:0] in_hidden_states,
    input  logic [63:0]  in_attention_mask,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [255:0] out_attn_output
);
    function automatic [31:0] neg_exp2_node(input integer index);
        case (index)
            0: neg_exp2_node = 32'd1073741824;
            1: neg_exp2_node = 32'd1050733751;
            2: neg_exp2_node = 32'd1028218693;
            3: neg_exp2_node = 32'd1006186087;
            4: neg_exp2_node = 32'd984625594;
            5: neg_exp2_node = 32'd963527098;
            6: neg_exp2_node = 32'd942880699;
            7: neg_exp2_node = 32'd922676710;
            8: neg_exp2_node = 32'd902905651;
            9: neg_exp2_node = 32'd883558244;
            10: neg_exp2_node = 32'd864625413;
            11: neg_exp2_node = 32'd846098274;
            12: neg_exp2_node = 32'd827968132;
            13: neg_exp2_node = 32'd810226483;
            14: neg_exp2_node = 32'd792865000;
            15: neg_exp2_node = 32'd775875538;
            16: neg_exp2_node = 32'd759250125;
            17: neg_exp2_node = 32'd742980960;
            18: neg_exp2_node = 32'd727060411;
            19: neg_exp2_node = 32'd711481005;
            20: neg_exp2_node = 32'd696235434;
            21: neg_exp2_node = 32'd681316545;
            22: neg_exp2_node = 32'd666717336;
            23: neg_exp2_node = 32'd652430958;
            24: neg_exp2_node = 32'd638450708;
            25: neg_exp2_node = 32'd624770026;
            26: neg_exp2_node = 32'd611382493;
            27: neg_exp2_node = 32'd598281827;
            28: neg_exp2_node = 32'd585461881;
            29: neg_exp2_node = 32'd572916640;
            30: neg_exp2_node = 32'd560640218;
            31: neg_exp2_node = 32'd548626854;
            32: neg_exp2_node = 32'd536870912;
            default: neg_exp2_node=32'd536870912;
        endcase
    endfunction
    function automatic signed [127:0] exp_weight(input logic signed [127:0] distance);
        logic [63:0] z, a, b, numerator;
        integer j, r, n;
        begin
            z=0; a=0; b=0; numerator=0; j=0; r=0; n=0;
            if (distance>=128'sd1048576) exp_weight=0;
            else begin
                z=64'(distance)*64'd94548/64'd65536;
                n=int'(z>>16); j=int'((z & 65535)>>11); r=int'(z & 2047);
                a=64'(neg_exp2_node(j)); b=64'(neg_exp2_node(j+1));
                numerator=a-(a-b)*64'(r)/64'd2048;
                exp_weight=128'(numerator>>n);
            end
        end
    endfunction
    function automatic [63:0] sqrt_scale(input integer dimension);
        logic [63:0] radicand, root, remainder, trial;
        begin
            radicand=64'(dimension)<<32; root=0; remainder=0; trial=0;
            for (integer i=31;i>=0;i=i-1) begin
                remainder=(remainder<<2)|((radicand>>(2*i)) & 3);
                trial=(root<<2)|1;
                if (remainder>=trial) begin remainder=remainder-trial; root=(root<<1)|1; end
                else root=root<<1;
            end
            sqrt_scale=root;
        end
    endfunction

    logic [255:0] next_out_attn_output;
    always_comb begin
        logic signed [127:0] x[0:3][0:3], score[0:3], w[0:3], p[0:3];
        logic signed [127:0] dot, maximum, total, acc, result;
        next_out_attn_output='0; dot=0; maximum=0; total=0; acc=0; result=0;
        for (integer s=0;s<4;s=s+1)
            for (integer d=0;d<4;d=d+1) x[s][d]=128'($signed(in_hidden_states[16*(4*s+d)+:16]));
        for (integer k=0;k<4;k=k+1) begin score[k]=0;w[k]=0;p[k]=0;end
        for (integer h=0;h<2;h=h+1) begin
            for (integer q=0;q<4;q=q+1) begin
                for (integer k=0;k<4;k=k+1) begin
                    dot=x[q][2*h]*x[k][2*h]+x[q][2*h+1]*x[k][2*h+1];
                    score[k]=dot*128'sd46341/128'sd65536+128'($signed(in_attention_mask[4*(4*q+k)+:4]))*128'sd65536;
                end
                maximum=score[0];total=0;
                for (integer k=1;k<4;k=k+1) if (score[k]>maximum) maximum=score[k];
                for (integer k=0;k<4;k=k+1) begin w[k]=exp_weight(maximum-score[k]);total=total+w[k];end
                for (integer k=0;k<4;k=k+1) p[k]=w[k]*128'sd65536/total;
                for (integer d=0;d<2;d=d+1) begin
                    acc=0;
                    for (integer k=0;k<4;k=k+1) acc=acc+p[k]*x[k][2*h+d];
                    result=acc/128'sd65536;
                    next_out_attn_output[16*(4*q+2*h+d)+:16]=16'(result);
                end
            end
        end
    end

    assign in_ready = !out_valid || out_ready;
    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin out_valid <= 0; out_attn_output <= 0; end
        else if (in_ready) begin
            out_valid <= in_valid;
            if (in_valid) begin out_attn_output <= next_out_attn_output; end
            else begin out_attn_output <= 0; end
        end
    end
endmodule
