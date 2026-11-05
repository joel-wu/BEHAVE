module signal_processing_cnn_softmax_arm_softmax_u8 (
    input logic clk,rst,in_valid,
    output logic in_ready,
    input logic [31:0] in_input,in_mult,in_shift,in_diff_min,
    output logic out_valid,
    input logic out_ready,
    output logic [31:0] out_output,
    output logic out_error
);
    function automatic logic signed [31:0] mul_q31(input logic signed [31:0] a,b);
        logic signed [63:0] n;
        begin
            n=a; n=n*b;
            n=n+(((a<0)==(b<0)) ? 64'sd1073741824 : -64'sd1073741823);
            mul_q31=n/64'sd2147483648;
            if (a==32'sh80000000 && b==32'sh80000000) mul_q31=32'sh7fffffff;
        end
    endfunction
    function automatic logic signed [31:0] div_round(input logic signed [31:0] x,input integer n);
        logic signed [31:0] q,mask,remainder,threshold;
        begin
            mask=(32'd1<<n)-1;
            q=x>>>n; remainder=x&mask;
            threshold=(mask>>>1)+(q<0 ? 1 : 0);
            div_round=q+(remainder>threshold ? 1 : 0);
        end
    endfunction
    function automatic logic signed [31:0] mul_pow2(input logic signed [31:0] x,input integer n);
        logic signed [31:0] threshold;
        begin
            threshold=(32'd1<<(31-n))-1;
            if(x>threshold) mul_pow2=32'sh7fffffff;
            else if(x < -threshold) mul_pow2=32'sh80000000;
            else mul_pow2=x<<<n;
        end
    endfunction
    function automatic logic signed [31:0] exp_neg(input logic signed [31:0] v);
        logic signed [31:0] a,remainder,x,x2,poly,result,constant;
        begin
            a=(v&32'sh00ffffff)-32'sd16777216; remainder=a-v;
            x=(a<<<5)+32'sd268435456; x2=mul_q31(x,x);
            poly=div_round(mul_q31(div_round(mul_q31(x2,x2),2)+mul_q31(x2,x),32'sd715827883)+x2,1);
            result=32'sd1895147668+mul_q31(32'sd1895147668,x+poly);
            for(integer j=0;j<7;j=j+1) begin
                case(j)
                    0:constant=1672461947;1:constant=1302514674;2:constant=790015084;
                    3:constant=290630308;4:constant=39332535;5:constant=720401;
                    default:constant=242;
                endcase
                if(remainder&(32'd1<<(24+j))) result=mul_q31(result,constant);
            end
            exp_neg=v==0 ? 32'sh7fffffff : result;
        end
    endfunction
    function automatic logic signed [31:0] reciprocal(input logic signed [31:0] v);
        logic signed [63:0] half_wide;
        logic signed [31:0] half,x;
        begin
            half_wide=v; half_wide=(half_wide+64'sd2147483648)/2;
            half=half_wide;
            x=32'sd1515870810+mul_q31(half,-32'sd1010580540);
            for(integer j=0;j<3;j=j+1)
                x=x+mul_pow2(mul_q31(x,32'sd536870912-mul_q31(half,x)),2);
            reciprocal=mul_pow2(x,1);
        end
    endfunction
    logic occupied,bad;
    logic [31:0] result;
    logic signed [31:0] mult,shift,cutoff,d[0:3],ex[0:3],total,scale,y;
    logic signed [63:0] scaled;
    integer maximum,headroom,n;
    assign in_ready=!occupied || out_ready;
    assign out_valid=occupied;
    always_comb begin
        mult=$signed(in_mult);shift=$signed(in_shift);cutoff=$signed(in_diff_min);
        bad=mult<0 || shift<0 || shift>30 || cutoff>0;
        maximum=0;
        for(integer j=0;j<4;j=j+1)
            if(in_input[8*j+:8]>maximum) maximum=in_input[8*j+:8];
        total=0;result=0;scaled=0;scale=0;y=0;headroom=0;n=0;
        for(integer j=0;j<4;j=j+1) begin
            d[j]=int'(in_input[8*j+:8])-maximum;ex[j]=0;
            if(!bad && d[j]>=cutoff) begin
                scaled=d[j];scaled=scaled<<<shift;
                if(scaled < -64'sd2147483648) bad=1;
                else ex[j]=exp_neg(mul_q31(scaled[31:0],mult));
            end
            total=total+div_round(ex[j],12);
        end
        if(!bad) begin
            // total is positive: at least one maximum has exp(0)=Q31_MAX.
            for(integer j=0;j<31;j=j+1)
                if(total < (32'd1<<(31-j))) headroom=j+1;
            scale=reciprocal((total<<headroom)-32'sh80000000);n=35-headroom;
            for(integer j=0;j<4;j=j+1) begin
                y=d[j]>=cutoff ? div_round(mul_q31(scale,ex[j]),n) : 0;
                result[8*j+:8]=y<0 ? 8'd0 : y>255 ? 8'd255 : y[7:0];
            end
        end
        if(bad) result=0;
    end
    always_ff @(posedge clk) begin
        if(rst) begin occupied<=0;out_output<=0;out_error<=0;end
        else if(in_valid && in_ready) begin occupied<=1;out_output<=result;out_error<=bad;end
        else if(out_ready) occupied<=0;
    end
endmodule
