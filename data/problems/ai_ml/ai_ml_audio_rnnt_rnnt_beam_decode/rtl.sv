module ai_ml_audio_rnnt_rnnt_beam_decode (
    input logic clk,rst,in_valid,
    output logic in_ready,
    input logic [3:0] in_beam_width,
    input logic [3:0] in_vocab_size,
    input logic [2:0] in_blank,
    input logic [3:0] in_num_hypos,
    input logic [2047:0] in_tokens_flat,
    input logic [47:0] in_lengths,
    input logic [255:0] in_scores_flat,
    input logic [2047:0] in_probs_flat,
    input logic out_ready,
    output logic out_valid,
    output logic [3:0] out_a_count,
    output logic [2047:0] out_a_tokens,
    output logic [47:0] out_a_lengths,
    output logic [511:0] out_a_scores,
    output logic [3:0] out_b_count,
    output logic [2047:0] out_b_tokens,
    output logic [47:0] out_b_lengths,
    output logic [511:0] out_b_scores,
    output logic [0:0] out_error
);
    localparam logic [63:0] Q = 64'd1099511627776;
    localparam logic signed [63:0] LN2 = 64'sd762123384786;

    function automatic logic [63:0] round_div(input logic [95:0] n, input logic [63:0] d);
        logic [95:0] quotient, remainder, denominator;
        begin
            denominator = {32'd0,d};
            quotient = n / denominator;
            remainder = n - quotient*denominator;
            if ((remainder << 1) > denominator ||
                ((remainder << 1) == denominator && quotient[0]))
                quotient = quotient + 96'd1;
            round_div = quotient[63:0];
        end
    endfunction

    function automatic logic signed [31:0] qsat(input logic signed [63:0] value);
        begin
            if (value > 64'sd2147483647)
                qsat = 32'sh7fffffff;
            else if (value < -64'sd2147483648)
                qsat = 32'sh80000000;
            else
                qsat = value[31:0];
        end
    endfunction





    function automatic logic [63:0] exp40(input logic signed [63:0] value);
        logic signed [63:0] wide;
        integer exponent;
        logic [63:0] residual, term, total;
        begin
            wide = 0;
            exponent = 0;
            residual = 0;
            term = Q;
            total = Q;
            if (value <= -64'sd2097152) begin
                exp40 = 0;
            end else begin
                wide = value * 64'sd16777216;
                exponent = int'(wide / LN2);
                if (wide < 0 && wide % LN2 != 0)
                    exponent = exponent - 1;
                residual = $unsigned(wide - exponent*LN2);
                for (int degree = 1; degree <= 12; degree++) begin
                    term = round_div({32'd0,term} * {32'd0,residual}, Q * 64'(degree));
                    total = total + term;
                end
                if (exponent >= 0)
                    exp40 = total << exponent;
                else
                    exp40 = round_div({32'd0,total},64'd1 << (-exponent));
            end
        end
    endfunction



    function automatic logic signed [31:0] softplus16(input logic signed [31:0] value);
        logic signed [63:0] extended;
        logic [63:0] magnitude, tail_value, z, square, term, total, correction;
        begin
            extended = $signed({{32{value[31]}},value});
            magnitude = extended < 0 ? $unsigned(-extended) : $unsigned(extended);
            tail_value = exp40(-$signed(magnitude));
            z = round_div({32'd0,tail_value} * {32'd0,Q}, 2*Q+tail_value);
            square = round_div({32'd0,z} * {32'd0,z}, Q);
            term = z;
            total = z;
            for (int index = 1; index < 12; index++) begin
                term = round_div({32'd0,term} * {32'd0,square}, Q);
                total = total + round_div({32'd0,term},64'(2*index+1));
            end
            correction = round_div({32'd0,total} << 1,64'd16777216);
            softplus16 = qsat((extended > 0 ? extended : 64'sd0) + $signed(correction));
        end
    endfunction




    function automatic signed [63:0] logadd(input logic signed [63:0] a,b);
        logic signed [63:0] difference,correction;
        begin
            difference=a>=b ? a-b : b-a;
            correction=difference>=64'sd1048576 ? 0 : 64'(softplus16(-$signed(difference[31:0])));
            logadd=(a>=b ? a : b)+correction;
        end
    endfunction
    logic [255:0] keys[0:7],bkeys[0:7],swapkey;
    integer lengths[0:7],blens[0:7],swaplen;
    logic signed [63:0] scores[0:7],bscores[0:7],candidates[0:55],p,score,threshold,bestscore,swapscore;
    logic live[0:55],bad;
    integer count,h,j,i,token,slot,found,bc,ac,best,rank,parent,length;
    logic [3:0] next_out_a_count;
    logic [2047:0] next_out_a_tokens;
    logic [47:0] next_out_a_lengths;
    logic [511:0] next_out_a_scores;
    logic [3:0] next_out_b_count;
    logic [2047:0] next_out_b_tokens;
    logic [47:0] next_out_b_lengths;
    logic [511:0] next_out_b_scores;
    logic [0:0] next_out_error;

    always_comb begin
        bad=in_beam_width<1 || in_beam_width>8 || in_vocab_size<1 || in_vocab_size>8 || in_num_hypos>8 || {1'b0,in_blank}!=in_vocab_size-1;
        count=in_num_hypos==0 ? 1 : (in_num_hypos>8 ? 8 : int'(in_num_hypos));
        p=0;score=0;threshold=0;bestscore=0;swapscore=0;swapkey=0;swaplen=0;found= -1;best= -1;parent=0;length=0;slot=0;
        bc=0;ac=0;
        next_out_a_count=0;
        next_out_a_tokens=0;
        next_out_a_lengths=0;
        next_out_a_scores=0;
        next_out_b_count=0;
        next_out_b_tokens=0;
        next_out_b_lengths=0;
        next_out_b_scores=0;
        next_out_error=0;

        for(h=0;h<8;h=h+1) begin
            keys[h]=0;lengths[h]=0;scores[h]=0;bkeys[h]=0;blens[h]=0;bscores[h]=0;
            if(h<count) begin
                if(in_num_hypos==0) begin lengths[h]=1;keys[h]={253'd0,in_blank};end
                else begin
                    length=int'(in_lengths[6*h+:6]);
                    if(length>31) begin bad=1;length=31;end
                    lengths[h]=length;keys[h]=in_tokens_flat[256*h+:256]&((256'd1<<(8*length))-1);
                    scores[h]=64'($signed(in_scores_flat[32*h+:32]));
                end
            end
        end
        for(h=0;h<8;h=h+1) if(h<count) begin
            found= -1;
            for(j=0;j<8;j=j+1) if(j<bc && bkeys[j]==keys[h] && blens[j]==lengths[h]) found=j;
            p=64'($signed(in_probs_flat[32*(8*h+int'(in_blank))+:32]));score=scores[h]+p;
            if(found>=0) bscores[found]=logadd(bscores[found],score);
            else begin bkeys[bc]=keys[h];blens[bc]=lengths[h];bscores[bc]=score;bc=bc+1;end
        end
        for(i=0;i<8;i=i+1) for(j=0;j<7;j=j+1) if(j+1<bc && bscores[j]>bscores[j+1]) begin
            swapscore=bscores[j];bscores[j]=bscores[j+1];bscores[j+1]=swapscore;
            swapkey=bkeys[j];bkeys[j]=bkeys[j+1];bkeys[j+1]=swapkey;
            swaplen=blens[j];blens[j]=blens[j+1];blens[j+1]=swaplen;
        end
        for(h=0;h<8;h=h+1) for(token=0;token<7;token=token+1) begin
            slot=h*7+token;candidates[slot]=0;live[slot]=0;
            if(h<count && token<int'(in_vocab_size)-1) begin
                candidates[slot]=scores[h]+64'($signed(in_probs_flat[32*(8*h+token)+:32]));live[slot]=1;
            end
        end
        if(bc>=int'(in_beam_width) && in_beam_width>=1 && in_beam_width<=8) threshold=bscores[bc-int'(in_beam_width)];
        for(rank=0;rank<8;rank=rank+1) begin
            best= -1;bestscore=0;
            for(slot=0;slot<56;slot=slot+1) if(live[slot] && (best<0 || candidates[slot]>bestscore)) begin best=slot;bestscore=candidates[slot];end
            if(best>=0) begin
                live[best]=0;
                if(rank<int'(in_beam_width) && (bc<int'(in_beam_width) || bestscore>threshold)) begin
                    parent=best/7;token=best%7;length=lengths[parent];
                    next_out_a_tokens[256*ac+:256]=keys[parent]|(256'(token)<<(8*length));
                    next_out_a_lengths[6*ac+:6]=6'(length+1);next_out_a_scores[64*ac+:64]=bestscore;ac=ac+1;
                end
            end
        end
        for(j=0;j<8;j=j+1) if(j<bc) begin
            next_out_b_tokens[256*j+:256]=bkeys[j];next_out_b_lengths[6*j+:6]=6'(blens[j]);next_out_b_scores[64*j+:64]=bscores[j];
        end
        next_out_a_count=4'(ac);next_out_b_count=4'(bc);
        if(bad) begin
            next_out_a_count=0;
            next_out_a_tokens=0;
            next_out_a_lengths=0;
            next_out_a_scores=0;
            next_out_b_count=0;
            next_out_b_tokens=0;
            next_out_b_lengths=0;
            next_out_b_scores=0;
            next_out_error=1;
        end
    end

    assign in_ready = !out_valid || out_ready;
    always_ff @(posedge clk) begin
        if (rst) begin out_valid <= 0; out_a_count <= 0; out_a_tokens <= 0; out_a_lengths <= 0; out_a_scores <= 0; out_b_count <= 0; out_b_tokens <= 0; out_b_lengths <= 0; out_b_scores <= 0; out_error <= 0; end
        else if (in_ready) begin
            out_valid <= in_valid;
            if (in_valid) begin out_a_count <= next_out_a_count; out_a_tokens <= next_out_a_tokens; out_a_lengths <= next_out_a_lengths; out_a_scores <= next_out_a_scores; out_b_count <= next_out_b_count; out_b_tokens <= next_out_b_tokens; out_b_lengths <= next_out_b_lengths; out_b_scores <= next_out_b_scores; out_error <= next_out_error; end
            else begin out_a_count <= 0; out_a_tokens <= 0; out_a_lengths <= 0; out_a_scores <= 0; out_b_count <= 0; out_b_tokens <= 0; out_b_lengths <= 0; out_b_scores <= 0; out_error <= 0; end
        end
    end
endmodule
