module gptq_group128 (
    input logic clk, rst,
    input logic in_valid, output logic in_ready,
    input logic [2047:0] in_weights,
    input logic [1015:0] in_first,
    input logic [1007:0] in_second,
    output logic out_valid, input logic out_ready,
    output logic [15:0] out_scale, out_zero,
    output logic [511:0] out_codes
);
wire signed [15:0] weights[0:127];
wire signed [7:0] first[0:127],second[0:127];
wire [8:0] count=128;
generate for(genvar n=0;n<128;n=n+1)begin
    assign weights[n]=in_weights[n*16+:16];
    if(n<127)assign first[n]=in_first[n*8+:8];else assign first[n]=0;
    if(n<126)assign second[n]=in_second[n*8+:8];else assign second[n]=0;
end endgenerate
function automatic signed [63:0] nearest_even(input logic signed [63:0] x, divisor);
    logic signed [63:0] magnitude,q,r;
    begin
        magnitude=x<0 ? -x:x;q=magnitude/divisor;r=magnitude-q*divisor;
        if(2*r>divisor || (2*r==divisor && q[0]))q=q+1;
        nearest_even=x<0 ? -q:q;
    end
endfunction
integer i,low,high,scale,zero,code;
logic signed [63:0] corrected[0:127];
logic signed [63:0] error;
logic [511:0] codes;
always_comb begin
    low=0;high=0;scale=1;zero=0;code=0;error=0;codes=0;
    for(i=0;i<128;i=i+1)begin
        corrected[i]=64'(weights[i])*65536;
        if(i<count)begin
            if(weights[i]<low)low=weights[i];
            if(weights[i]>high)high=weights[i];
        end
    end
    scale=(high-low+14)/15;if(scale==0)scale=1;zero=low+8*scale;
    for(i=0;i<128;i=i+1)if(i<count)begin
        code=nearest_even(corrected[i]-64'(low)*65536,64'(scale)*65536);
        if(code<0)code=0;else if(code>15)code=15;
        codes[i*4+:4]=code-8;
        error=corrected[i]-64'(low+code*scale)*65536;
        if(i+1<count)corrected[i+1]=corrected[i+1]-nearest_even(error*64'(first[i]),256);
        if(i+2<count)corrected[i+2]=corrected[i+2]-nearest_even(error*64'(second[i]),256);
    end
end

assign in_ready=!out_valid || out_ready;
always_ff @(posedge clk)begin
    if(rst)begin out_valid<=0;out_scale<=0;out_zero<=0;out_codes<=0;end
    else if(in_ready)begin
        out_valid<=in_valid;out_scale<=in_valid ? scale:0;
        out_zero<=in_valid ? zero:0;out_codes<=in_valid ? codes:0;
    end
end
endmodule
