module ethernet_parser (
    input logic clk, rst, vld, sof,
    input logic [31:0] data,
    input logic eof,
    output logic ack,
    output logic [15:0] field,
    output logic field_vld
);
    logic active, pending;
    logic [3:0] count;
    logic [15:0] extracted;
    assign ack=1'b1;
    always_ff @(posedge clk) begin
        if(rst) begin active<=0;pending<=0;count<=0;extracted<=0;field<=0;field_vld<=0;end
        else begin
            if(pending) begin field<=extracted;field_vld<=1;pending<=0;end
            if(vld) begin
                if(sof) begin active<=1;count<=1;field<=0;field_vld<=0;pending<=0;end
                else if(active) begin
                    if(count==1) begin extracted<=data[31:16];pending<=1;end
                    if(count<15) count<=count+1'b1;
                end
                if(eof) begin active<=0;count<=0;pending<=0;field_vld<=0;end
            end
        end
    end
endmodule
