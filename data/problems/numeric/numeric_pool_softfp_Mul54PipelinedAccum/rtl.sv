module Mul54PipelinedAccum (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic         in_val_s0,
    input  logic         in_latch_a_s0,
    input  logic [53:0]  in_a_s0,
    input  logic         in_latch_b_s0,
    input  logic [53:0]  in_b_s0,
    input  logic [104:0] in_c_s2,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [104:0] out_result_s3
);
logic v1,v2;
logic [53:0] a1,b1,a2,b2;
wire [107:0] product=a2*b2;
wire [104:0] result=product[104:0]+in_c_s2;
assign in_ready=!out_valid || out_ready;
always_ff @(posedge clk) begin
    if(rst) begin
        v1<=0;v2<=0;a1<=0;b1<=0;a2<=0;b2<=0;
        out_valid<=0;out_result_s3<=0;
    end else if(in_ready) begin
        out_valid<=0;out_result_s3<=0;
        if(in_valid) begin
            v1<=in_val_s0;v2<=v1;
            if(in_val_s0) begin
                if(in_latch_a_s0) a1<=in_a_s0;
                if(in_latch_b_s0) b1<=in_b_s0;
            end
            if(v1) begin a2<=a1;b2<=b1;end
            if(v2) begin out_valid<=1;out_result_s3<=result;end
        end
    end
end
endmodule
