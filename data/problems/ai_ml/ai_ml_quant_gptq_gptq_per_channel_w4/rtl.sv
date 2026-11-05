module gptq_channel #(parameter integer N=128) (
    input logic clk, rst,
    input logic in_valid, output logic in_ready,
    input logic [15:0] in_weight,
    input logic [7:0] in_first, in_second,
    input logic in_last,
    output logic out_valid, input logic out_ready,
    output logic [3:0] out_qweight,
    output logic [15:0] out_scale, out_zero,
    output logic [6:0] out_index,
    output logic out_last
);
logic signed [15:0] weight_buffer[0:127];
logic signed [7:0] first_buffer[0:127],second_buffer[0:127];
wire signed [15:0] weights[0:127];
wire signed [7:0] first[0:127],second[0:127];
logic [7:0] used,output_count,position;
wire [8:0] count={1'b0,used}+1;
logic sending;
logic [511:0] result_codes;
logic [15:0] result_scale,result_zero;
generate for(genvar n=0;n<128;n=n+1)begin
    assign weights[n]=(n==used) ? $signed(in_weight):weight_buffer[n];
    assign first[n]=(n==used) ? $signed(in_first):first_buffer[n];
    assign second[n]=(n==used) ? $signed(in_second):second_buffer[n];
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

assign in_ready=!sending;
assign out_valid=sending;
assign out_qweight=sending ? result_codes[position*4+:4]:0;
assign out_scale=sending ? result_scale:0;
assign out_zero=sending ? result_zero:0;
assign out_index=sending ? position[6:0]:0;
assign out_last=sending && (position==output_count-1);
always_ff @(posedge clk)begin
    if(rst)begin
        used<=0;output_count<=0;position<=0;sending<=0;
        result_codes<=0;result_scale<=0;result_zero<=0;
        for(integer n=0;n<128;n=n+1)begin
            weight_buffer[n]<=0;first_buffer[n]<=0;second_buffer[n]<=0;
        end
    end else if(sending)begin
        if(out_ready)begin
            if(position==output_count-1)begin sending<=0;position<=0;end
            else position<=position+1;
        end
    end else if(in_valid)begin
        weight_buffer[used]<=in_weight;first_buffer[used]<=in_first;second_buffer[used]<=in_second;
        if(in_last || used==N-1)begin
            result_codes<=codes;result_scale<=scale;result_zero<=zero;
            output_count<=count;position<=0;sending<=1;used<=0;
        end else used<=used+1;
    end
end
endmodule
