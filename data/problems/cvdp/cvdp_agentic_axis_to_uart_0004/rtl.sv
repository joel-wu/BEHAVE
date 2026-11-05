module uart_rx_to_axis #(
    parameter int CLK_FREQ=100,
    parameter int BIT_RATE=115200,
    parameter int BIT_PER_WORD=8,
    parameter int PARITY_BIT=0,
    parameter int STOP_BITS_NUM=1
) (
    input logic aclk, aresetn, RX,
    output logic [BIT_PER_WORD-1:0] tdata,
    output logic tuser, tvalid
);
    localparam int PERIOD=(64'(CLK_FREQ)*1000000)/BIT_RATE;
    localparam int IDLE=0, START=1, DATA=2, PARITY=3, STOP=4, OUT_RDY=6;
    logic [2:0] phase;
    logic previous, parity, bad_parity, bad_stop;
    logic [BIT_PER_WORD-1:0] word;
    integer timer, index, stops;
    assign tvalid=(phase==OUT_RDY);
    always_ff @(posedge aclk or negedge aresetn) begin
        if(!aresetn) begin
            phase<=IDLE;previous<=1;timer<=0;index<=0;word<=0;parity<=0;
            bad_parity<=0;bad_stop<=0;stops<=0;tdata<=0;tuser<=0;
        end else begin
            previous<=RX;
            if(phase==IDLE) begin
                if(previous && !RX) begin
                    phase<=START;timer<=PERIOD/2-1;word<=0;index<=0;
                    parity<=0;bad_parity<=0;bad_stop<=0;stops<=0;
                end
            end else if(phase==OUT_RDY) phase<=IDLE;
            else if(timer!=0) timer<=timer-1;
            else begin
                timer<=PERIOD-1;
                case(phase)
                    START: phase<=RX ? IDLE : DATA;
                    DATA: begin
                        word[index]<=RX;parity<=parity ^ RX;
                        if(index==BIT_PER_WORD-1) phase<=PARITY_BIT ? PARITY : STOP;
                        else index<=index+1;
                    end
                    PARITY: begin bad_parity<=(RX != (parity ^ (PARITY_BIT==1)));phase<=STOP;end
                    STOP: begin
                        bad_stop<=bad_stop | !RX;stops<=stops+1;
                        if(stops==STOP_BITS_NUM-1) begin
                            if(!(bad_stop | !RX)) begin tdata<=word;tuser<=bad_parity;phase<=OUT_RDY;end
                            else phase<=IDLE;
                        end
                    end
                    default: phase<=IDLE;
                endcase
            end
        end
    end
endmodule
