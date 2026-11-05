module LIFObuffer (input logic Clk, init_reset, EN, clear, RW, input logic [3:0] dataIn, output logic EMPTY, FULL, output logic [3:0] dataOut);
    logic [3:0] words [0:3];
    logic [2:0] count;
    assign EMPTY=(count==0);
    assign FULL=(count==4);
    always_ff @(posedge Clk) begin
        if(init_reset) begin
            count<=0; dataOut<=0;
            for(int i=0;i<4;i++) words[i]<=0;
        end else if(EN) begin
            dataOut<=0;
            if(clear) begin
                count<=0;
                for(int i=0;i<4;i++) words[i]<=0;
            end else if(!RW && count<4) begin
                words[count]<=dataIn; count<=count+3'd1;
            end else if(RW && count>0) begin
                dataOut<=words[count-3'd1]; words[count-3'd1]<=0;
                count<=count-3'd1;
            end
        end
    end
endmodule
