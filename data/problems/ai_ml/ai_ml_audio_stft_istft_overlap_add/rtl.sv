module ai_ml_audio_stft_istft_overlap_add (
    input logic clk,rst,in_valid,
    output logic in_ready,
    input logic in_start,
    input logic [31:0] in_bin0_re,
    input logic [31:0] in_bin0_im,
    input logic [31:0] in_bin1_re,
    input logic [31:0] in_bin1_im,
    input logic [31:0] in_bin2_re,
    input logic [31:0] in_bin2_im,
    input logic [31:0] in_bin3_re,
    input logic [31:0] in_bin3_im,
    input logic [31:0] in_bin4_re,
    input logic [31:0] in_bin4_im,
    input logic out_ready,
    output logic out_valid,
    output logic [31:0] out_sample_idx, out_sample,
    output logic out_clipped
);
    function automatic signed [127:0] round_even(input logic signed [127:0] v,d);
        logic signed [127:0] mag,q,rem;
        begin
            mag=v<0 ? -v : v;q=mag/d;rem=mag-q*d;
            if(rem*2>d || (rem*2==d && q[0])) q=q+1;
            round_even=v<0 ? -q : q;
        end
    endfunction
    function automatic signed [63:0] cosine(input integer i);
        case(i)
            0: cosine=64'sd1073741824;
            1: cosine=64'sd759250125;
            2: cosine=64'sd0;
            3: cosine=-64'sd759250125;
            4: cosine=-64'sd1073741824;
            5: cosine=-64'sd759250125;
            6: cosine=64'sd0;
            7: cosine=64'sd759250125;
            default:cosine=0;
        endcase
    endfunction
    function automatic signed [63:0] window(input integer i);
        case(i)
            0: window=64'sd0;
            1: window=64'sd157245850;
            2: window=64'sd536870912;
            3: window=64'sd916495974;
            4: window=64'sd1073741824;
            5: window=64'sd916495974;
            6: window=64'sd536870912;
            7: window=64'sd157245850;
            default:window=0;
        endcase
    endfunction
    logic signed [63:0] re[0:4],im[0:4],weighted[0:7],prev_tail[0:3];
    logic signed [127:0] total,sample,denominator,value;
    logic [31:0] next_samples[0:3], samples[0:3];
    logic next_clipped[0:3],clipped[0:3];
    logic have_tail,busy;
    logic [1:0] beat;
    logic [31:0] sample_index,buffer_index;
    integer n,k,i;
    always_comb begin
        re[0]={{32{in_bin0_re[31]}},in_bin0_re};im[0]={{32{in_bin0_im[31]}},in_bin0_im};
        re[1]={{32{in_bin1_re[31]}},in_bin1_re};im[1]={{32{in_bin1_im[31]}},in_bin1_im};
        re[2]={{32{in_bin2_re[31]}},in_bin2_re};im[2]={{32{in_bin2_im[31]}},in_bin2_im};
        re[3]={{32{in_bin3_re[31]}},in_bin3_re};im[3]={{32{in_bin3_im[31]}},in_bin3_im};
        re[4]={{32{in_bin4_re[31]}},in_bin4_re};im[4]={{32{in_bin4_im[31]}},in_bin4_im};

        total=0;sample=0;denominator=0;value=0;
        for(n=0;n<8;n=n+1) begin
            total=128'(re[0])*128'sd1073741824+128'(re[4])*128'(cosine((4*n)%8));
            for(k=1;k<4;k=k+1) total=total+2*(128'(re[k])*128'(cosine((k*n)%8))-128'(im[k])*128'(cosine((10-k*n%8)%8)));
            sample=round_even(total,128'sd8589934592);
            weighted[n]=64'(round_even(sample*128'(window(n)),128'sd1073741824));
        end
        for(i=0;i<4;i=i+1) begin
            denominator=128'(window(i))*128'(window(i))+128'(window(i+4))*128'(window(i+4));
            value=round_even((128'(prev_tail[i])+128'(weighted[i]))*128'sd1152921504606846976,denominator);
            next_clipped[i]=0;
            if(value>128'sd2147483647) begin next_samples[i]=32'h7fffffff;next_clipped[i]=1;end
            else if(value< -128'sd2147483648) begin next_samples[i]=32'h80000000;next_clipped[i]=1;end
            else next_samples[i]=value[31:0];
        end
    end
    assign in_ready=!busy || (out_ready && beat==3);
    assign out_valid=busy;
    assign out_sample=busy ? samples[beat] : 0;
    assign out_sample_idx=busy ? buffer_index+{30'd0,beat} : 0;
    assign out_clipped=busy ? clipped[beat] : 0;
    integer j;
    always_ff @(posedge clk) begin
        if(rst) begin
            have_tail<=0;busy<=0;beat<=0;sample_index<=0;buffer_index<=0;
            for(j=0;j<4;j=j+1) begin prev_tail[j]<=0;samples[j]<=0;clipped[j]<=0;end
        end else begin
            if(busy && out_ready) begin
                if(beat==3) begin busy<=0;beat<=0;end
                else beat<=beat+1;
            end
            if(in_valid && in_ready) begin
                for(j=0;j<4;j=j+1) prev_tail[j]<=weighted[j+4];
                have_tail<=1;
                if(in_start || !have_tail) begin sample_index<=0;busy<=0;beat<=0;end
                else begin
                    busy<=1;beat<=0;buffer_index<=sample_index;sample_index<=sample_index+4;
                    for(j=0;j<4;j=j+1) begin samples[j]<=next_samples[j];clipped[j]<=next_clipped[j];end
                end
            end
        end
    end
endmodule
