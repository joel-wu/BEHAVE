module ai_ml_tx_attn_sdpa_mistral_sdpa_swa_attn #(
    parameter int HEAD_DIM = 4,
    parameter int NUM_ATTENTION_HEADS = 4,
    parameter int NUM_KEY_VALUE_HEADS = 2,
    parameter int SLIDING_WINDOW = 8,
    parameter int ELEM_W = 32
) (
    input  logic clk,
    input  logic rst,

    input  logic in_valid,
    output logic in_ready,
    input  logic [NUM_ATTENTION_HEADS*HEAD_DIM*ELEM_W-1:0] in_query,
    input  logic [NUM_KEY_VALUE_HEADS*HEAD_DIM*ELEM_W-1:0] in_key,
    input  logic [NUM_KEY_VALUE_HEADS*HEAD_DIM*ELEM_W-1:0] in_value,

    output logic out_valid,
    input  logic out_ready,
    output logic [NUM_ATTENTION_HEADS*HEAD_DIM*ELEM_W-1:0] out_attn
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

    integer cache_len, next_len;
    logic signed [31:0] key_cache[0:SLIDING_WINDOW-1][0:NUM_KEY_VALUE_HEADS*HEAD_DIM-1], value_cache[0:SLIDING_WINDOW-1][0:NUM_KEY_VALUE_HEADS*HEAD_DIM-1];
    logic signed [31:0] keys[0:SLIDING_WINDOW-1][0:NUM_KEY_VALUE_HEADS*HEAD_DIM-1], values[0:SLIDING_WINDOW-1][0:NUM_KEY_VALUE_HEADS*HEAD_DIM-1];
    logic [NUM_ATTENTION_HEADS*HEAD_DIM*32-1:0] next_out_attn;
    always_comb begin
        logic signed [127:0] scores[0:SLIDING_WINDOW-1], weights[0:SLIDING_WINDOW-1];
        logic signed [127:0] dot, maximum, total, value_sum, value;
        integer pos, kh;
        dot=0;maximum=0;total=0;value_sum=0;value=0;kh=0;pos=0;next_len=0;next_out_attn='0;
        for (integer t=0;t<SLIDING_WINDOW;t=t+1) begin
            scores[t]=0;weights[t]=0;
            for (integer d=0;d<NUM_KEY_VALUE_HEADS*HEAD_DIM;d=d+1) begin keys[t][d]=key_cache[t][d];values[t][d]=value_cache[t][d];end
        end
        pos=cache_len;
        if (cache_len==SLIDING_WINDOW) begin
            pos=SLIDING_WINDOW-1;
            for (integer t=0;t<SLIDING_WINDOW-1;t=t+1)
                for (integer d=0;d<NUM_KEY_VALUE_HEADS*HEAD_DIM;d=d+1) begin keys[t][d]=key_cache[t+1][d];values[t][d]=value_cache[t+1][d];end
        end
        next_len=pos+1;
        for (integer d=0;d<NUM_KEY_VALUE_HEADS*HEAD_DIM;d=d+1) begin keys[pos][d]=$signed(in_key[32*d+:32]);values[pos][d]=$signed(in_value[32*d+:32]);end
        for (integer h=0;h<NUM_ATTENTION_HEADS;h=h+1) begin
            kh=h/(NUM_ATTENTION_HEADS/NUM_KEY_VALUE_HEADS);
            for (integer t=0;t<SLIDING_WINDOW;t=t+1) begin
                scores[t]=0;weights[t]=0;
                if (t<next_len) begin
                    dot=0;
                    for (integer d=0;d<HEAD_DIM;d=d+1)
                        dot=dot+128'($signed(in_query[32*(h*HEAD_DIM+d)+:32]))*128'(keys[t][kh*HEAD_DIM+d]);
                    scores[t]=dot/$signed(128'(sqrt_scale(HEAD_DIM)));
                end
            end
            maximum=scores[0];total=0;
            for (integer t=1;t<SLIDING_WINDOW;t=t+1) if (t<next_len && scores[t]>maximum) maximum=scores[t];
            for (integer t=0;t<SLIDING_WINDOW;t=t+1) if (t<next_len) begin weights[t]=exp_weight(maximum-scores[t]);total=total+weights[t];end
            for (integer d=0;d<HEAD_DIM;d=d+1) begin
                value_sum=0;
                for (integer t=0;t<SLIDING_WINDOW;t=t+1) if (t<next_len) value_sum=value_sum+weights[t]*128'(values[t][kh*HEAD_DIM+d]);
                value=value_sum/total;
                next_out_attn[32*(h*HEAD_DIM+d)+:32]=32'(value);
            end
        end
    end
    assign in_ready=!out_valid || out_ready;
    always_ff @(posedge clk) begin
        if (rst) begin
            out_valid<=0;out_attn<='0;cache_len<=0;
            for (integer t=0;t<SLIDING_WINDOW;t=t+1)
                for (integer d=0;d<NUM_KEY_VALUE_HEADS*HEAD_DIM;d=d+1) begin key_cache[t][d]<=0;value_cache[t][d]<=0;end
        end else if (in_ready) begin
            out_valid<=in_valid;out_attn<=in_valid ? next_out_attn : '0;
            if (in_valid) begin
                cache_len<=next_len;
                for (integer t=0;t<SLIDING_WINDOW;t=t+1)
                    for (integer d=0;d<NUM_KEY_VALUE_HEADS*HEAD_DIM;d=d+1) begin key_cache[t][d]<=keys[t][d];value_cache[t][d]<=values[t][d];end
            end
        end
    end
endmodule
