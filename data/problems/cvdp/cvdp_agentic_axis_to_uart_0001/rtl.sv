module axis_to_uart_tx #(
    parameter int CLK_FREQ=100,
    parameter int BIT_RATE=115200,
    parameter int BIT_PER_WORD=8,
    parameter int PARITY_BIT=0,
    parameter int STOP_BITS_NUM=1
) (
    input logic aclk, aresetn,
    input logic [BIT_PER_WORD-1:0] tdata,
    input logic tvalid,
    output logic tready, TX
);
    localparam int PERIOD=(64'(CLK_FREQ)*1000000)/BIT_RATE;
    localparam int EXTRA=(PARITY_BIT!=0), LENGTH=1+BIT_PER_WORD+EXTRA+STOP_BITS_NUM;
    logic [LENGTH-1:0] frame, incoming;
    integer remaining, timer;
    assign tready=(remaining==0);
    assign TX=(remaining!=0) ? frame[0] : 1'b1;
    always_comb begin
        incoming='0;
        incoming[BIT_PER_WORD:1]=tdata;
        if(EXTRA) incoming[1+BIT_PER_WORD]=(^tdata) ^ (PARITY_BIT==1);
        for(int i=0;i<STOP_BITS_NUM;i++) incoming[1+BIT_PER_WORD+EXTRA+i]=1'b1;
    end
    always_ff @(posedge aclk or negedge aresetn) begin
        if(!aresetn) begin frame<=0;remaining<=0;timer<=0;end
        else if(remaining==0) begin
            if(tvalid) begin frame<=incoming;remaining<=LENGTH;timer<=PERIOD-1;end
        end else if(timer!=0) timer<=timer-1;
        else begin frame<=frame>>1;remaining<=remaining-1;timer<=PERIOD-1;end
    end
endmodule
